"""
Stock Analysis Agent Service
Manages initialization and lifecycle of the stock analysis multi-agent system
"""
import asyncio
import socket
from urllib.parse import urlparse
from typing import Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
import os
from dotenv import load_dotenv
load_dotenv()

# Global references
_stock_supervisor = None
_stock_saver = None
_agents_initialized = False


async def wait_for_server(url: str, timeout: int = 10) -> bool:
    """
    Wait until the MCP server is ready to accept connections.
    
    Args:
        url: Server URL to check
        timeout: Maximum wait time in seconds
        
    Returns:
        True if server is ready, False if timeout
    """
    import time
    
    parsed = urlparse(url)
    host = parsed.hostname or 'localhost'
    port = parsed.port
    
    start = time.time()
    while time.time() - start < timeout:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                print(f"MCP server is up at {url}")
                return True
        except:
            pass
        await asyncio.sleep(1)
    
    print(f"WARNING: MCP server at {url} did not respond within {timeout} seconds")
    return False


async def initialize_stock_agents(checkpointer: Optional[BaseCheckpointSaver] = None):
    """
    Initialize the stock analysis supervisor and sub-agents.

    Args:
        checkpointer: Shared LangGraph checkpointer (the same Postgres-backed
            saver used by the RAG graph — see app/main.py). Required; the
            stock supervisor no longer creates its own separate SQLite store.

    Returns:
        Tuple of (supervisor, initialized_status)
    """
    global _stock_supervisor, _stock_saver, _agents_initialized

    if _agents_initialized and _stock_supervisor is not None:
        print("Stock agents already initialized, reusing...")
        return _stock_supervisor, True

    if checkpointer is None:
        print("ERROR: initialize_stock_agents() requires a shared checkpointer (none was provided)")
        _agents_initialized = False
        return None, False

    try:
        print("\nInitializing Stock Analysis Supervisor Agent...")
        print("="*70)

        # Shared checkpointer (same Postgres-backed saver as the RAG graph) —
        # already set up (tables created) by the caller, nothing to do here.
        _stock_saver = checkpointer

        # Wait for MCP servers to be ready (with timeout)
        print("Checking MCP servers...")
        servers = [
            ("http://localhost:8565/mcp", "Stock Information"),
            ("http://localhost:8566/mcp", "Technical Analysis"),
            ("http://localhost:8567/mcp", "Research"),
            ("http://localhost:8568/mcp", "Options Intelligence"),
        ]
        
        server_status = {}
        servers_ready = []
        for url, name in servers:
            ready = await wait_for_server(url, timeout=5)
            server_status[name] = ready
            servers_ready.append(ready)
            if not ready:
                print(f"WARNING: {name} server not responding at {url}")
        
        if not all(servers_ready):
            print("WARNING: Some MCP servers are not ready. Stock analysis may have limited functionality.")
            print("   To enable full functionality, start the MCP servers:")
            print("   - Stock Information: port 8565")
            print("   - Technical Analysis: port 8566")
            print("   - Research: port 8567")
            print("   - Options Intelligence: port 8568")
        
        # Import here to avoid circular dependencies
        try:
            import sys
            # Add quant directory to Python path if not already there
            quant_dir = os.path.join(os.getcwd(), 'quant', 'stock_agent')
            if quant_dir not in sys.path:
                sys.path.insert(0, quant_dir)

            from stock_exchange_agent.subagents.stock_information.langgraph_agent import create_stock_information_agent
            from stock_exchange_agent.subagents.technical_analysis_agent.langgraph_agent import create_technical_analysis_agent
            from stock_exchange_agent.subagents.ticker_finder_tool.langgraph_agent import create_ticker_finder_agent
            from stock_exchange_agent.subagents.research_agent.langgraph_agent import create_research_agent
            from stock_exchange_agent.subagents.options_agent.langgraph_agent import create_options_agent
            from langgraph_supervisor import create_supervisor
        except ImportError as e:
            print(f"ERROR: Failed to import stock agent modules: {e}")
            print("   Make sure the quant/ directory structure is correct")
            print(f"   Tried to add: {quant_dir}")
            import traceback
            traceback.print_exc()
            raise
        
        # Create sub-agents with error handling for each
        print("Creating sub-agents...")
        agents_created = []
        
        # Stock Information Agent
        if server_status.get("Stock Information"):
            try:
                print("   Creating stock_information_agent...")
                stock_info_agent = await create_stock_information_agent(checkpointer=_stock_saver)
                agents_created.append("stock_info")
                print("   stock_information_agent created")
            except Exception as e:
                print(f"   WARNING: Failed to create stock_information_agent: {e}")
                stock_info_agent = None
        else:
            print("   Skipping stock_information_agent (MCP server not ready)")
            stock_info_agent = None
        
        # Technical Analysis Agent
        if server_status.get("Technical Analysis"):
            try:
                print("   Creating technical_analysis_agent...")
                technical_agent = await create_technical_analysis_agent(checkpointer=_stock_saver)
                agents_created.append("technical")
                print("   technical_analysis_agent created")
            except Exception as e:
                print(f"   WARNING: Failed to create technical_analysis_agent: {e}")
                print(f"   -> Technical Analysis MCP server may not be running (port 8566)")
                technical_agent = None
        else:
            print("   Skipping technical_analysis_agent (MCP server not ready)")
            technical_agent = None
        
        # Ticker Finder Agent (doesn't need MCP server)
        try:
            print("   Creating ticker_finder_agent...")
            ticker_finder = await create_ticker_finder_agent(checkpointer=_stock_saver)
            agents_created.append("ticker_finder")
            print("   ticker_finder_agent created")
        except Exception as e:
            print(f"   WARNING: Failed to create ticker_finder_agent: {e}")
            ticker_finder = None
        
        # Research Agent
        if server_status.get("Research"):
            try:
                print("   Creating research_agent...")
                research_agent = await create_research_agent(checkpointer=_stock_saver)
                agents_created.append("research")
                print("   research_agent created")
            except Exception as e:
                print(f"   WARNING: Failed to create research_agent: {e}")
                print(f"   -> Research MCP server may not be running (port 8567)")
                research_agent = None
        else:
            print("   Skipping research_agent (MCP server not ready)")
            research_agent = None

        # Options Intelligence Agent
        if server_status.get("Options Intelligence"):
            try:
                print("   Creating options_intelligence_agent...")
                options_agent = await create_options_agent(checkpointer=_stock_saver)
                agents_created.append("options_intelligence")
                print("   options_intelligence_agent created")
            except Exception as e:
                print(f"   WARNING: Failed to create options_intelligence_agent: {e}")
                print(f"   -> Options Intelligence MCP server may not be running (port 8568)")
                options_agent = None
        else:
            print("   Skipping options_intelligence_agent (MCP server not ready)")
            options_agent = None

        print(f"Created {len(agents_created)}/5 sub-agents: {', '.join(agents_created)}")
        
        # Check if we have at least one working agent
        available_agents = [a for a in [stock_info_agent, technical_agent, ticker_finder, research_agent, options_agent] if a is not None]
        
        if len(available_agents) == 0:
            print("ERROR: No agents were successfully created")
            print("   Stock analysis system cannot be initialized")
            _agents_initialized = False
            return None, False
        
        print(f"\nInitializing supervisor with {len(available_agents)} available agents...")
        
        # Create supervisor with only available agents
        agent_names = []
        if stock_info_agent:
            agent_names.append("stock_information_agent")
        if technical_agent:
            agent_names.append("technical_analysis_agent")
        if ticker_finder:
            agent_names.append("ticker_finder_agent")
        if research_agent:
            agent_names.append("research_agent")
        if options_agent:
            agent_names.append("options_intelligence_agent")

        supervisor_prompt = f"""You are a stock analysis supervisor managing {len(available_agents)} agents:

AVAILABLE AGENTS:
{chr(10).join([f"{i+1}. {name}" for i, name in enumerate(agent_names)])}

CAPABILITIES:
- ticker_finder_agent: Converts company names to ticker symbols
- stock_information_agent: Fundamental data (prices, financials, news, statements, holders)
- technical_analysis_agent: Technical charts (SMA, RSI, MACD, Bollinger, Volume, Support/Resistance, Candlestick)
- research_agent: Analyst ratings, web research, sentiment, bull/bear scenarios
- options_intelligence_agent: Options chain analysis — put/call ratio, OI concentration zones, max pain,
  smart money signals, unusual activity, support/resistance from OI, OI visualization charts

ROUTING RULES:
- Company name mentioned -> ticker_finder_agent FIRST (if available)
- Price/financials/news/fundamentals -> stock_information_agent (if available)
- Charts/indicators/technical analysis -> technical_analysis_agent (if available)
- Analyst opinions/research/scenarios -> research_agent (if available)
- Options chain / put-call ratio / max pain / OI distribution / open interest analysis /
  smart money options / unusual options activity / options positioning -> options_intelligence_agent (if available)
- If an agent is not available, apologize and suggest alternatives

CRITICAL:
- ONLY use agents that are in the available list above
- If user requests unavailable service, explain it's temporarily unavailable
- Never invent data - only report what agents return
- Preserve all source attribution with dates and URLs"""
        
        supervisor_graph = create_supervisor(
            model=ChatOpenAI(temperature=0, model_name="gpt-4o"),
            agents=available_agents,
            prompt=supervisor_prompt,
            add_handoff_back_messages=True,
            output_mode="full_history",
        )
        _stock_supervisor = supervisor_graph.compile(checkpointer=_stock_saver)
        
        # Set recursion limit to prevent infinite loops
        _stock_supervisor.recursion_limit = 50
        
        _agents_initialized = True
        print("Stock Analysis Supervisor initialized successfully")
        print(f"   Active agents: {', '.join(agent_names)}")
        print("="*70 + "\n")
        
        return _stock_supervisor, True
        
    except Exception as e:
        print(f"ERROR: Failed to initialize stock agents: {str(e)}")
        import traceback
        traceback.print_exc()
        _agents_initialized = False
        return None, False


async def cleanup_stock_agents():
    """
    Cleanup stock agent resources. The checkpointer itself is shared with the
    RAG graph and owned by app/main.py's startup/shutdown lifecycle — this
    only resets this module's references, it does not close the checkpointer.
    """
    global _stock_supervisor, _stock_saver, _agents_initialized

    _stock_supervisor = None
    _stock_saver = None
    _agents_initialized = False
    print("Stock agents cleaned up")


def get_stock_supervisor():
    """Get the global stock supervisor instance"""
    return _stock_supervisor


def is_agents_initialized() -> bool:
    """Check if stock agents are initialized"""
    return _agents_initialized
