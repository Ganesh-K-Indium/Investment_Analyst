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
import uuid
import json
import datetime
import os

router = APIRouter(tags=["RAG"])


# Pydantic Models
class AskInput(BaseModel):
    query: str = Field(..., description="User query")
    thread_id: str = Field(..., description="Session thread_id (required for portfolio context)")


class CompareInput(BaseModel):
    company1: str = Field(..., description="First company to compare")
    company2: str = Field(..., description="Second company to compare")
    company3: Optional[str] = Field(None, description="Optional third company")
    thread_id: Optional[str] = Field(None, description="Optional thread_id for conversation continuity")


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
    Uses the pre-initialized Vector DB instance from portfolio activation.
    """
    try:
        if not agent:
            raise HTTPException(status_code=503, detail="Agent not initialized")
        
        query = payload.query
        thread_id = payload.thread_id
        
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
        
        # CRITICAL: Get the PRE-INITIALIZED Vector DB instance
        # This was created when the portfolio was created!
        vectordb_mgr = get_vectordb_manager()
        vectordb_result = vectordb_mgr.get_for_session(thread_id)
        
        if not vectordb_result:
            print(f"Vector DB instance not in memory for thread {thread_id}. Attempting lazy load...")
            # Lazy loading: Re-initialize from portfolio data
            # This handles server restarts where in-memory state is lost but DB session persists
            try:
                # Initialize at PORTFOLIO level, then register session
                vectordb_mgr.initialize_for_portfolio(portfolio.id, portfolio.company_names)
                vectordb_mgr.register_session(thread_id, portfolio.id)
                vectordb_result = vectordb_mgr.get_for_session(thread_id)
                print(f"Successfully lazy-loaded Vector DB for portfolio {portfolio.id}")
            except Exception as e:
                print(f"Failed to lazy-load Vector DB: {e}")
                
        if not vectordb_result:
            raise HTTPException(
                status_code=500,
                detail="Vector DB not initialized for this session. Please reactivate the portfolio."
            )
        
        db_instance, company_filter = vectordb_result
        
        print(f"Using portfolio-scoped Vector DB")
        print(f"   Portfolio: {portfolio.name}")
        print(f"   Companies: {company_filter}")
        print(f"   NO company name needed in state - DB already filtered!")
        
        config = {"configurable": {"thread_id": thread_id}}
        
        # Check semantic cache
        if semantic_cache:
            start_time = datetime.datetime.now()
            cached_data = semantic_cache.lookup(query, thread_id=thread_id)
            if cached_data:
                print(f"Returning cached response for: {query}")
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
            # Standard execution with portfolio filtering
            # IMPORTANT: We pass the DB instance directly, not company names!
            # The DB is already scoped to portfolio companies
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
                "company_filter": company_filter,  # For logging/display only
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
                "company_filter": company_filter,
                "vectorstore_searched": result.get("vectorstore_searched", False),
                "web_searched": result.get("web_searched", False),
                "document_count": len(result.get("documents", [])),
                "sources": [doc.metadata.get("source_file", "Unknown") for doc in result.get("documents", [])][:5]
            }
        )
        
        print(f"Query: {query}")
        print(f"Thread ID: {thread_id}")
        print(f"Portfolio: {portfolio.name} (Companies: {company_filter})")
        print(f"Answer: {answer[:200]}...")
        print(f"Chat persisted to database")
        
        # Prepare response
        response_data = {
            "answer": answer,
            "thread_id": thread_id,
            "portfolio_id": portfolio.id,
            "portfolio_name": portfolio.name,
            "company_filter": company_filter,
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
            semantic_cache.update(query, response_data, thread_id=thread_id)
        
        return response_data
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/compare")
async def compare_companies(payload: CompareInput):
    """
    Handle company comparison queries.
    Creates a TEMPORARY Vector DB instance with specified companies.
    Does NOT affect portfolio-scoped DB instances.
    """
    try:
        if not agent:
            raise HTTPException(status_code=503, detail="Agent not initialized")
        
        company1 = payload.company1
        company2 = payload.company2
        company3 = payload.company3
        
        # Validate input
        if not company1 or not company2:
            raise HTTPException(status_code=400, detail="company1 and company2 are required")
        
        # Build company list and query
        companies = [company1.lower(), company2.lower()]
        comparison_str = f"{company1} vs {company2}"
        
        if company3:
            companies.append(company3.lower())
            comparison_str += f" vs {company3}"
        
        # This does NOT affect any portfolio DB instances!
        # Use provided thread_id or generate a new one
        thread_id = payload.thread_id or f"comparison_{uuid.uuid4()}"

        vectordb_mgr = get_vectordb_manager()
        db_instance, company_filter = vectordb_mgr.create_temporary(thread_id, companies)
        
        print(f"Compare mode: Using temporary Vector DB")
        print(f"   Companies: {companies}")
        print(f"   Portfolio DB instances unaffected")
        
        # Predefined comparison prompt
        query = f"""
Compare {comparison_str} 2024:
- Financial performance (revenue, earnings growth, net income/loss, operating margin)
- Investment & costs (Research and Development (R&D) expenses)
- Financial position (total assets, total debts)
- Business fundamentals (profit drivers, risk factors)
"""
        
        # Use provided thread_id or generate a new one - MOVED UP
        # thread_id = payload.thread_id or f"comparison_{uuid.uuid4()}"
        config = {"configurable": {"thread_id": thread_id}}
        
        # Prepare inputs with comparison mode enabled
        # Pass the TEMPORARY DB instance, not company names
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
            "company_filter": company_filter,  # For logging/display
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
        
        print(f"Comparison Query: {comparison_str}")
        print(f"Thread ID: {thread_id}")
        print(f"Chart URL: {chart_url}")
        print(f"Answer: {answer[:200]}...")
        
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
