"""
Quant Stock Analysis API endpoints with chat persistence
Integrates the multi-agent stock analysis system into the main API
"""
import logging
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from langchain_core.messages import HumanMessage
from app.database.connection import get_db_session
from app.core.paths import OUTPUT_DIR
from app.services.portfolio import PortfolioService
from app.services.chat import ChatService
from app.database.models import AgentType, MessageRole, User, ChatSession
from app.auth.deps import get_current_user, verify_user_id_matches, verify_owner
from app.utils.time import to_iso_z
from app.utils.usage_tracker import track_usage
from app.services import app_settings, quant_runtime, usage_ledger, mcp_registry, llm_providers, run_context
from app.services.app_settings import store as settings_store
from app.services.graph_stream import stream_graph
from datetime import datetime
import json
import os

logger = logging.getLogger("api.quant")
router = APIRouter(prefix="/quant", tags=["Quant Analysis"])

# Debug-only: dump full multi-agent response payloads to output/json/quant/ on
# every query. Off by default — was previously unconditional, growing that
# directory without bound on every request in any environment.
_SAVE_DEBUG_RESPONSES = os.getenv("SAVE_DEBUG_RESPONSES", "false").lower() == "true"


# Pydantic Models
class StockQueryRequest(BaseModel):
    query: str = Field(..., description="Stock analysis query")
    portfolio_id: Optional[int] = Field(None, description="Optional portfolio ID to link query")
    user_id: str = Field(..., description="User identifier")
    session_id: Optional[str] = Field(None, description="Session ID for conversation continuity")
    postgres_integration_id: Optional[int] = Field(None, description="Optional ID of the PostgreSQL integration to use as a data source")


class StockQueryResponse(BaseModel):
    response: str
    session_id: str
    portfolio_id: Optional[int]
    timestamp: str
    success: bool
    agent_used: Optional[str]
    metadata: Optional[Dict[str, Any]]


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


# Global references (set by main app during startup)
stock_supervisor = None
agents_initialized = False


def set_stock_supervisor(supervisor_instance):
    """Set the global stock supervisor instance"""
    global stock_supervisor
    stock_supervisor = supervisor_instance


def set_agents_status(status: bool):
    """Set the agents initialization status"""
    global agents_initialized
    agents_initialized = status


@router.post("/query", response_model=StockQueryResponse)
async def query_stock_agent(
    payload: StockQueryRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Send a query to the stock analysis supervisor agent.

    The agent can:
    - Analyze stock fundamentals (prices, financials, news)
    - Perform technical analysis (RSI, SMA, MACD, etc.)
    - Research analyst ratings and sentiment
    - Find ticker symbols from company names

    Maintains conversation context per session_id.
    """
    return await query_core(payload, background_tasks, db, current_user)


async def query_core(
    payload: StockQueryRequest,
    background_tasks: Optional[BackgroundTasks],
    db: AsyncSession,
    current_user: User,
    emit=None,
) -> StockQueryResponse:
    """Core /quant/query logic, shared by the HTTP route and the background job runner."""
    verify_user_id_matches(payload.user_id, current_user)

    if not agents_initialized or stock_supervisor is None:
        raise HTTPException(
            status_code=503,
            detail="Stock analysis agents not initialized. Please check system status."
        )

    try:
        # Generate session ID if not provided
        session_id = payload.session_id
        if not session_id:
            if payload.portfolio_id:
                # Link to portfolio if provided
                portfolio = await PortfolioService.get_portfolio(db, payload.portfolio_id)
                if portfolio:
                    session_id = f"quant_portfolio_{payload.portfolio_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                else:
                    session_id = f"quant_{payload.user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            else:
                session_id = f"quant_{payload.user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        logger.info("Processing stock query: session=%s user=%s portfolio=%s query=%.100s",
                    session_id, payload.user_id, payload.portfolio_id, payload.query)

        # Create or get chat session for persistence
        portfolio_id = payload.portfolio_id
        portfolio_name = None
        if portfolio_id:
            portfolio = await PortfolioService.get_portfolio(db, portfolio_id)
            if portfolio:
                verify_owner(portfolio.user_id, current_user)
                portfolio_name = portfolio.name

        chat_session = await ChatService.create_or_get_chat_session(
            db=db,
            session_id=session_id,
            user_id=payload.user_id,
            agent_type=AgentType.QUANT,
            portfolio_id=portfolio_id,
            title=f"Stock Analysis: {portfolio_name}" if portfolio_name else "Stock Analysis"
        )
        # If session_id referenced a PRE-EXISTING session, guard against it
        # belonging to a different user (create_or_get returns the existing
        # row rather than creating a new one in that case).
        verify_owner(chat_session.user_id, current_user)

        # Save user message
        await ChatService.add_message(
            db=db,
            session_id=session_id,
            role=MessageRole.USER,
            content=payload.query
        )
        
        # Get the current state to know how many messages exist
        current_state = await stock_supervisor.aget_state(
            config={"configurable": {"thread_id": f"quant:{session_id}"}}
        )
        messages_before = len(current_state.values.get('messages', [])) if current_state.values else 0
        
        # Inject context (Postgres ID)
        final_query = payload.query
        context_tags = []
        
        if payload.postgres_integration_id:
            from app.services.integration import IntegrationService
            from app.services.mcp_auth import sign_integration
            integ = await IntegrationService.get_integration(db, payload.postgres_integration_id)
            if integ is None or str(integ.user_id) != str(current_user.id):
                raise HTTPException(status_code=403, detail="That database connection does not belong to you.")
            # the model gets a signed token, not a bare id: the Postgres tool verifies it and the owner, so it cannot be pointed at someone else's database
            context_tags.append(f"use integration_id={sign_integration(current_user.id, integ.id)}")
            
        if context_tags:
            context_str = " | ".join(context_tags)
            final_query = f"{payload.query}\n\n[SYSTEM: {context_str}]"

        # The user's settings: models per agent, enabled agents, history window, risk profile, step limit
        settings, creds = await run_context.load(db, current_user.id, payload.portfolio_id)
        run_context.preflight(settings, creds, run_context.QUANT_ROLES)
        final_query = f"{final_query}\n\n{quant_runtime.request_preamble(settings)}"

        # Release the pooled connection for the length of the agent run (see rag._run_agent_tracked); expire_on_commit=False
        # keeps the objects loaded above usable.
        await db.commit()

        # Invoke supervisor with thread_id for memory persistence
        with track_usage(f"quant:{session_id}") as tracker, app_settings.use(settings), llm_providers.use_credentials(creds):
            run_inputs = {"messages": [HumanMessage(content=final_query)]}
            run_config = {"configurable": {"thread_id": f"quant:{session_id}", "user_id": str(current_user.id)},
                          "recursion_limit": settings["quant"]["recursion_limit"]}
            if emit is None:
                response = await stock_supervisor.ainvoke(run_inputs, config=run_config)
            else:
                response = await stream_graph(stock_supervisor, run_inputs, run_config, emit)
        try:
            usage = await usage_ledger.persist(db, current_user.id, tracker)
        except Exception:
            logger.exception("Could not store quant usage")
            usage = None
        
        # Extract only NEW messages from this turn
        all_messages = response['messages']
        new_messages = all_messages[messages_before:] if messages_before > 0 else all_messages
        
        # Find the last AI message from the new messages that is not a transfer/handoff
        final_message = None
        agent_used = None
        for msg in reversed(new_messages):
            if (msg.type == 'ai' and 
                msg.name != 'supervisor' and 
                not msg.content.startswith('Transferring back') and 
                not msg.content.startswith('Successfully transferred')):
                final_message = msg
                agent_used = getattr(msg, 'name', None)
                break
        
        # Fallback to last new message if no suitable AI message found
        if final_message is None and new_messages:
            final_message = new_messages[-1]
        elif final_message is None:
            final_message = all_messages[-1]
        
        # Save assistant message with metadata
        await ChatService.add_message(
            db=db,
            session_id=session_id,
            role=MessageRole.ASSISTANT,
            content=final_message.content,
            metadata={
                "agent_used": agent_used,
                "portfolio_id": payload.portfolio_id,
                "message_count": len(all_messages),
                "new_messages": len(new_messages)
            }
        )
        
        if _SAVE_DEBUG_RESPONSES and background_tasks is not None:
            background_tasks.add_task(
                save_quant_response,
                response,
                session_id,
                payload.user_id,
                payload.portfolio_id
            )

        return StockQueryResponse(
            response=final_message.content,
            session_id=session_id,
            portfolio_id=payload.portfolio_id,
            timestamp=to_iso_z(datetime.utcnow()),
            success=True,
            agent_used=agent_used,
            metadata={
                "message_count": len(all_messages),
                "new_messages": len(new_messages),
                "usage": usage,
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error processing stock query: %s", e)
        raise HTTPException(status_code=500, detail=f"Error processing request: {str(e)}")


@router.post("/query/stream")
async def stream_stock_query(payload: StockQueryRequest, db: AsyncSession = Depends(get_db_session), current_user: User = Depends(get_current_user)):
    """Server-sent events for a quant query: {"type":"step","node"} as each agent finishes, tokens, then {"type":"done","result"} or {"type":"error"}.
    Runs on the interactive worker when one is up (same events, see app/jobs/chat_stream.py), otherwise in this process."""
    import asyncio
    from fastapi.responses import StreamingResponse
    from app.jobs.chat_stream import stream_via_worker, KIND_QUANT
    relayed = await stream_via_worker(KIND_QUANT, payload.model_dump(), current_user.id)
    if relayed is not None:
        return relayed

    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event):
        await queue.put(event)

    async def run():
        try:
            result = await query_core(payload, None, db, current_user, emit=emit)
            await queue.put({"type": "done", "result": result.model_dump()})
        except HTTPException as e:
            await queue.put({"type": "error", "status": e.status_code, "detail": e.detail})
        except Exception as e:  # noqa: BLE001
            logger.exception("Streaming quant query failed")
            await queue.put({"type": "error", "status": 500, "detail": str(e)})
        finally:
            await queue.put(None)

    task = asyncio.create_task(run())

    async def events():
        try:
            while True:
                ev = await queue.get()
                if ev is None:
                    break
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/health", response_model=HealthStatusResponse)
async def health_check():
    """
    Comprehensive health check for stock analysis system.
    
    Checks:
    - All MCP servers are responding (Stock Info, Technical Analysis, Research)
    - Agents are initialized
    - Supervisor agent is ready
    """
    import socket
    from urllib.parse import urlparse
    
    def check_server(url):
        """Check if a server is responding on its port"""
        try:
            parsed = urlparse(url)
            host = parsed.hostname or 'localhost'
            port = parsed.port
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception as e:
            logger.warning("Server check failed for %s: %s", url, e)
            return False
    
    # Check every MCP server the registry lists (including Options and Postgres, which the old check skipped)
    servers_status = {m["key"]: check_server(m["url"]) for m in mcp_registry.all_servers() if m["enabled"]}
    
    # Determine overall health
    all_servers_ready = all(servers_status.values())
    overall_healthy = all_servers_ready and agents_initialized and stock_supervisor is not None
    
    status = "healthy" if overall_healthy else "unhealthy"
    
    return HealthStatusResponse(
        status=status,
        servers_ready=servers_status,
        agents_ready=agents_initialized,
        timestamp=datetime.now().isoformat()
    )


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities():
    """Get information about available stock analysis capabilities"""
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
            "Options data and chains"
        ],
        technical_analysis=[
            "Simple Moving Average (SMA)",
            "Relative Strength Index (RSI)",
            "Bollinger Bands",
            "MACD (Moving Average Convergence Divergence)",
            "Volume analysis",
            "Support and resistance levels",
            "Comprehensive technical charting",
            "Trading signals and technical outlook"
        ],
        research_analysis=[
            "Web search for analyst ratings and news",
            "Aggregated analyst consensus and price targets",
            "Sentiment analysis of market commentary",
            "Bull case scenarios with catalysts",
            "Bear case scenarios with risks",
            "Comprehensive investment research",
            "Upgrades, downgrades, and rating changes"
        ],
        ticker_lookup=[
            "Find ticker symbols from company names",
            "Support for US and international stocks",
            "Yahoo Finance integration"
        ],
        intelligent_features=[
            "Automatic ticker resolution from company names",
            "Context-aware conversations (remembers previous tickers)",
            "Multi-part query handling (fundamentals + technicals + research)",
            "Smart routing to specialized agents",
            "Session-based conversation memory",
            "Portfolio-linked queries"
        ]
    )


@router.get("/sessions/{session_id}")
async def get_session_history(
    session_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Get conversation history for a specific stock analysis session"""
    if not agents_initialized or stock_supervisor is None:
        raise HTTPException(
            status_code=503,
            detail="Stock analysis agents not initialized."
        )

    result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
    chat_session = result.scalar_one_or_none()
    if not chat_session:
        raise HTTPException(status_code=404, detail="Session not found")
    verify_owner(chat_session.user_id, current_user)

    try:
        state = await stock_supervisor.aget_state(
            config={"configurable": {"thread_id": f"quant:{session_id}"}}
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
            "messages": serialized_messages
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving session: {str(e)}"
        )


@router.get("/portfolio/{portfolio_id}/sessions")
async def get_portfolio_stock_sessions(
    portfolio_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Get all stock analysis sessions linked to a portfolio"""
    # Verify portfolio exists
    portfolio = await PortfolioService.get_portfolio(db, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    verify_owner(portfolio.user_id, current_user)


    # This would require a database table to track quant sessions
    # For now, return portfolio info
    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": portfolio.name,
        "companies": portfolio.company_names,
        "message": "Stock analysis sessions for this portfolio"
    }


def save_quant_response(response, session_id: str, user_id: str, portfolio_id: Optional[int]):
    """Save stock analysis response to JSON file"""
    try:
        def serialize_response(obj):
            try:
                if isinstance(obj, dict):
                    return {k: serialize_response(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [serialize_response(item) for item in obj]
                elif isinstance(obj, (str, int, float, bool, type(None))):
                    return obj
                elif hasattr(obj, 'dict') and callable(getattr(obj, 'dict', None)):
                    return obj.model_dump()
                elif hasattr(obj, '__dict__'):
                    return serialize_response(obj.__dict__)
                else:
                    return str(obj)
            except Exception:
                return str(obj)
        
        # Save to output/json/quant directory
        responses_dir = str(OUTPUT_DIR / "json" / "quant")
        os.makedirs(responses_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"quant_{session_id}_{timestamp}.json"
        filepath = os.path.join(responses_dir, filename)
        
        response_data = {
            "session_id": session_id,
            "user_id": user_id,
            "portfolio_id": portfolio_id,
            "timestamp": timestamp,
            "response": serialize_response(response)
        }
        
        with open(filepath, "w") as f:
            json.dump(response_data, f, indent=4)
        
        logger.info("Stock analysis response saved to %s", filepath)
    except Exception as e:
        logger.error("Failed to save quant response: %s", e)
