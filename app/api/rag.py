"""
RAG endpoints (ask and compare) with portfolio integration and chat persistence
"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from langchain_core.messages import HumanMessage
from app.database.connection import get_db_session
from app.services.portfolio import PortfolioService
from app.services.chat import ChatService
from app.database.models import AgentType, MessageRole, User, ChatSession
from app.auth.deps import get_current_user, verify_user_id_matches, verify_owner
from app.services.vectordb_manager import get_vectordb_manager
from app.utils.company_mapping import get_ticker
import uuid
import json
import datetime
import os

logger = logging.getLogger("api.rag")
router = APIRouter(tags=["RAG"])

# Debug-only: dump full response payloads to output/json/ on every /ask or
# /compare call. Off by default — was previously unconditional, growing
# output/json/ without bound on every request in any environment.
_SAVE_DEBUG_RESPONSES = os.getenv("SAVE_DEBUG_RESPONSES", "false").lower() == "true"


def _maybe_save_debug_response(response_data: dict, prefix: str) -> None:
    if not _SAVE_DEBUG_RESPONSES:
        return
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        json_dir = "output/json"
        os.makedirs(json_dir, exist_ok=True)
        json_path = os.path.join(json_dir, f"{prefix}_{timestamp}.json")
        with open(json_path, 'w') as f:
            json.dump(response_data, f, indent=4)
        logger.info("Debug response saved to: %s", json_path)
    except Exception as e:
        logger.warning("Failed to save debug response: %s", e)


def _build_citation_info(result: dict) -> list:
    """Build clean, structured citation info from document metadata if not present"""
    citation_info = result.get("citation_info", [])
    if citation_info:
        return citation_info

    docs = result.get("documents", [])
    if not docs:
        return []

    seen = set()
    built = []
    for doc in docs:
        meta = doc.metadata if hasattr(doc, "metadata") else {}
        sf = meta.get("source_file", meta.get("source", ""))
        pg = meta.get("page_num")
        if sf:
            key = (sf, pg)
            if key not in seen:
                seen.add(key)
                built.append({
                    "source_file": sf,
                    "page_num": pg,
                    "company": meta.get("company"),
                    "filing_type": meta.get("filing_type"),
                    "period_end_date": meta.get("period_end_date"),
                    "year": meta.get("year")
                })
    return built


# Pydantic Models
class AskInput(BaseModel):
    query: str = Field(..., description="User query")
    thread_id: str = Field(..., description="Session thread_id (required for portfolio context)")


class CompareInput(BaseModel):
    company1: str = Field(..., description="First company to compare")
    company2: str = Field(..., description="Second company to compare")
    company3: Optional[str] = Field(None, description="Optional third company")
    user_id: str = Field(..., description="User identifier")
    thread_id: Optional[str] = Field(None, description="Optional thread_id for conversation continuity")
    year: Optional[int] = Field(None, description="Year for comparison (e.g. 2024)")


class AlphaInput(BaseModel):
    tickers: List[str] = Field(..., description="Ticker symbols to run the ALPHA framework for — no free-text query")
    user_id: str = Field(..., description="User identifier")
    thread_id: str = Field(..., description="Session thread_id (required for portfolio context)")


class HealthStatusResponse(BaseModel):
    status: str
    agent_initialized: bool
    timestamp: str


class CapabilitiesResponse(BaseModel):
    document_qa: List[str]
    company_comparison: List[str]
    data_sources: List[str]
    intelligent_features: List[str]


# Global references (set by main app)
agent = None


def set_agent(agent_instance):
    """Set the global agent instance"""
    global agent
    agent = agent_instance


@router.post("/ask")
async def ask_agent(
    payload: AskInput,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
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

        # Get session and associated portfolio
        session = await PortfolioService.get_session(db, thread_id)
        if not session:
            raise HTTPException(
                status_code=404,
                detail=f"Session not found. Please create a portfolio session first."
            )
        verify_owner(session.user_id, current_user)

        portfolio = session.portfolio

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

        # Create or get chat session for persistence
        chat_session = await ChatService.create_or_get_chat_session(
            db=db,
            session_id=thread_id,
            user_id=session.user_id,
            agent_type=AgentType.RAG,
            portfolio_id=portfolio.id,
            title=f"RAG: {portfolio.name}",
            session_metadata={
                "type": "ask",
                "portfolio_name": portfolio.name,
                "companies": portfolio.company_names,
                "tickers": company_tickers
            }
        )

        # Save user message
        await ChatService.add_message(
            db=db,
            session_id=thread_id,
            role=MessageRole.USER,
            content=query
        )

        # Register session with VectorDBManager (for context tracking)
        vectordb_mgr = get_vectordb_manager()
        vectordb_mgr.register_session(thread_id, portfolio.id)
                
        logger.info("Portfolio-scoped context: %s | Tickers: %s", portfolio.name, company_tickers)
        
        config = {"configurable": {"thread_id": f"rag:{thread_id}"}}
        
        # Standard execution
        inputs = {
            "messages": [HumanMessage(content=query)],
            "vectorstore_searched": False,
            "web_searched": False,
            "vectorstore_quality": "none",
            "needs_web_fallback": False,
            "retry_count": 0,
            "documents": [],
            "document_sources": {},
            "citation_info": [],
            "summary_strategy": "single_source",
            "company_filter": company_tickers,  # Pass valid tickers for this portfolio
            "ticker": None,  # Explicitly None, relying on company_filter
            "sub_query_analysis": {},
            "sub_query_results": {},
            # Explicitly reset comparison-mode fields: the checkpointer merges
            # each turn's inputs on top of the LAST checkpointed state for this
            # thread_id, so if this thread was ever used for a /compare call
            # (which sets these), they'd otherwise persist forever and silently
            # route every future /ask question through the annual-only 10-K
            # comparison templates instead of the real analyzer.
            "is_comparison_mode": False,
            "comparison_company1": None,
            "comparison_company2": None,
            "comparison_company3": None,
            "year_start": None,
            "year_end": None
        }
        result = await agent.ainvoke(inputs, config)
    
        # Extract answer
        answer = result["messages"][-1].content
        
        # Save assistant message with metadata
        await ChatService.add_message(
            db=db,
            session_id=thread_id,
            role=MessageRole.ASSISTANT,
            content=answer,
            metadata={
                "portfolio_id": portfolio.id,
                "portfolio_name": portfolio.name,
                "company_filter": company_tickers,
                "chart_url": result.get("chart_url"),
                "chart_filename": result.get("chart_filename"),
                "vectorstore_searched": result.get("vectorstore_searched", False),
                "web_searched": result.get("web_searched", False),
                "vectorstore_quality": result.get("vectorstore_quality", "none"),
                "needs_web_fallback": result.get("needs_web_fallback", False),
                "retry_count": result.get("retry_count", 0),
                "summary_strategy": result.get("summary_strategy", "single_source"),
                "document_count": len(result.get("documents", [])),
                "sources": [doc.metadata.get("source_file", "Unknown") for doc in result.get("documents", [])][:5],
                "citation_info": _build_citation_info(result),
                "document_sources": result.get("document_sources", {}),
                "documents": [
                    {
                        "metadata": doc.metadata if hasattr(doc, "metadata") else {}
                    }
                    for doc in result.get("documents", [])
                ],
                "sub_query_analysis": result.get("sub_query_analysis", {}),
                "sub_query_results": result.get("sub_query_results", {}),
                "intermediate_message": result.get("Intermediate_message", ""),
                "ticker": result.get("ticker")
            }
        )
        
        logger.info("Query: %s | Thread: %s | Answer: %.200s...", query, thread_id, answer)

        # Prepare response
        response_data = {
            "answer": answer,
            "thread_id": thread_id,
            "portfolio_id": portfolio.id,
            "portfolio_name": portfolio.name,
            "company_filter": company_tickers,
            "ticker": None,
            "chart_url": result.get("chart_url"),
            "chart_filename": result.get("chart_filename"),
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
            "document_sources": result.get("document_sources", {}),
            "citation_info": _build_citation_info(result),
            "summary_strategy": result.get("summary_strategy", "single_source"),
            "sub_query_analysis": result.get("sub_query_analysis", {}),
            "sub_query_results": result.get("sub_query_results", {})
        }
        
        _maybe_save_debug_response(response_data, "ask")

        return response_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in /ask: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/compare")
async def compare_companies(
    payload: CompareInput,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Handle company comparison queries with chat persistence.
    Creates a TEMPORARY Vector DB instance with specified companies.
    Does NOT affect portfolio-scoped DB instances.
    """
    verify_user_id_matches(payload.user_id, current_user)
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
        
        logger.info("Mapped companies %s to tickers: %s", companies, tickers)
        
        # Generate a stable session ID based on user + companies if not provided.
        # Using a deterministic hash ensures the same comparison always continues
        # the same session rather than creating a new one on every call.
        if payload.thread_id:
            thread_id = payload.thread_id
        else:
            import hashlib
            companies_key = "_".join(sorted(companies))  # sorted for order-independence
            thread_id = f"compare_{user_id}_{hashlib.md5(companies_key.encode()).hexdigest()[:12]}"
        
        # Create or get chat session for persistence
        chat_session = await ChatService.create_or_get_chat_session(
            db=db,
            session_id=thread_id,
            user_id=user_id,
            agent_type=AgentType.RAG,
            portfolio_id=None,  # Comparisons are not portfolio-linked
            title=f"Comparison: {comparison_str}",
            session_metadata={
                "type": "compare",
                "companies": companies,
                "tickers": tickers,
                "year": payload.year
            }
        )
        # If payload.thread_id referenced a PRE-EXISTING session, guard against
        # it belonging to a different user (create_or_get_chat_session returns
        # the existing row rather than creating a new one in that case).
        verify_owner(chat_session.user_id, current_user)

        # Build year string for the query
        year_str = str(payload.year) if payload.year else "2024"
        
        # Predefined comparison prompt
        query = f"""
Compare {comparison_str} {year_str}:
- Financial performance (revenue, earnings growth, net income/loss, operating margin)
- Investment & costs (Research and Development (R&D) expenses)
- Financial position (total assets, total debts)
- Business fundamentals (profit drivers, risk factors)
"""
        
        # Save user message
        await ChatService.add_message(
            db=db,
            session_id=thread_id,
            role=MessageRole.USER,
            content=f"Compare {comparison_str}"
        )

        # Create_temporary might be redundant if we use existing ticker collections.
        # But for now we kept existing logic in vectordb_manager.
        # Bypass using the returned company_filter and use our mapped tickers.
        vectordb_mgr = get_vectordb_manager()
        # db_instance, _ = vectordb_mgr.create_temporary(thread_id, companies) 
        # Commenting out create_temporary as we want to use existing collections
        # If we need ad-hoc ingestion for comparison, that's a separate feature.
        
        logger.info("Compare mode: tickers=%s session=%s", tickers, thread_id)
        
        config = {"configurable": {"thread_id": f"rag:{thread_id}"}}
        
        # Prepare inputs with comparison mode enabled
        inputs = {
            "messages": [HumanMessage(content=query)],
            "vectorstore_searched": False,
            "web_searched": False,
            "vectorstore_quality": "none",
            "needs_web_fallback": False,
            "retry_count": 0,
            "documents": [],
            "document_sources": {},
            "citation_info": [],
            "summary_strategy": "single_source",
            #"vectordb_instance": db_instance,  # REMOVED: Retrieved dynamically in nodes
            "company_filter": tickers,  # Pass TICKERS here
            "ticker": None,  # Reset any ticker left over from a prior /ask turn on this thread_id
            "sub_query_analysis": {},
            "sub_query_results": {},
            "is_comparison_mode": True,
            "comparison_company1": company1,
            "comparison_company2": company2,
            "comparison_company3": company3,
            "year_start": payload.year,
            "year_end": payload.year,
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
        await ChatService.add_message(
            db=db,
            session_id=thread_id,
            role=MessageRole.ASSISTANT,
            content=answer,
            metadata={
                "comparison_companies": companies,
                "company1": company1,
                "company2": company2,
                "company3": company3,
                "year": payload.year,
                "chart_url": chart_url,
                "chart_filename": chart_filename,
                "vectorstore_searched": result.get("vectorstore_searched", False),
                "web_searched": result.get("web_searched", False),
                "vectorstore_quality": result.get("vectorstore_quality", "none"),
                "needs_web_fallback": result.get("needs_web_fallback", False),
                "retry_count": result.get("retry_count", 0),
                "summary_strategy": result.get("summary_strategy", "single_source"),
                "document_count": len(result.get("documents", [])),
                "sources": [doc.metadata.get("source_file", "Unknown") for doc in result.get("documents", [])][:5],
                "citation_info": _build_citation_info(result),
                "document_sources": result.get("document_sources", {}),
                "documents": [
                    {
                        "metadata": doc.metadata if hasattr(doc, "metadata") else {}
                    }
                    for doc in result.get("documents", [])
                ],
                "sub_query_analysis": result.get("sub_query_analysis", {}),
                "sub_query_results": result.get("sub_query_results", {}),
                "intermediate_message": result.get("Intermediate_message", "")
            }
        )
        
        logger.info("Comparison query: %s | Thread: %s | Chart: %s", comparison_str, thread_id, chart_url)
        
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
            "document_sources": result.get("document_sources", {}),
            "citation_info": _build_citation_info(result),
            "summary_strategy": result.get("summary_strategy", "single_source"),
            "sub_query_analysis": result.get("sub_query_analysis", {}),
            "sub_query_results": result.get("sub_query_results", {})
        }
        
        _maybe_save_debug_response(response_data, "comparison")

        return response_data

    except Exception as e:
        logger.error("Error in /compare: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/alpha")
async def run_alpha(
    payload: AlphaInput,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Run the ALPHA framework directly for a list of tickers — no free-text query.
    Intended for the dedicated ALPHA button in the UI: it just passes the
    portfolio's ticker symbols and triggers the full 5-dimension analysis
    per ticker, bypassing the (now-disabled) keyword detection in general chat.
    """
    verify_user_id_matches(payload.user_id, current_user)
    try:
        if not agent:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        thread_id = payload.thread_id

        session = await PortfolioService.get_session(db, thread_id)
        if not session:
            raise HTTPException(
                status_code=404,
                detail=f"Session not found. Please create a portfolio session first."
            )
        verify_owner(session.user_id, current_user)

        portfolio = session.portfolio

        resolved_tickers = []
        for t in payload.tickers:
            ticker = get_ticker(t) or t.upper()
            resolved_tickers.append(ticker)

        chat_session = await ChatService.create_or_get_chat_session(
            db=db,
            session_id=thread_id,
            user_id=session.user_id,
            agent_type=AgentType.RAG,
            portfolio_id=portfolio.id,
            title=f"ALPHA: {portfolio.name}",
            session_metadata={
                "type": "alpha",
                "portfolio_name": portfolio.name,
                "tickers": resolved_tickers
            }
        )

        await ChatService.add_message(
            db=db,
            session_id=thread_id,
            role=MessageRole.USER,
            content=f"Provide 360 degree ALPHA analysis for {', '.join(resolved_tickers)} stock"
        )

        results = []
        for ticker in resolved_tickers:
            logger.info("Running ALPHA for ticker: %s", ticker)

            # Each ticker gets its own checkpointer thread so per-ticker
            # alpha_dimensions/state don't leak into one another.
            config = {"configurable": {"thread_id": f"rag:{thread_id}-alpha-{ticker}"}}

            inputs = {
                "messages": [HumanMessage(content=f"ALPHA analysis for {ticker}")],
                "vectorstore_searched": False,
                "web_searched": False,
                "vectorstore_quality": "none",
                "needs_web_fallback": False,
                "retry_count": 0,
                "documents": [],
                "document_sources": {},
                "citation_info": [],
                "summary_strategy": "single_source",
                "company_filter": [ticker],
                "sub_query_analysis": {},
                "sub_query_results": {},
                "alpha_mode": True,
                "alpha_pillar": None,
                "ticker": ticker,
                "alpha_dimensions": {},
                "alpha_report": ""
            }

            result = await agent.ainvoke(inputs, config)
            report = result.get("alpha_report") or result["messages"][-1].content

            results.append({
                "ticker": ticker,
                "report": report
            })

            await ChatService.add_message(
                db=db,
                session_id=thread_id,
                role=MessageRole.ASSISTANT,
                content=report,
                metadata={
                    "portfolio_id": portfolio.id,
                    "portfolio_name": portfolio.name,
                    "ticker": ticker,
                    "alpha_pillar": None
                }
            )

        return {
            "thread_id": thread_id,
            "portfolio_id": portfolio.id,
            "portfolio_name": portfolio.name,
            "tickers": resolved_tickers,
            "results": results
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in /alpha: %s", e)
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
    status = "healthy" if agent is not None else "unhealthy"

    return HealthStatusResponse(
        status=status,
        agent_initialized=agent is not None,
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
async def get_session_history(
    session_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
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

    result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
    chat_session = result.scalar_one_or_none()
    if not chat_session:
        raise HTTPException(status_code=404, detail="Session not found")
    verify_owner(chat_session.user_id, current_user)

    try:
        state = await agent.aget_state(
            config={"configurable": {"thread_id": f"rag:{session_id}"}}
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
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get all RAG sessions (ask + compare) linked to a portfolio.

    Returns all chat sessions where agent_type='rag' and
    portfolio_id matches the requested portfolio.
    """
    # Verify portfolio exists
    portfolio = await PortfolioService.get_portfolio(db, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    verify_owner(portfolio.user_id, current_user)

    # Get all RAG sessions for this portfolio
    sessions = await ChatService.get_portfolio_sessions(
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
