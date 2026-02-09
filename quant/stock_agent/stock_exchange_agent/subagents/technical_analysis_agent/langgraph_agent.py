"""
Technical Analysis Agent - LangGraph Implementation
Handles technical analysis queries using MCP tools via LangGraph React Agent
"""
import asyncio
import aiohttp
from datetime import datetime
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain.agents import create_agent
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from dotenv import load_dotenv
import os

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
                print(f"✅ Technical Analysis MCP server is up at {url}")
                return True
        except:
            pass
        await asyncio.sleep(1)
    raise TimeoutError(f"Technical Analysis MCP server at {url} did not respond within {timeout} seconds")


async def create_technical_analysis_agent(checkpointer=None):
    """Create the Technical Analysis sub-agent with all MCP tools."""
    
    # Calculate dates dynamically
    from datetime import timedelta
    today = datetime.now().strftime("%Y-%m-%d")
    date_50_days_ago = (datetime.now() - timedelta(days=50)).strftime("%Y-%m-%d")
    date_1_year_ago = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    date_3_months_ago = (datetime.now() - timedelta(days=91)).strftime("%Y-%m-%d")
    
    system_prompt = f"""You are a technical analysis agent. Generate charts and analyze technical indicators for stocks.

TODAY'S DATE: {today}

**AVAILABLE TOOLS:**

**SINGLE STOCK ANALYSIS:**
- get_stock_sma: SMA chart (20, 100, 200, 300-day) for ONE stock
- get_stock_rsi: RSI chart with overbought/oversold levels for ONE stock
- get_stock_bollingerbands: Bollinger Bands chart for ONE stock
- get_stock_macd: MACD chart with signal line for ONE stock
- get_stock_volume: Volume analysis chart for ONE stock
- get_stock_support_resistance: Support/Resistance levels chart for ONE stock
- get_all_technical_analysis: Comprehensive chart with all indicators for ONE stock
- get_chart_summary: AI-powered chart interpretation (requires file_path)

**MULTI-STOCK COMPARISON (Use when user asks for 2+ companies):**
- get_multi_stock_sma: Compare SMA for MULTIPLE stocks on same chart
- get_multi_stock_rsi: Compare RSI for MULTIPLE stocks on same chart
- get_multi_stock_bollingerbands: Compare Bollinger Bands for MULTIPLE stocks on same chart
- get_multi_stock_macd: Compare MACD for MULTIPLE stocks on same chart
- get_multi_stock_volume: Compare Volume for MULTIPLE stocks on separate subplots

**WHEN TO USE MULTI-STOCK TOOLS:**
- User asks: "compare AAPL and MSFT SMA" → use get_multi_stock_sma with tickers=["AAPL", "MSFT"]
- User asks: "show me RSI for Tesla and Apple" → use get_multi_stock_rsi with tickers=["TSLA", "AAPL"]
- User asks: "MACD comparison of 3 tech stocks" → use get_multi_stock_macd with list of 3 tickers
- ANY request mentioning multiple companies/tickers → use appropriate multi-stock tool

**REQUIRED PARAMETERS:**
Single stock tools: ticker (string), start_date, end_date
Multi-stock tools: tickers (list of strings), start_date, end_date

**DATE HANDLING RULES:**
1. If user provides RELATIVE dates like "50 days", "3 months", "1 year", "last 6 months":
   - end_date = TODAY's date ({today})
   - start_date = TODAY minus the specified period
   - Example: "50 days" → end_date={today}, start_date={date_50_days_ago}
   - Example: "1 year" → end_date={today}, start_date={date_1_year_ago}
   - Example: "last 3 months" → end_date={today}, start_date={date_3_months_ago}

2. If user provides NO date at all (just ticker/company name):
   - ASK user to specify the time period. Do NOT assume or invent dates.

3. If user provides SPECIFIC dates:
   - Convert to YYYY-MM-DD format and use directly.

**CRITICAL RULES:**
1. NEVER use old dates like 2023 unless user explicitly requests them.
2. Call ONE tool, wait for response, then provide your analysis.
3. When tool returns "chart_generated": true, present results immediately.
4. Only report data from tool response. Do NOT invent values.
5. For multi-stock requests, ALWAYS use multi-stock tools (not single stock tools multiple times).

**RESPONSE FORMAT:**
After successful chart generation:
1. Chart location (filename)
2. Key indicator values from response
3. Brief interpretation (bullish/bearish/neutral)
4. **Data Attribution**: "Technical analysis based on historical price data | Date Range: [start_date] to [end_date] | Generated: [current timestamp]"

**DATA SOURCE ATTRIBUTION:**
- All technical indicators are calculated from historical price data
- Always state the exact date range analyzed
- Include when the analysis was generated
- Example: "Technical Analysis for AAPL | Data Period: Dec 13, 2025 - Jan 2, 2026 | Generated: Jan 2, 2026 4:00 PM | Source: Yahoo Finance historical data"

**EXAMPLES:**
User: "Show me SMA for Tesla for 50 days"
→ Calculate: end_date={today}, start_date={date_50_days_ago}
→ Call get_stock_sma(ticker="TSLA", start_date="{date_50_days_ago}", end_date="{today}")

User: "Compare RSI for AAPL and MSFT over last 3 months"
→ Calculate: end_date={today}, start_date={date_3_months_ago}
→ Call get_multi_stock_rsi(tickers=["AAPL", "MSFT"], start_date="{date_3_months_ago}", end_date="{today}")

User: "RSI for AAPL"
→ You: "Please specify the time period (e.g., '50 days', '3 months', or specific dates like 2024-01-01 to 2024-12-01)."
"""
    
    model = ChatOpenAI(model="gpt-4o", temperature=0)
    MCP_HTTP_STREAM_URL = "http://localhost:8566/mcp"  # Technical Analysis MCP server
    
    # Keep the client and session open for the lifetime of the agent
    client = streamablehttp_client(MCP_HTTP_STREAM_URL)
    read_stream, write_stream, _ = await client.__aenter__()
    session = ClientSession(read_stream, write_stream)
    await session.__aenter__()
    await session.initialize()
    tools = await load_mcp_tools(session)
    
    agent = create_agent(
        model=model,
        tools=tools,
        name="technical_analysis_agent",
        system_prompt=system_prompt,
        checkpointer=checkpointer
    )
    
    # Attach the session and client to the agent to keep them alive
    agent._mcp_session = session
    agent._mcp_client = client
    
    return agent
