"""
Main LangGraph Supervisor Agent for Stock Analysis
---------------------------------------------------
Manages Stock Information, Technical Analysis, and Ticker Finder agents as specialized sub-agents.
Uses langgraph-supervisor to coordinate work between agents.
"""

import asyncio
import aiohttp
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.tools import load_mcp_tools

from langgraph_supervisor import create_supervisor
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from dotenv import load_dotenv
from stock_exchange_agent.subagents.stock_information.langgraph_agent import create_stock_information_agent
from stock_exchange_agent.subagents.technical_analysis_agent.langgraph_agent import create_technical_analysis_agent
from stock_exchange_agent.subagents.ticker_finder_tool.langgraph_agent import create_ticker_finder_agent
from stock_exchange_agent.subagents.research_agent.langgraph_agent import create_research_agent
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import os
from datetime import datetime
import json

load_dotenv()


async def wait_for_server(url: str, timeout: int = 10):
    """Wait until the MCP server is ready to accept connections."""
    import time
    import socket
    from urllib.parse import urlparse
    
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
                print(f"✅ MCP server is up at {url}")
                return True
        except:
            pass
        await asyncio.sleep(1)
    raise TimeoutError(f"MCP server at {url} did not respond within {timeout} seconds")


async def main():
    """Main supervisor agent that coordinates stock analysis sub-agents."""
    
    print("🚀 Initializing Stock Analysis Supervisor Agent...")
    print("=" * 80)
    
    # Initialize memory saver
    print("💾 Initializing SQLite memory...")
    db_path = os.getenv("SQLITE_DB_PATH", "sqlite:///checkpoints.db")
    
    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        await saver.setup()  # Creates tables if needed
        print("✅ Memory initialized successfully")
        
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
    
        supervisor_graph = create_supervisor(
            model=ChatOpenAI(temperature=0, model_name="gpt-4o"),
            agents=[stock_info_agent, technical_agent, ticker_finder, research_agent],
            prompt=(
                "You are a supervisor managing four stock analysis agents. Route user requests to the appropriate agent.\n\n"
                "**AGENTS:**\n"
                "1. **ticker_finder_agent**: Converts company names to ticker symbols. Use FIRST when user provides a company name.\n"
                "2. **stock_information_agent**: Stock prices, financials, news, dividends, holder info, recommendations, options, projections.\n"
                "3. **technical_analysis_agent**: Charts and technical indicators (SMA, RSI, MACD, Bollinger Bands, Volume, Support/Resistance).\n"
                "4. **research_agent**: Web research, analyst ratings, sentiment analysis, bull/bear scenarios.\n\n"
                "**ROUTING RULES:**\n"
                "- Company name (Apple, Tesla) → ticker_finder_agent FIRST, then route to specialist\n"
                "- Ticker symbol provided (AAPL, TSLA) → Route directly to specialist\n"
                "- Price/financials/news/dividends/holders/options → stock_information_agent\n"
                "- Charts/RSI/SMA/MACD/Bollinger/technical → technical_analysis_agent\n"
                "- Analyst ratings/research/scenarios/sentiment → research_agent\n\n"
                "**CRITICAL RULES:**\n"
                "1. Delegate to ONE agent at a time. Wait for response before next delegation.\n"
                "2. Do NOT make up stock data. Only present what agents return.\n"
                "3. If agent asks for more info (dates, parameters), relay that to user.\n"
                "4. Remember ticker from conversation - don't re-lookup unless company changes.\n"
                "5. For multi-part queries, delegate sequentially and combine results.\n"
                "6. Do NOT invent prices, percentages, or recommendations.\n"
                "7. PRESERVE ALL SOURCE ATTRIBUTION: Agents return data with sources, dates, and timestamps. Pass these through COMPLETELY to the user.\n"
                "8. For investment data, ALWAYS include: source name, data date, published date (if available)\n"
                "9. When presenting agent responses, maintain ALL source URLs, dates, and attribution exactly as provided.\n\n"
                "**DATA INTEGRITY FOR INVESTMENT DECISIONS:**\n"
                "- Every data point must be traceable to its source\n"
                "- Always show when data was published and when it was retrieved\n"
                "- Alert users if data is older than 7 days\n"
                "- Never combine data from different time periods without clear labels\n\n"
                "**EXAMPLES:**\n"
                "User: 'Apple stock price' → ticker_finder_agent → stock_information_agent\n"
                "User: 'TSLA RSI chart' → technical_analysis_agent (ticker already provided)\n"
                "User: 'What do analysts think about NVDA?' → research_agent\n"
                "User: 'Show me RSI for Netflix' (no dates) → Agent will ask for date range, relay to user"
            ),
            add_handoff_back_messages=True,
            output_mode="full_history",
        )
        supervisor = supervisor_graph.compile(
            checkpointer=saver,
        )
        
        # Set recursion limit for the supervisor to prevent infinite loops
        supervisor.recursion_limit = 50
        
        print("\n" + "="*80)
        print("🤖 STOCK ANALYSIS SUPERVISOR AGENT - Ready for Commands")
        print("="*80)
        print("\n📋 What I can help you with:")
        print("\n📊 FUNDAMENTAL ANALYSIS:")
        print("  • Current stock prices and market data")
        print("  • Historical price charts and trends")
        print("  • Financial news and sentiment analysis")
        print("  • Dividends, stock splits, and corporate actions")
        print("  • Financial statements and company financials")
        print("  • Analyst recommendations and price targets")
        print("  • Holder information and institutional ownership")
        print("  • 5-year projections and growth estimates")
        
        print("\n📈 TECHNICAL ANALYSIS:")
        print("  • Moving averages (SMA, EMA)")
        print("  • RSI and momentum indicators")
        print("  • Bollinger Bands and volatility")
        print("  • MACD and trend analysis")
        print("  • Volume analysis")
        print("  • Support and resistance levels")
        print("  • Comprehensive technical charting")
        
        print("\n🔬 RESEARCH & SCENARIOS:")
        print("  • Web search for analyst ratings and news")
        print("  • Aggregated analyst consensus and price targets")
        print("  • Sentiment analysis of market commentary")
        print("  • Bull case scenarios with catalysts")
        print("  • Bear case scenarios with risks")
        print("  • Comprehensive investment research")
        print("  • Upgrades, downgrades, and rating changes")
        
        print("\n🔍 TICKER LOOKUP:")
        print("  • Find ticker symbols from company names")
        print("  • Support for US and international stocks")
        
        print("\n🤖 INTELLIGENT FEATURES:")
        print("  • Automatic ticker resolution from company names")
        print("  • Context-aware conversation (remembers previous tickers)")
        print("  • Multi-part query handling (fundamentals + technicals + research)")
        print("  • Smart routing to specialized agents")
        
        print("\nEnter your command (or 'quit' to exit): ")
        
        while True:
            try:
                user_input = input("\n>>> ").strip()
                if user_input.lower() in ['quit', 'exit', 'q']:
                    print("👋 Goodbye!")
                    break
                
                if not user_input:
                    continue
                
                print(f"\n🧠 Processing: {user_input}")
                print("-" * 50)
                
                # Get the current state to know how many messages exist
                current_state = await supervisor.aget_state(config={"configurable": {"thread_id": "main_thread"}})
                messages_before = len(current_state.values.get('messages', [])) if current_state.values else 0
                
                # Invoke supervisor with thread_id for memory persistence
                response = await supervisor.ainvoke(
                    {"messages": [HumanMessage(content=user_input)]},
                    config={"configurable": {"thread_id": "main_thread"}}
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
                
                print("\n🤖 Response:")
                print(final_message.content)

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
                filename = f"response_{timestamp}.json"
                filepath = os.path.join(responses_dir, filename)
                with open(filepath, "w") as f:
                    json.dump(serialize_response(response), f, indent=4)
                print(f"📁 Response saved to {filepath}")
                
            except KeyboardInterrupt:
                print("\n👋 Goodbye!")
                break
            except Exception as e:
                print(f"\n❌ Error: {str(e)}")
                import traceback
                traceback.print_exc()
                continue
        
        print("💾 Memory saved successfully")


if __name__ == "__main__":
    asyncio.run(main())
