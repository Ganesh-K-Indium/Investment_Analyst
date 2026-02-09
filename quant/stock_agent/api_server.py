"""
FastAPI Server for Stock Analysis Supervisor Agent
Exposes the supervisor agent functionality via REST API
"""

import asyncio
import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any
import json
import os
from datetime import datetime
from contextlib import asynccontextmanager

# Import the existing agent functionality
from main_agent import (
    wait_for_server,
)
from stock_exchange_agent.subagents.stock_information.langgraph_agent import create_stock_information_agent
from stock_exchange_agent.subagents.technical_analysis_agent.langgraph_agent import create_technical_analysis_agent
from stock_exchange_agent.subagents.ticker_finder_tool.langgraph_agent import create_ticker_finder_agent
from stock_exchange_agent.subagents.research_agent.langgraph_agent import create_research_agent

from langchain_openai import ChatOpenAI
from langgraph_supervisor import create_supervisor
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langchain_core.messages import HumanMessage

# Pydantic models for API requests/responses
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    response: str
    session_id: str
    timestamp: str
    success: bool

class StatusResponse(BaseModel):
    status: str
    servers_ready: Dict[str, bool]
    agents_ready: bool
    timestamp: str

# Global variables for agent management
supervisor = None
agents_initialized = False
saver = None
saver_cm = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await initialize_agents()
    yield
    # Shutdown
    global saver_cm
    if saver_cm is not None:
        try:
            await saver_cm.__aexit__(None, None, None)
            print("✅ Memory saver cleaned up successfully")
        except Exception as e:
            print(f"⚠️ Error cleaning up memory saver: {e}")

# FastAPI app
app = FastAPI(
    title="Stock Analysis Supervisor API",
    description="REST API for the Stock Analysis Supervisor Agent with LangGraph",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure this for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def initialize_agents():
    """Initialize all agents and supervisor on startup"""
    global supervisor, agents_initialized, saver, saver_cm

    if agents_initialized:
        return

    try:
        print("🚀 Initializing Stock Analysis Supervisor Agent...")
        
        # Initialize memory saver
        print("💾 Initializing SQLite memory...")
        db_path = os.getenv("SQLITE_DB_PATH", "sqlite:///checkpoints.db")
        print(f"📍 Using database path: {db_path}")
        
        try:
            saver_cm = AsyncSqliteSaver.from_conn_string(db_path)
            print("🔗 Created AsyncSqliteSaver context manager")
            saver = await saver_cm.__aenter__()
            print("✅ Entered saver context manager")
            await saver.setup()  # Creates tables if needed
            print("✅ Memory initialized successfully")
        except Exception as saver_error:
            print(f"❌ Failed to initialize saver: {str(saver_error)}")
            print(f"❌ Database path: {db_path}")
            import traceback
            traceback.print_exc()
            raise

        # Wait for MCP servers to be ready
        print("⏳ Waiting for MCP servers...")
        await wait_for_server("http://localhost:8565/mcp")  # Stock Information
        await wait_for_server("http://localhost:8566/mcp")  # Technical Analysis
        await wait_for_server("http://localhost:8567/mcp")  # Research

        # Create sub-agents
        print("🔧 Creating sub-agents...")
        stock_info_agent = await create_stock_information_agent(checkpointer=saver)
        technical_agent = await create_technical_analysis_agent(checkpointer=saver)
        ticker_finder = await create_ticker_finder_agent(checkpointer=saver)
        research_agent = await create_research_agent(checkpointer=saver)

        print("✅ Sub-agents created successfully")

        # Create supervisor
        supervisor_prompt = """You are a stock analysis supervisor managing 4 agents:

AGENTS:
1. ticker_finder_agent - Converts company names to ticker symbols
2. stock_information_agent - Fundamental data (prices, financials, news, statements, holders, options)
3. technical_analysis_agent - Technical charts (SMA, RSI, MACD, Bollinger, Volume, Support/Resistance)
4. research_agent - Analyst ratings, web research, sentiment, bull/bear scenarios

ROUTING RULES:
- Company name mentioned → ticker_finder_agent FIRST
- Price/financials/news/fundamentals → stock_information_agent  
- Charts/indicators/technical analysis → technical_analysis_agent
- Analyst opinions/research/scenarios → research_agent

CRITICAL:
- If user doesn't provide required info (dates, parameters), ASK before delegating
- One agent at a time, wait for completion
- Remember ticker from conversation - don't re-lookup
- Never invent data - only report what agents return
- Combine multi-agent responses into coherent answer"""

        global supervisor
        supervisor_graph = create_supervisor(
            model=ChatOpenAI(temperature=0, model_name="gpt-4o"),
            agents=[stock_info_agent, technical_agent, ticker_finder, research_agent],
            prompt=supervisor_prompt,
            add_handoff_back_messages=True,
            output_mode="full_history",
        )
        supervisor = supervisor_graph.compile(checkpointer=saver)
        
        # Set recursion limit for the supervisor to prevent infinite loops
        supervisor.recursion_limit = 50

        agents_initialized = True
        print("✅ Supervisor agent initialized successfully")

    except Exception as e:
        print(f"❌ Failed to initialize agents: {str(e)}")
        import traceback
        traceback.print_exc()
        raise


@app.get("/", tags=["Health"])
async def root():
    """Root endpoint"""
    return {"message": "Stock Analysis Supervisor API", "status": "running", "version": "1.0.0"}


@app.get("/health", response_model=StatusResponse, tags=["Health"])
async def health_check():
    """
    Comprehensive health check for the API and all MCP servers.
    
    Checks:
    - All MCP servers are responding (Stock Info, Technical Analysis, Research)
    - Agents are initialized
    - Database connectivity (PostgreSQL)
    - Supervisor agent is ready
    
    Returns:
    - status: "healthy" or "unhealthy"
    - servers_ready: Status of each MCP server
    - agents_ready: Whether supervisor and sub-agents are initialized
    - timestamp: ISO formatted timestamp
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
            print(f"❌ Server check failed for {url}: {str(e)}")
            return False

    # Check all MCP servers
    servers_status = {
        "stock_information": check_server("http://localhost:8565/mcp"),
        "technical_analysis": check_server("http://localhost:8566/mcp"),
        "research": check_server("http://localhost:8567/mcp"),
    }

    # Determine overall health
    all_servers_ready = all(servers_status.values())
    overall_healthy = all_servers_ready and agents_initialized and supervisor is not None

    status = "healthy" if overall_healthy else "unhealthy"

    return StatusResponse(
        status=status,
        servers_ready=servers_status,
        agents_ready=agents_initialized,
        timestamp=datetime.now().isoformat()
    )


@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat_with_agent(request: ChatRequest, background_tasks: BackgroundTasks):
    """
    Send a message to the supervisor agent and get a response.
    
    The agent maintains conversation context per session_id.
    If no session_id is provided, a new one will be generated.
    """

    if not agents_initialized or supervisor is None:
        raise HTTPException(
            status_code=503,
            detail="Agents not initialized. Please check server status at /health endpoint."
        )

    try:
        # Generate session ID if not provided
        session_id = request.session_id or f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        print(f"🧠 Processing request for session {session_id}: {request.message[:100]}...")

        # Get the current state to know how many messages exist
        current_state = await supervisor.aget_state(config={"configurable": {"thread_id": session_id}})
        messages_before = len(current_state.values.get('messages', [])) if current_state.values else 0

        # Invoke supervisor with thread_id for memory persistence
        response = await supervisor.ainvoke(
            {"messages": [HumanMessage(content=request.message)]},
            config={"configurable": {"thread_id": session_id}}
        )

        # Extract only NEW messages from this turn
        all_messages = response['messages']
        new_messages = all_messages[messages_before:] if messages_before > 0 else all_messages
        
        # Find the last AI message from the new messages that is not a transfer/handoff
        final_message = None
        for msg in reversed(new_messages):
            if msg.type == 'ai' and msg.name != 'supervisor' and not msg.content.startswith('Transferring back') and not msg.content.startswith('Successfully transferred'):
                final_message = msg
                break
        
        # Fallback to last new message if no suitable AI message found
        if final_message is None and new_messages:
            final_message = new_messages[-1]
        elif final_message is None:
            final_message = all_messages[-1]

        # Save response to file in background
        background_tasks.add_task(save_response_to_file, response, session_id)

        return ChatResponse(
            response=final_message.content,
            session_id=session_id,
            timestamp=datetime.now().isoformat(),
            success=True
        )

    except Exception as e:
        print(f"❌ Error processing request: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error processing request: {str(e)}")


def save_response_to_file(response, session_id):
    """Save response to JSON file"""
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

        responses_dir = os.path.join(os.path.dirname(__file__), "responses")
        os.makedirs(responses_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"api_response_{session_id}_{timestamp}.json"
        filepath = os.path.join(responses_dir, filename)
        with open(filepath, "w") as f:
            json.dump(serialize_response(response), f, indent=4)
        print(f"📁 API response saved to {filepath}")
    except Exception as e:
        print(f"❌ Failed to save response: {str(e)}")


@app.get("/capabilities", tags=["Info"])
async def get_capabilities():
    """Get information about available capabilities"""
    return {
        "fundamental_analysis": [
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
        "technical_analysis": [
            "Simple Moving Average (SMA)",
            "Relative Strength Index (RSI)",
            "Bollinger Bands",
            "MACD (Moving Average Convergence Divergence)",
            "Volume analysis",
            "Support and resistance levels",
            "Comprehensive technical charting",
            "Trading signals and technical outlook"
        ],
        "ticker_lookup": [
            "Find ticker symbols from company names",
            "Support for US and international stocks",
            "Yahoo Finance integration"
        ],
        "intelligent_features": [
            "Automatic ticker resolution from company names",
            "Context-aware conversations (remembers previous tickers)",
            "Multi-part query handling (fundamentals + technicals)",
            "Smart routing to specialized agents",
            "Session-based conversation memory"
        ]
    }


@app.get("/sessions/{session_id}", tags=["Sessions"])
async def get_session_history(session_id: str):
    """Get conversation history for a specific session"""
    if not agents_initialized or supervisor is None:
        raise HTTPException(
            status_code=503,
            detail="Agents not initialized."
        )
    
    try:
        state = await supervisor.aget_state(config={"configurable": {"thread_id": session_id}})
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
        raise HTTPException(status_code=500, detail=f"Error retrieving session: {str(e)}")


if __name__ == "__main__":
    print("🚀 Starting Stock Analysis Supervisor API Server...")
    print("📍 API will be available at: http://localhost:8567")
    print("📖 API documentation at: http://localhost:8567/docs")

    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=8568,
        reload=False,
        log_level="info"
    )
