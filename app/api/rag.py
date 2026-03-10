"""
RAG endpoints (ask and compare) — streaming SSE responses.

Both /ask and /compare now return Server-Sent Events so the React frontend
can show real-time agentic progress milestones instead of waiting for a
single JSON blob.

SSE event types emitted:
  status   – milestone update after each visible graph node
  answer   – final answer text (+ chart_url for comparisons)
  metadata – full result metadata (sources, flags, etc.)
  done     – stream end signal
  error    – on exceptions
"""
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
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
from app.api.stream_utils import rag_stream_generator, format_sse
import hashlib
import datetime

router = APIRouter(tags=["RAG"])

# SSE response headers (prevents nginx/CDN buffering)
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

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


class HealthStatusResponse(BaseModel):
    status: str
    agent_initialized: bool
    timestamp: str


class CapabilitiesResponse(BaseModel):
    document_qa: List[str]
    company_comparison: List[str]
    data_sources: List[str]
    intelligent_features: List[str]


# ---------------------------------------------------------------------------
# Global agent reference (set by main app on startup)
# ---------------------------------------------------------------------------

agent = None


def set_agent(agent_instance):
    global agent
    agent = agent_instance


# ---------------------------------------------------------------------------
# POST /ask  — streaming RAG query
# ---------------------------------------------------------------------------

@router.post("/ask")
async def ask_agent(
    payload: AskInput,
    db: Session = Depends(get_db_session)
):
    """
    Execute a RAG query with portfolio-based filtering.
    Returns a Server-Sent Events stream of milestone + answer events.
    """
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    query = payload.query
    thread_id = payload.thread_id

    # --- pre-stream validation (raise HTTP errors before we start streaming) ---

    session = PortfolioService.get_session(db, thread_id)
    if not session:
        raise HTTPException(
            status_code=404,
            detail="Session not found. Please create a portfolio session first."
        )

    portfolio = session.portfolio

    # Map portfolio companies to tickers
    company_tickers = []
    for company in portfolio.company_names:
        t = get_ticker(company)
        company_tickers.append(t if t else company)

    # Create or get chat session for persistence
    ChatService.create_or_get_chat_session(
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
            "tickers": company_tickers,
        },
    )

    # Save user message before streaming
    ChatService.add_message(
        db=db,
        session_id=thread_id,
        role=MessageRole.USER,
        content=query,
    )

    # Register session with VectorDBManager
    vectordb_mgr = get_vectordb_manager()
    vectordb_mgr.register_session(thread_id, portfolio.id)

    print(f"[/ask] Portfolio: {portfolio.name} | Tickers: {company_tickers}")

    config = {"configurable": {"thread_id": thread_id}}

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
        "company_filter": company_tickers,
        "ticker": None,
        "sub_query_analysis": {},
        "sub_query_results": {},
    }

    # Metadata forwarded to the stream generator for DB persistence
    extra_metadata = {
        "portfolio_id": portfolio.id,
        "portfolio_name": portfolio.name,
        "company_filter": company_tickers,
    }

    generator = rag_stream_generator(
        agent=agent,
        inputs=inputs,
        config=config,
        thread_id=thread_id,
        extra_metadata=extra_metadata,
    )

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ---------------------------------------------------------------------------
# POST /compare  — streaming company comparison
# ---------------------------------------------------------------------------

@router.post("/compare")
async def compare_companies(
    payload: CompareInput,
    db: Session = Depends(get_db_session)
):
    """
    Compare 2-3 companies using the RAG graph.
    Returns a Server-Sent Events stream. The answer event includes chart_url
    when a comparison chart is generated.
    """
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    company1 = payload.company1
    company2 = payload.company2
    company3 = payload.company3
    user_id = payload.user_id

    if not company1 or not company2:
        raise HTTPException(status_code=400, detail="company1 and company2 are required")

    # Build company list
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
        elif len(company) <= 5 and " " not in company:
            tickers.append(company.upper())

    print(f"[/compare] Companies: {companies} → Tickers: {tickers}")

    # Deterministic thread_id (stable across re-comparisons of the same companies)
    if payload.thread_id:
        thread_id = payload.thread_id
    else:
        companies_key = "_".join(sorted(companies))
        thread_id = f"compare_{user_id}_{hashlib.md5(companies_key.encode()).hexdigest()[:12]}"

    # Create or get chat session
    ChatService.create_or_get_chat_session(
        db=db,
        session_id=thread_id,
        user_id=user_id,
        agent_type=AgentType.RAG,
        portfolio_id=None,
        title=f"Comparison: {comparison_str}",
        session_metadata={
            "type": "compare",
            "companies": companies,
            "tickers": tickers,
            "year": payload.year,
        },
    )

    # Build comparison prompt
    from datetime import datetime as _dt
    year_str = str(payload.year) if payload.year else str(_dt.now().year)
    query = (
        f"Compare {comparison_str} {year_str}:\n"
        "- Financial performance (revenue, earnings growth, net income/loss, operating margin)\n"
        "- Investment & costs (Research and Development (R&D) expenses)\n"
        "- Financial position (total assets, total debts)\n"
        "- Business fundamentals (profit drivers, risk factors)\n"
    )

    # Save user message before streaming
    ChatService.add_message(
        db=db,
        session_id=thread_id,
        role=MessageRole.USER,
        content=f"Compare {comparison_str}",
    )

    config = {"configurable": {"thread_id": thread_id}}

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
        "company_filter": tickers,
        "sub_query_analysis": {},
        "sub_query_results": {},
        "is_comparison_mode": True,
        "comparison_company1": company1,
        "comparison_company2": company2,
        "comparison_company3": company3,
        "year_start": payload.year,
        "year_end": payload.year,
        "chart_url": None,
        "chart_filename": None,
    }

    extra_metadata = {
        "comparison_companies": companies,
        "company1": company1,
        "company2": company2,
        "company3": company3,
        "year": payload.year,
        "thread_id": thread_id,
    }

    generator = rag_stream_generator(
        agent=agent,
        inputs=inputs,
        config=config,
        thread_id=thread_id,
        extra_metadata=extra_metadata,
    )

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthStatusResponse)
async def health_check():
    status = "healthy" if agent is not None else "unhealthy"
    return HealthStatusResponse(
        status=status,
        agent_initialized=agent is not None,
        timestamp=datetime.datetime.now().isoformat(),
    )


# ---------------------------------------------------------------------------
# GET /capabilities
# ---------------------------------------------------------------------------

@router.get("/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities():
    return CapabilitiesResponse(
        document_qa=[
            "Portfolio-based document filtering",
            "Financial report Q&A (10-Ks, earnings calls, annual reports)",
            "Multi-document context synthesis",
            "Source citations and document references",
            "Web search fallback for missing information",
            "Sub-query decomposition for complex questions",
        ],
        company_comparison=[
            "Multi-company financial comparison (2-3 companies)",
            "Revenue and earnings growth analysis",
            "R&D investment comparison",
            "Financial position analysis (assets, debts)",
            "Risk factor identification",
            "Visual chart generation for comparisons",
            "Side-by-side metric analysis",
        ],
        data_sources=[
            "Financial documents (PDF, DOCX)",
            "10-K annual reports",
            "10-Q quarterly reports",
            "Earnings call transcripts",
            "Annual reports",
            "Web search results (fallback)",
            "Chroma vector database",
        ],
        intelligent_features=[
            "Real-time agentic streaming (SSE)",
            "Portfolio-scoped vector database filtering",
            "Context-aware conversation memory (LangGraph)",
            "Automatic quality assessment of retrieved documents",
            "Intelligent web fallback when documents insufficient",
            "Citation extraction and source tracking",
            "Session-based conversation persistence",
            "Multi-document summarization strategies",
        ],
    )


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}
# ---------------------------------------------------------------------------

@router.get("/sessions/{session_id}")
async def get_session_history(session_id: str):
    """
    Get LangGraph conversation state for a specific RAG session.
    """
    if not agent:
        raise HTTPException(status_code=503, detail="RAG agent not initialized.")

    try:
        state = await agent.aget_state(
            config={"configurable": {"thread_id": session_id}}
        )
        messages = state.values.get("messages", []) if state.values else []

        serialized_messages = [
            {
                "type": msg.type,
                "content": msg.content,
                "name": getattr(msg, "name", None),
                "id": getattr(msg, "id", None),
            }
            for msg in messages
        ]

        return {
            "session_id": session_id,
            "message_count": len(serialized_messages),
            "messages": serialized_messages,
            "vectorstore_searched": state.values.get("vectorstore_searched", False) if state.values else False,
            "web_searched": state.values.get("web_searched", False) if state.values else False,
            "company_filter": state.values.get("company_filter", []) if state.values else [],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving session: {str(e)}")


# ---------------------------------------------------------------------------
# GET /portfolio/{portfolio_id}/sessions
# ---------------------------------------------------------------------------

@router.get("/portfolio/{portfolio_id}/sessions")
async def get_portfolio_rag_sessions(
    portfolio_id: int,
    db: Session = Depends(get_db_session)
):
    """
    Get all RAG sessions linked to a portfolio.
    """
    portfolio = PortfolioService.get_portfolio(db, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    sessions = ChatService.get_portfolio_sessions(
        db=db,
        portfolio_id=portfolio_id,
        agent_type=AgentType.RAG,
    )

    result = [
        {
            "session_id": s.session_id,
            "user_id": s.user_id,
            "agent_type": s.agent_type.value,
            "portfolio_id": s.portfolio_id,
            "title": s.title,
            "is_active": s.is_active,
            "message_count": len(s.messages),
            "created_at": s.created_at.isoformat(),
            "last_message_at": s.last_message_at.isoformat() if s.last_message_at else None,
        }
        for s in sessions
    ]

    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": portfolio.name,
        "companies": portfolio.company_names,
        "session_count": len(result),
        "sessions": result,
    }
