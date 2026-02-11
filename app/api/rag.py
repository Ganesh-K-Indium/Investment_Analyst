"""
RAG endpoints (ask and compare) with portfolio integration and chat persistence
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List
from sqlalchemy.orm import Session
from langchain_core.messages import HumanMessage
from app.database.connection import get_db_session
from app.services.portfolio import PortfolioService
from app.services.chat import ChatService
from app.database.models import AgentType, MessageRole
from app.services.vectordb_manager import get_vectordb_manager
from app.utils.company_mapping import get_ticker
import uuid
import json
import datetime
import os

router = APIRouter(tags=["RAG"])


# Pydantic Models
class AskInput(BaseModel):
    query: str = Field(..., description="User query")
    thread_id: str = Field(..., description="Session thread_id (required for portfolio context)")
    ticker: Optional[str] = Field(None, description="Specific ticker symbol to target (optional)")


class CompareInput(BaseModel):
    company1: str = Field(..., description="First company to compare")
    company2: str = Field(..., description="Second company to compare")
    company3: Optional[str] = Field(None, description="Optional third company")
    user_id: str = Field(..., description="User identifier")
    thread_id: Optional[str] = Field(None, description="Optional thread_id for conversation continuity")


class HealthStatusResponse(BaseModel):
    status: str
    agent_initialized: bool
    cache_initialized: bool
    timestamp: str


class CapabilitiesResponse(BaseModel):
    document_qa: List[str]
    company_comparison: List[str]
    data_sources: List[str]
    intelligent_features: List[str]


# Global references (set by main app)
agent = None
semantic_cache = None


def set_agent(agent_instance):
    """Set the global agent instance"""
    global agent
    agent = agent_instance


def set_semantic_cache(cache_instance):
    """Set the global semantic cache instance"""
    global semantic_cache
    semantic_cache = cache_instance


@router.post("/ask")
async def ask_agent(
    payload: AskInput,
    db: Session = Depends(get_db_session)
):
    """
    Handle RAG queries with portfolio-based filtering and chat persistence.
    Uses ticker-based vector collections.
    """
    try:
        if not agent:
            raise HTTPException(status_code=503, detail="Agent not initialized")
        
        query = payload.query
        thread_id = payload.thread_id
        ticker = payload.ticker
        
        # Get session and associated portfolio
        session = PortfolioService.get_session(db, thread_id)
        if not session:
            raise HTTPException(
                status_code=404,
                detail=f"Session not found. Please create a portfolio session first."
            )
        
        portfolio = session.portfolio
        
        # Create or get chat session for persistence
        chat_session = ChatService.create_or_get_chat_session(
            db=db,
            session_id=thread_id,
            user_id=session.user_id,
            agent_type=AgentType.RAG,
            portfolio_id=portfolio.id,
            title=f"RAG: {portfolio.name}"
        )
        
        # Save user message
        ChatService.add_message(
            db=db,
            session_id=thread_id,
            role=MessageRole.USER,
            content=query
        )
        
        # Register session with VectorDBManager (for context tracking)
        vectordb_mgr = get_vectordb_manager()
        vectordb_mgr.register_session(thread_id, portfolio.id)
        
        # Map portfolio companies to tickers for the filter
        # This helps the agent know which tickers are valid for this portfolio
        company_tickers = []
        for company in portfolio.company_names:
            t = get_ticker(company)
            if t:
                company_tickers.append(t)
            else:
                # Fallback to company name if no ticker found
                company_tickers.append(company)
                
        print(f"Using portfolio-scoped context")
        print(f"   Portfolio: {portfolio.name}")
        print(f"   Tickers: {company_tickers}")
        if ticker:
            print(f"   Target Ticker: {ticker}")
        
        config = {"configurable": {"thread_id": thread_id}}
        
        # Check semantic cache
        if semantic_cache:
            start_time = datetime.datetime.now()
            # Include ticker in cache key if present?
            # Creating a composite key or just appending to query might be better
            cache_query = f"{ticker}:{query}" if ticker else query
            cached_data = semantic_cache.lookup(cache_query, thread_id=thread_id)
            if cached_data:
                print(f"Returning cached response for: {cache_query}")
                response = cached_data.get("response")
                response["thread_id"] = thread_id
                elapsed = (datetime.datetime.now() - start_time).total_seconds()
                print(f"Cache response time: {elapsed:.4f}s")
                return response
        
        # Check for interrupted state (HITL)
        current_state = await agent.aget_state(config)
        is_interrupted = bool(current_state.next)
        
        if is_interrupted:
            print(f"Resuming from interrupt for thread {thread_id}")
            await agent.aupdate_state(config, {"user_clarification": query})
            result = await agent.ainvoke(None, config)
        else:
            # Standard execution
            inputs = {
                "messages": [HumanMessage(content=query)],
                "vectorstore_searched": False,
                "web_searched": False,
                "vectorstore_quality": "none",
                "needs_web_fallback": False,
                "retry_count": 0,
                "tool_calls": [],
                "document_sources": {},
                "citation_info": [],
                "summary_strategy": "single_source",
                "company_filter": company_tickers,  # Pass valid tickers for this portfolio
                "ticker": ticker,  # Specific ticker if provided
                "sub_query_analysis": {},
                "sub_query_results": {}
            }
            result = await agent.ainvoke(inputs, config)
        
        # Extract answer
        if result.get("clarification_needed") and result.get("clarification_request"):
            answer = result["clarification_request"]
            print(f"HITL Interrupt: Asking user -> {answer}")
        else:
            answer = result["messages"][-1].content
        
        # Save assistant message with metadata
        ChatService.add_message(
            db=db,
            session_id=thread_id,
            role=MessageRole.ASSISTANT,
            content=answer,
            metadata={
                "portfolio_id": portfolio.id,
                "portfolio_name": portfolio.name,
                "company_filter": company_tickers,
                "ticker": ticker,
                "vectorstore_searched": result.get("vectorstore_searched", False),
                "web_searched": result.get("web_searched", False),
                "document_count": len(result.get("documents", [])),
                "sources": [doc.metadata.get("source_file", "Unknown") for doc in result.get("documents", [])][:5]
            }
        )
        
        print(f"Query: {query}")
        print(f"Thread ID: {thread_id}")
        print(f"Answer: {answer[:200]}...")
        print(f"Chat persisted to database")
        
        # Prepare response
        response_data = {
            "answer": answer,
            "thread_id": thread_id,
            "portfolio_id": portfolio.id,
            "portfolio_name": portfolio.name,
            "company_filter": company_tickers,
            "ticker": ticker,
            "messages": [
                {
                    "type": msg.__class__.__name__,
                    "content": msg.content if hasattr(msg, 'content') else str(msg)
                }
                for msg in result.get("messages", [])
            ],
            "intermediate_message": result.get("Intermediate_message", ""),
            "documents": [
                {
                    "content": doc.page_content if hasattr(doc, 'page_content') else str(doc),
                    "metadata": doc.metadata if hasattr(doc, 'metadata') else {}
                }
                for doc in result.get("documents", [])
            ],
            "vectorstore_searched": result.get("vectorstore_searched", False),
            "web_searched": result.get("web_searched", False),
            "vectorstore_quality": result.get("vectorstore_quality", "none"),
            "needs_web_fallback": result.get("needs_web_fallback", False),
            "retry_count": result.get("retry_count", 0),
            "tool_calls": result.get("tool_calls", []),
            "document_sources": result.get("document_sources", {}),
            "citation_info": result.get("citation_info", []),
            "summary_strategy": result.get("summary_strategy", "single_source"),
            "sub_query_analysis": result.get("sub_query_analysis", {}),
            "sub_query_results": result.get("sub_query_results", {})
        }
        
        # Save response to output/json directory
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        json_dir = "output/json"
        os.makedirs(json_dir, exist_ok=True)
        json_path = os.path.join(json_dir, f"{timestamp}.json")
        with open(json_path, 'w') as f:
            json.dump(response_data, f, indent=4)
        
        print(f"Response saved to: {json_path}")
        
        # Update cache
        if semantic_cache:
            cache_query = f"{ticker}:{query}" if ticker else query
            semantic_cache.update(cache_query, response_data, thread_id=thread_id)
        
        return response_data
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/compare")
async def compare_companies(
    payload: CompareInput,
    db: Session = Depends(get_db_session)
):
    """
    Handle company comparison queries with chat persistence.
    Creates a TEMPORARY Vector DB instance with specified companies.
    Does NOT affect portfolio-scoped DB instances.
    """
    try:
        if not agent:
            raise HTTPException(status_code=503, detail="Agent not initialized")
        
        company1 = payload.company1
        company2 = payload.company2
        company3 = payload.company3
        user_id = payload.user_id
        
        # Validate input
        if not company1 or not company2:
            raise HTTPException(status_code=400, detail="company1 and company2 are required")
        
        # Build company list and query
        companies = [company1.lower(), company2.lower()]
        comparison_str = f"{company1} vs {company2}"
        
        if company3:
            companies.append(company3.lower())
            comparison_str += f" vs {company3}"
            
        # Map companies to tickers
        tickers = []
        for company in companies:
            t = get_ticker(company)
            if t:
                tickers.append(t)
            else:
                # If it looks like a ticker, use it
                if len(company) <= 5 and " " not in company:
                    tickers.append(company.upper())
                else:
                    # Fallback? Maybe just warn or ignore?
                    # For now keep it as is, retrieve will fail to find collection and fallback to web search probably.
                    pass
        
        print(f"Mapped companies {companies} to tickers: {tickers}")
        
        # Generate session ID if not provided
        thread_id = payload.thread_id or f"comparison_{user_id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Create or get chat session for persistence
        chat_session = ChatService.create_or_get_chat_session(
            db=db,
            session_id=thread_id,
            user_id=user_id,
            agent_type=AgentType.RAG,
            portfolio_id=None,  # Comparisons are not portfolio-linked
            title=f"Comparison: {comparison_str}"
        )
        
        # Predefined comparison prompt
        query = f"""
Compare {comparison_str} 2024:
- Financial performance (revenue, earnings growth, net income/loss, operating margin)
- Investment & costs (Research and Development (R&D) expenses)
- Financial position (total assets, total debts)
- Business fundamentals (profit drivers, risk factors)
"""
        
        # Save user message
        ChatService.add_message(
            db=db,
            session_id=thread_id,
            role=MessageRole.USER,
            content=f"Compare {comparison_str}"
        )

        # NOTE: create_temporary might be redundant if we use existing ticker collections.
        # But for now we kept existing logic in vectordb_manager.
        # We will bypass using the returned company_filter and use our mapped tickers.
        vectordb_mgr = get_vectordb_manager()
        # db_instance, _ = vectordb_mgr.create_temporary(thread_id, companies) 
        # Commenting out create_temporary as we want to use existing collections
        # If we need ad-hoc ingestion for comparison, that's a separate feature.
        
        print(f"Compare mode: Using ticker-based collections")
        print(f"   Tickers: {tickers}")
        print(f"   Session ID: {thread_id}")
        
        config = {"configurable": {"thread_id": thread_id}}
        
        # Prepare inputs with comparison mode enabled
        inputs = {
            "messages": [HumanMessage(content=query)],
            "vectorstore_searched": False,
            "web_searched": False,
            "vectorstore_quality": "none",
            "needs_web_fallback": False,
            "retry_count": 0,
            "tool_calls": [],
            "document_sources": {},
            "citation_info": [],
            "summary_strategy": "single_source",
            #"vectordb_instance": db_instance,  # REMOVED: Retrieved dynamically in nodes
            "company_filter": tickers,  # Pass TICKERS here
            "sub_query_analysis": {},
            "sub_query_results": {},
            "is_comparison_mode": True,
            "comparison_company1": company1,
            "comparison_company2": company2,
            "comparison_company3": company3,
            "chart_url": None,
            "chart_filename": None
        }
        
        # Invoke with memory
        result = await agent.ainvoke(inputs, config)
        
        # Extract answer and chart URL
        answer = result["messages"][-1].content
        chart_url = result.get("chart_url")
        chart_filename = result.get("chart_filename")
        
        # Save assistant message with metadata
        ChatService.add_message(
            db=db,
            session_id=thread_id,
            role=MessageRole.ASSISTANT,
            content=answer,
            metadata={
                "comparison_companies": companies,
                "company1": company1,
                "company2": company2,
                "company3": company3,
                "chart_url": chart_url,
                "chart_filename": chart_filename,
                "vectorstore_searched": result.get("vectorstore_searched", False),
                "web_searched": result.get("web_searched", False),
                "document_count": len(result.get("documents", [])),
                "sources": [doc.metadata.get("source_file", "Unknown") for doc in result.get("documents", [])][:5]
            }
        )
        
        print(f"Comparison Query: {comparison_str}")
        print(f"Thread ID: {thread_id}")
        print(f"Chart URL: {chart_url}")
        print(f"Answer: {answer[:200]}...")
        print(f"Chat persisted to database")
        
        # Prepare response
        response_data = {
            "answer": answer,
            "thread_id": thread_id,
            "company1": company1,
            "company2": company2,
            "company3": company3,
            "company_filter": companies,
            "chart_url": chart_url,
            "chart_filename": chart_filename,
            "messages": [
                {
                    "type": msg.__class__.__name__,
                    "content": msg.content if hasattr(msg, 'content') else str(msg)
                }
                for msg in result.get("messages", [])
            ],
            "intermediate_message": result.get("Intermediate_message", ""),
            "documents": [
                {
                    "content": doc.page_content if hasattr(doc, 'page_content') else str(doc),
                    "metadata": doc.metadata if hasattr(doc, 'metadata') else {}
                }
                for doc in result.get("documents", [])
            ],
            "vectorstore_searched": result.get("vectorstore_searched", False),
            "web_searched": result.get("web_searched", False),
            "vectorstore_quality": result.get("vectorstore_quality", "none"),
            "needs_web_fallback": result.get("needs_web_fallback", False),
            "retry_count": result.get("retry_count", 0),
            "tool_calls": result.get("tool_calls", []),
            "document_sources": result.get("document_sources", {}),
            "citation_info": result.get("citation_info", []),
            "summary_strategy": result.get("summary_strategy", "single_source"),
            "sub_query_analysis": result.get("sub_query_analysis", {}),
            "sub_query_results": result.get("sub_query_results", {})
        }
        
        # Save response to output/json directory
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        json_dir = "output/json"
        os.makedirs(json_dir, exist_ok=True)
        json_path = os.path.join(json_dir, f"comparison_{timestamp}.json")
        with open(json_path, 'w') as f:
            json.dump(response_data, f, indent=4)
        
        return response_data
        
    except Exception as e:
        print(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health", response_model=HealthStatusResponse)
async def health_check():
    """
    Health check for RAG system.
    
    Checks:
    - RAG agent is initialized
    - Semantic cache is initialized
    - System is ready to handle queries
    """
    overall_healthy = agent is not None and semantic_cache is not None
    status = "healthy" if overall_healthy else "unhealthy"
    
    return HealthStatusResponse(
        status=status,
        agent_initialized=agent is not None,
        cache_initialized=semantic_cache is not None,
        timestamp=datetime.datetime.now().isoformat()
    )


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities():
    """Get information about available RAG capabilities"""
    return CapabilitiesResponse(
        document_qa=[
            "Portfolio-based document filtering",
            "Financial report Q&A (10-Ks, earnings calls, annual reports)",
            "Multi-document context synthesis",
            "Source citations and document references",
            "Web search fallback for missing information",
            "Semantic caching for faster responses",
            "Human-in-the-loop clarification requests",
            "Sub-query decomposition for complex questions"
        ],
        company_comparison=[
            "Multi-company financial comparison (2-3 companies)",
            "Revenue and earnings growth analysis",
            "R&D investment comparison",
            "Financial position analysis (assets, debts)",
            "Risk factor identification",
            "Visual chart generation for comparisons",
            "Side-by-side metric analysis"
        ],
        data_sources=[
            "Financial documents (PDF, DOCX)",
            "10-K annual reports",
            "10-Q quarterly reports",
            "Earnings call transcripts",
            "Annual reports",
            "Web search results (fallback)",
            "Chroma vector database"
        ],
        intelligent_features=[
            "Portfolio-scoped vector database filtering",
            "Context-aware conversation memory (LangGraph)",
            "Automatic quality assessment of retrieved documents",
            "Intelligent web fallback when documents insufficient",
            "Citation extraction and source tracking",
            "Session-based conversation persistence",
            "Semantic similarity caching",
            "Multi-document summarization strategies"
        ]
    )


@router.get("/sessions/{session_id}")
async def get_session_history(session_id: str):
    """
    Get conversation history for a specific RAG session.
    
    Returns the LangGraph conversation state including all messages
    and intermediate states for this session.
    """
    if not agent:
        raise HTTPException(
            status_code=503,
            detail="RAG agent not initialized."
        )
    
    try:
        state = await agent.aget_state(
            config={"configurable": {"thread_id": session_id}}
        )
        messages = state.values.get('messages', []) if state.values else []
        
        # Serialize messages
        serialized_messages = []
        for msg in messages:
            serialized_messages.append({
                "type": msg.type,
                "content": msg.content,
                "name": getattr(msg, 'name', None),
                "id": getattr(msg, 'id', None)
            })
        
        return {
            "session_id": session_id,
            "message_count": len(serialized_messages),
            "messages": serialized_messages,
            "vectorstore_searched": state.values.get("vectorstore_searched", False) if state.values else False,
            "web_searched": state.values.get("web_searched", False) if state.values else False,
            "company_filter": state.values.get("company_filter", []) if state.values else []
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving session: {str(e)}"
        )


@router.get("/portfolio/{portfolio_id}/sessions")
async def get_portfolio_rag_sessions(
    portfolio_id: int,
    db: Session = Depends(get_db_session)
):
    """
    Get all RAG sessions (ask + compare) linked to a portfolio.
    
    Returns all chat sessions where agent_type='rag' and 
    portfolio_id matches the requested portfolio.
    """
    # Verify portfolio exists
    portfolio = PortfolioService.get_portfolio(db, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    
    # Get all RAG sessions for this portfolio
    sessions = ChatService.get_portfolio_sessions(
        db=db,
        portfolio_id=portfolio_id,
        agent_type=AgentType.RAG
    )
    
    # Build response with message counts
    result = []
    for session in sessions:
        message_count = len(session.messages)
        result.append({
            "session_id": session.session_id,
            "user_id": session.user_id,
            "agent_type": session.agent_type.value,
            "portfolio_id": session.portfolio_id,
            "title": session.title,
            "is_active": session.is_active,
            "message_count": message_count,
            "created_at": session.created_at.isoformat(),
            "last_message_at": session.last_message_at.isoformat() if session.last_message_at else None
        })
    
    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": portfolio.name,
        "companies": portfolio.company_names,
        "session_count": len(result),
        "sessions": result
    }
