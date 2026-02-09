"""
Quant Stock Analysis API endpoints with chat persistence
Integrates the multi-agent stock analysis system into the main API
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from langchain_core.messages import HumanMessage
from app.database.connection import get_db_session
from app.services.portfolio import PortfolioService
from app.services.chat import ChatService
from app.database.models import AgentType, MessageRole
from datetime import datetime
import json
import os

router = APIRouter(prefix="/quant", tags=["Quant Analysis"])


# Pydantic Models
class StockQueryRequest(BaseModel):
    query: str = Field(..., description="Stock analysis query")
    portfolio_id: Optional[int] = Field(None, description="Optional portfolio ID to link query")
    user_id: str = Field(..., description="User identifier")
    session_id: Optional[str] = Field(None, description="Session ID for conversation continuity")


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
    db: Session = Depends(get_db_session)
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
                portfolio = PortfolioService.get_portfolio(db, payload.portfolio_id)
                if portfolio:
                    session_id = f"quant_portfolio_{payload.portfolio_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                else:
                    session_id = f"quant_{payload.user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            else:
                session_id = f"quant_{payload.user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        print(f"Processing stock query for session {session_id}")
        print(f"   User: {payload.user_id}")
        print(f"   Portfolio: {payload.portfolio_id}")
        print(f"   Query: {payload.query[:100]}...")
        
        # Create or get chat session for persistence
        portfolio_id = payload.portfolio_id
        portfolio_name = None
        if portfolio_id:
            portfolio = PortfolioService.get_portfolio(db, portfolio_id)
            if portfolio:
                portfolio_name = portfolio.name
        
        chat_session = ChatService.create_or_get_chat_session(
            db=db,
            session_id=session_id,
            user_id=payload.user_id,
            agent_type=AgentType.QUANT,
            portfolio_id=portfolio_id,
            title=f"Stock Analysis: {portfolio_name}" if portfolio_name else "Stock Analysis"
        )
        
        # Save user message
        ChatService.add_message(
            db=db,
            session_id=session_id,
            role=MessageRole.USER,
            content=payload.query
        )
        
        # Get the current state to know how many messages exist
        current_state = await stock_supervisor.aget_state(
            config={"configurable": {"thread_id": session_id}}
        )
        messages_before = len(current_state.values.get('messages', [])) if current_state.values else 0
        
        # Invoke supervisor with thread_id for memory persistence
        response = await stock_supervisor.ainvoke(
            {"messages": [HumanMessage(content=payload.query)]},
            config={"configurable": {"thread_id": session_id}}
        )
        
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
        ChatService.add_message(
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
        
        print(f"Chat persisted to database")
        
        # Save response to file in background
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
            timestamp=datetime.now().isoformat(),
            success=True,
            agent_used=agent_used,
            metadata={
                "message_count": len(all_messages),
                "new_messages": len(new_messages)
            }
        )
        
    except Exception as e:
        print(f"ERROR: Error processing stock query: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error processing request: {str(e)}")


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
            print(f"ERROR: Server check failed for {url}: {str(e)}")
            return False
    
    # Check all MCP servers
    servers_status = {
        "stock_information": check_server("http://localhost:8565/mcp"),
        "technical_analysis": check_server("http://localhost:8566/mcp"),
        "research": check_server("http://localhost:8567/mcp"),
    }
    
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
async def get_session_history(session_id: str):
    """Get conversation history for a specific stock analysis session"""
    if not agents_initialized or stock_supervisor is None:
        raise HTTPException(
            status_code=503,
            detail="Stock analysis agents not initialized."
        )
    
    try:
        state = await stock_supervisor.aget_state(
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
    db: Session = Depends(get_db_session)
):
    """Get all stock analysis sessions linked to a portfolio"""
    # Verify portfolio exists
    portfolio = PortfolioService.get_portfolio(db, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    
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
        responses_dir = "output/json/quant"
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
        
        print(f"📁 Stock analysis response saved to {filepath}")
    except Exception as e:
        print(f"ERROR: Failed to save quant response: {str(e)}")
