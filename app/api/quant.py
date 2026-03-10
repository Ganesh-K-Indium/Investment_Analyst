"""
Quant Stock Analysis API endpoints — streaming SSE responses.

POST /quant/query now returns a Server-Sent Events stream so the frontend
can display real-time progress as each sub-agent in the supervisor executes.

SSE event types emitted:
  status   – milestone after each agent node (ticker_finder, stock_info, etc.)
  answer   – final answer text + which agent produced it
  metadata – session / portfolio context
  done     – stream end signal
  error    – on exceptions
"""
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from langchain_core.messages import HumanMessage
from app.database.connection import get_db_session
from app.services.portfolio import PortfolioService
from app.services.chat import ChatService
from app.database.models import AgentType, MessageRole
from app.api.stream_utils import quant_stream_generator, format_sse
from datetime import datetime

router = APIRouter(prefix="/quant", tags=["Quant Analysis"])

# SSE response headers (prevents nginx/CDN buffering)
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class StockQueryRequest(BaseModel):
    query: str = Field(..., description="Stock analysis query")
    portfolio_id: Optional[int] = Field(None, description="Optional portfolio ID to link query")
    user_id: str = Field(..., description="User identifier")
    session_id: Optional[str] = Field(None, description="Session ID for conversation continuity")


class HealthStatusResponse(BaseModel):
    status: str
    servers_ready: Dict[str, bool]
    agents_ready: bool
    timestamp: str


class CapabilitiesResponse(BaseModel):
    fundamental_analysis: List[str]
    technical_analysis: List[str]
    research_analysis: List[str]
    ticker_lookup: List[str]
    intelligent_features: List[str]


# ---------------------------------------------------------------------------
# Global references (set by main app during startup)
# ---------------------------------------------------------------------------

stock_supervisor = None
agents_initialized = False


def set_stock_supervisor(supervisor_instance):
    global stock_supervisor
    stock_supervisor = supervisor_instance


def set_agents_status(status: bool):
    global agents_initialized
    agents_initialized = status


# ---------------------------------------------------------------------------
# POST /quant/query  — streaming multi-agent analysis
# ---------------------------------------------------------------------------

@router.post("/query")
async def query_stock_agent(
    payload: StockQueryRequest,
    db: Session = Depends(get_db_session)
):
    """
    Send a query to the stock analysis supervisor agent.
    Returns a Server-Sent Events stream showing real-time progress
    across ticker_finder, stock_information, technical_analysis,
    and research sub-agents.
    """
    if not agents_initialized or stock_supervisor is None:
        raise HTTPException(
            status_code=503,
            detail="Stock analysis agents not initialized. Please check system status."
        )

    # --- Resolve session ID (stable across turns for same session) ---
    session_id = payload.session_id
    if not session_id:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if payload.portfolio_id:
            session_id = f"quant_portfolio_{payload.portfolio_id}_{ts}"
        else:
            session_id = f"quant_{payload.user_id}_{ts}"

    print(f"[/quant/query] Session: {session_id} | User: {payload.user_id} | Query: {payload.query[:80]}...")

    # --- Portfolio metadata (optional) ---
    portfolio_name = None
    if payload.portfolio_id:
        portfolio = PortfolioService.get_portfolio(db, payload.portfolio_id)
        if portfolio:
            portfolio_name = portfolio.name

    # --- Create / get chat session ---
    ChatService.create_or_get_chat_session(
        db=db,
        session_id=session_id,
        user_id=payload.user_id,
        agent_type=AgentType.QUANT,
        portfolio_id=payload.portfolio_id,
        title=f"Stock Analysis: {portfolio_name}" if portfolio_name else "Stock Analysis",
    )

    # --- Save user message before streaming ---
    ChatService.add_message(
        db=db,
        session_id=session_id,
        role=MessageRole.USER,
        content=payload.query,
    )

    config = {"configurable": {"thread_id": session_id}}
    inputs = {"messages": [HumanMessage(content=payload.query)]}

    extra_metadata = {
        "session_id": session_id,
        "portfolio_id": payload.portfolio_id,
        "portfolio_name": portfolio_name,
        "user_id": payload.user_id,
    }

    generator = quant_stream_generator(
        supervisor=stock_supervisor,
        inputs=inputs,
        config=config,
        session_id=session_id,
        extra_metadata=extra_metadata,
    )

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ---------------------------------------------------------------------------
# GET /quant/health
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthStatusResponse)
async def health_check():
    """
    Comprehensive health check for stock analysis system.
    Checks MCP servers (ports 8565, 8566, 8567) and agent initialization.
    """
    import socket
    from urllib.parse import urlparse

    def check_server(url):
        try:
            parsed = urlparse(url)
            host = parsed.hostname or "localhost"
            port = parsed.port
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception:
            return False

    servers_status = {
        "stock_information": check_server("http://localhost:8565/mcp"),
        "technical_analysis": check_server("http://localhost:8566/mcp"),
        "research": check_server("http://localhost:8567/mcp"),
    }

    overall_healthy = all(servers_status.values()) and agents_initialized and stock_supervisor is not None

    return HealthStatusResponse(
        status="healthy" if overall_healthy else "unhealthy",
        servers_ready=servers_status,
        agents_ready=agents_initialized,
        timestamp=datetime.now().isoformat(),
    )


# ---------------------------------------------------------------------------
# GET /quant/capabilities
# ---------------------------------------------------------------------------

@router.get("/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities():
    return CapabilitiesResponse(
        fundamental_analysis=[
            "Current stock prices and market data",
            "Historical price charts and trends",
            "Financial news and sentiment analysis",
            "Dividends, stock splits, and corporate actions",
            "Financial statements (income, balance sheet, cash flow)",
            "Analyst recommendations and price targets",
            "Holder information and institutional ownership",
            "5-year projections and growth estimates",
            "Options data and chains",
        ],
        technical_analysis=[
            "Simple Moving Average (SMA)",
            "Relative Strength Index (RSI)",
            "Bollinger Bands",
            "MACD (Moving Average Convergence Divergence)",
            "Volume analysis",
            "Support and resistance levels",
            "Comprehensive technical charting",
            "Trading signals and technical outlook",
        ],
        research_analysis=[
            "Web search for analyst ratings and news",
            "Aggregated analyst consensus and price targets",
            "Sentiment analysis of market commentary",
            "Bull case scenarios with catalysts",
            "Bear case scenarios with risks",
            "Comprehensive investment research",
            "Upgrades, downgrades, and rating changes",
        ],
        ticker_lookup=[
            "Find ticker symbols from company names",
            "Support for US and international stocks",
            "Yahoo Finance integration",
        ],
        intelligent_features=[
            "Real-time multi-agent streaming (SSE)",
            "Automatic ticker resolution from company names",
            "Context-aware conversations (remembers previous tickers)",
            "Multi-part query handling (fundamentals + technicals + research)",
            "Smart routing to specialized agents",
            "Session-based conversation memory",
            "Portfolio-linked queries",
        ],
    )


# ---------------------------------------------------------------------------
# GET /quant/sessions/{session_id}
# ---------------------------------------------------------------------------

@router.get("/sessions/{session_id}")
async def get_session_history(session_id: str):
    """Get LangGraph conversation state for a specific quant session."""
    if not agents_initialized or stock_supervisor is None:
        raise HTTPException(status_code=503, detail="Stock analysis agents not initialized.")

    try:
        state = await stock_supervisor.aget_state(
            config={"configurable": {"thread_id": session_id}}
        )
        messages = state.values.get("messages", []) if state.values else []

        serialized = [
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
            "message_count": len(serialized),
            "messages": serialized,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving session: {str(e)}")


# ---------------------------------------------------------------------------
# GET /quant/portfolio/{portfolio_id}/sessions
# ---------------------------------------------------------------------------

@router.get("/portfolio/{portfolio_id}/sessions")
async def get_portfolio_stock_sessions(
    portfolio_id: int,
    db: Session = Depends(get_db_session)
):
    """Get all stock analysis sessions linked to a portfolio."""
    portfolio = PortfolioService.get_portfolio(db, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": portfolio.name,
        "companies": portfolio.company_names,
        "message": "Stock analysis sessions for this portfolio",
    }
