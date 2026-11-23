"""
Stock Analysis Agent Service
Manages initialization and lifecycle of the stock analysis multi-agent system
"""
import asyncio
import socket
from app.services import mcp_registry
from app.services.quant_runtime import RoleChatModel
import logging
from urllib.parse import urlparse
from typing import Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
import os
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger("app.services.stock_agent")

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
                logger.info(f"MCP server is up at {url}")
                return True
        except:
            pass
        await asyncio.sleep(1)

    logger.warning(f"WARNING: MCP server at {url} did not respond within {timeout} seconds")
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
        logger.info("Stock agents already initialized, reusing...")
        return _stock_supervisor, True

    if checkpointer is None:
        logger.error("ERROR: initialize_stock_agents() requires a shared checkpointer (none was provided)")
        _agents_initialized = False
        return None, False

    try:
        logger.info("\nInitializing Stock Analysis Supervisor Agent...")
        logger.info("="*70)

        # Shared checkpointer (same Postgres-backed saver as the RAG graph) —
        # already set up (tables created) by the caller, nothing to do here.
        _stock_saver = checkpointer

        # Wait for MCP servers to be ready (with timeout)
        logger.info("Checking MCP servers...")
        servers = [(m["url"], m["label"]) for m in mcp_registry.all_servers() if m["enabled"] and m["key"] != "postgres"]
        
        server_status = {}
        servers_ready = []
        for url, name in servers:
            ready = await wait_for_server(url, timeout=5)
            server_status[name] = ready
            servers_ready.append(ready)
            if not ready:
                logger.warning(f"WARNING: {name} server not responding at {url}")

        if not all(servers_ready):
            logger.warning("WARNING: Some MCP servers are not ready. Stock analysis may have limited functionality.")
            logger.warning("   To enable full functionality, start the MCP servers:")
            for m in mcp_registry.all_servers():
                logger.warning("   - %s: %s", m["label"], m["url"])
        
        # Import here to avoid circular dependencies
        try:
            from quant.stock_agent.stock_exchange_agent.subagents.stock_information.langgraph_agent import create_stock_information_agent
            from quant.stock_agent.stock_exchange_agent.subagents.technical_analysis_agent.langgraph_agent import create_technical_analysis_agent
            from quant.stock_agent.stock_exchange_agent.subagents.ticker_finder_tool.langgraph_agent import create_ticker_finder_agent
            from quant.stock_agent.stock_exchange_agent.subagents.research_agent.langgraph_agent import create_research_agent
            from quant.stock_agent.stock_exchange_agent.subagents.options_agent.langgraph_agent import create_options_agent
            from quant.stock_agent.stock_exchange_agent.subagents.postgres_agent.langgraph_agent import create_postgres_agent
            from quant.stock_agent.stock_exchange_agent.subagents.documents_agent.langgraph_agent import create_documents_agent
            from quant.stock_agent.stock_exchange_agent.subagents.custom_tools_agent.langgraph_agent import create_custom_tools_agent
            from quant.stock_agent.stock_exchange_agent.subagents.data_sources_agent.langgraph_agent import create_data_sources_agent
            from langgraph_supervisor import create_supervisor
        except ImportError as e:
            logger.error(f"ERROR: Failed to import stock agent modules: {e}")
            logger.error("   Make sure the quant/ directory structure is correct")
            import traceback
            traceback.print_exc()
            raise

        # Create sub-agents with error handling for each
        logger.info("Creating sub-agents...")
        agents_created = []
        
        # Stock Information Agent
        if server_status.get("Stock Information"):
            try:
                logger.info("   Creating stock_information_agent...")
                stock_info_agent = await create_stock_information_agent(checkpointer=_stock_saver)
                agents_created.append("stock_info")
                logger.info("   stock_information_agent created")
            except Exception as e:
                logger.warning(f"   WARNING: Failed to create stock_information_agent: {e}")
                stock_info_agent = None
        else:
            logger.info("   Skipping stock_information_agent (MCP server not ready)")
            stock_info_agent = None
        
        # Technical Analysis Agent
        if server_status.get("Technical Analysis"):
            try:
                logger.info("   Creating technical_analysis_agent...")
                technical_agent = await create_technical_analysis_agent(checkpointer=_stock_saver)
                agents_created.append("technical")
                logger.info("   technical_analysis_agent created")
            except Exception as e:
                logger.warning(f"   WARNING: Failed to create technical_analysis_agent: {e}")
                logger.warning(f"   -> Technical Analysis MCP server may not be running (port 8566)")
                technical_agent = None
        else:
            logger.info("   Skipping technical_analysis_agent (MCP server not ready)")
            technical_agent = None
        
        # Ticker Finder Agent (doesn't need MCP server)
        try:
            logger.info("   Creating ticker_finder_agent...")
            ticker_finder = await create_ticker_finder_agent(checkpointer=_stock_saver)
            agents_created.append("ticker_finder")
            logger.info("   ticker_finder_agent created")
        except Exception as e:
            logger.warning(f"   WARNING: Failed to create ticker_finder_agent: {e}")
            ticker_finder = None
        
        # Research Agent
        if server_status.get("Research"):
            try:
                logger.info("   Creating research_agent...")
                research_agent = await create_research_agent(checkpointer=_stock_saver)
                agents_created.append("research")
                logger.info("   research_agent created")
            except Exception as e:
                logger.warning(f"   WARNING: Failed to create research_agent: {e}")
                logger.warning(f"   -> Research MCP server may not be running (port 8567)")
                research_agent = None
        else:
            logger.info("   Skipping research_agent (MCP server not ready)")
            research_agent = None

        # Options Intelligence Agent
        if server_status.get("Options Intelligence"):
            try:
                logger.info("   Creating options_intelligence_agent...")
                options_agent = await create_options_agent(checkpointer=_stock_saver)
                agents_created.append("options_intelligence")
                logger.info("   options_intelligence_agent created")
            except Exception as e:
                logger.warning(f"   WARNING: Failed to create options_intelligence_agent: {e}")
                logger.warning(f"   -> Options Intelligence MCP server may not be running (port 8568)")
                options_agent = None
        else:
            logger.info("   Skipping options_intelligence_agent (MCP server not ready)")
            options_agent = None

        # Postgres Agent
        try:
            logger.info("   Creating postgres_agent...")
            postgres_agent = await create_postgres_agent(checkpointer=_stock_saver)
            agents_created.append("postgres")
            logger.info("   postgres_agent created")
        except Exception as e:
            logger.warning(f"   WARNING: Failed to create postgres_agent: {e}")
            postgres_agent = None

        # Documents Agent (the user's uploaded files; runs in this process, no MCP server)
        try:
            documents_agent = await create_documents_agent(checkpointer=_stock_saver)
            agents_created.append("documents")
        except Exception as e:
            logger.warning(f"   WARNING: Failed to create documents_agent: {e}")
            documents_agent = None

        # Your Tools Agent (MCP servers each user connected themselves; resolved per request, so one agent serves everyone)
        try:
            custom_tools_agent = await create_custom_tools_agent(checkpointer=_stock_saver)
            agents_created.append("custom_tools")
        except Exception as e:
            logger.warning(f"   WARNING: Failed to create custom_tools_agent: {e}")
            custom_tools_agent = None

        # Data Sources Agent (market-data providers each user connected: Alpha Vantage, Polygon, Finnhub, ...; resolved per request)
        try:
            data_sources_agent = await create_data_sources_agent(checkpointer=_stock_saver)
            agents_created.append("data_sources")
        except Exception as e:
            logger.warning(f"   WARNING: Failed to create data_sources_agent: {e}")
            data_sources_agent = None

        logger.info(f"Created {len(agents_created)}/9 sub-agents: {', '.join(agents_created)}")
        
        # Check if we have at least one working agent
        available_agents = [a for a in [stock_info_agent, technical_agent, ticker_finder, research_agent, options_agent, postgres_agent, documents_agent, custom_tools_agent, data_sources_agent] if a is not None]
        
        if len(available_agents) == 0:
            logger.error("ERROR: No agents were successfully created")
            logger.error("   Stock analysis system cannot be initialized")
            _agents_initialized = False
            return None, False

        logger.info(f"\nInitializing supervisor with {len(available_agents)} available agents...")
        
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
        if postgres_agent:
            agent_names.append("postgres_agent")
        if documents_agent:
            agent_names.append("documents_agent")
        if custom_tools_agent:
            agent_names.append("custom_tools_agent")
        if data_sources_agent:
            agent_names.append("data_sources_agent")

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
- postgres_agent: Analyzes user's private PostgreSQL database, schema, and executes read-only SQL queries
- documents_agent: The user's OWN uploaded files (statements, broker notes, models, scans, decks): search them, list them, read their tables

- custom_tools_agent: Servers the user connected themselves (their own MCP tools: internal models, data feeds, other systems)

- data_sources_agent: Market data from providers the user connected (Alpha Vantage, Polygon, Finnhub, Twelve Data, Financial Modeling Prep, or their own API)

ROUTING RULES:
- The user names one of their connected data providers ("use my Alpha Vantage / Polygon / ... key", "from my data source"), or asks for data from a provider the built-in agents do not cover -> data_sources_agent (if available); it says plainly when none is connected
- The user's own connected tools / servers / "my tool X" / their internal systems -> custom_tools_agent (if available); it says plainly when the user has connected none
- Company name mentioned -> ticker_finder_agent FIRST (if available)
- A ticker lookup is only a STEP, never the answer. As soon as ticker_finder_agent returns a ticker, immediately call the transfer tool for the specialist agent that handles the user's ACTUAL request, in the same turn. Do not stop after the ticker lookup
- Price/financials/news/fundamentals -> stock_information_agent (if available)
- Charts/indicators/technical analysis -> technical_analysis_agent (if available)
- Analyst opinions/research/scenarios -> research_agent (if available)
- Options chain / put-call ratio / max pain / OI distribution / open interest analysis /
  smart money options / unusual options activity / options positioning -> options_intelligence_agent (if available)
- "my database", "SQL", "query my data", "table", "schema", private postgres data -> postgres_agent (if available)
- "my document", "my file", "the model I uploaded", "my broker note", "my statement", "in the PDF/spreadsheet I gave you" -> documents_agent (if available). To compare an uploaded model with live market data, use documents_agent for the figures first, then the market agents; always keep the document's stated units
- CROSS-AGENT SYNERGY: If a user asks to query their database for tickers/companies and then analyze them, route to postgres_agent FIRST, then hand off the results to the appropriate stock/research agents.
- MISSING DATA / MISMATCH: If an agent cannot find matching data (e.g. portfolio companies do not match database tickers), ALWAYS POLITELY ASK THE USER a clarifying question to resolve the mismatch or ask them how they want to proceed, rather than just stating an error occurred.
- If an agent is not available, apologize and suggest alternatives

CRITICAL:
- ONLY use agents that are in the available list above
- NEVER say you have transferred or will transfer a request unless you actually call the transfer tool in that same step. If you intend to hand off, call the tool; do not write about it instead
- Each new user request is independent of earlier turns: route it fresh, even if the same ticker or agent appeared before
- If user requests unavailable service, explain it's temporarily unavailable
- Never invent data - only report what agents return
- Preserve all source attribution with dates and URLs"""
        
        supervisor_graph = create_supervisor(
            model=RoleChatModel(role="quant_supervisor"),  # follows the requesting user's settings
            agents=available_agents,
            prompt=supervisor_prompt,
            add_handoff_back_messages=True,
            output_mode="full_history",
        )
        _stock_supervisor = supervisor_graph.compile(checkpointer=_stock_saver)
        
        # Set recursion limit to prevent infinite loops
        # (the per-run limit comes from the user's settings, passed in the run config by the API)
        
        _agents_initialized = True
        logger.info("Stock Analysis Supervisor initialized successfully")
        logger.info(f"   Active agents: {', '.join(agent_names)}")
        logger.info("="*70 + "\n")
        
        return _stock_supervisor, True
        
    except Exception as e:
        logger.error(f"ERROR: Failed to initialize stock agents: {str(e)}")
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
    logger.info("Stock agents cleaned up")


def get_stock_supervisor():
    """Get the global stock supervisor instance"""
    return _stock_supervisor


def is_agents_initialized() -> bool:
    """Check if stock agents are initialized"""
    return _agents_initialized
