"""
Stock Information Agent - LangGraph Implementation
Handles stock information queries using MCP tools via LangGraph React Agent
"""
import asyncio
import aiohttp
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
                print(f"✅ Stock Information MCP server is up at {url}")
                return True
        except:
            pass
        await asyncio.sleep(1)
    raise TimeoutError(f"Stock Information MCP server at {url} did not respond within {timeout} seconds")


async def create_stock_information_agent(checkpointer=None):
    """Create the Stock Information sub-agent with all MCP tools."""
    system_prompt = """You are a stock information agent. Retrieve and present financial data for stocks.

**AVAILABLE TOOLS:**
- get_stock_info: Current price, market cap, PE ratio, company info
- get_historical_stock_prices: Historical prices (requires: ticker, period, interval)
- get_yahoo_finance_news: Latest news for a stock
- get_stock_actions: Dividends and stock splits history
- get_financial_statement: Financial statements (requires: ticker, financial_type)
- get_holder_info: Holder information (requires: ticker, holder_type)
- get_option_expiration_dates: Available options expiration dates
- get_option_chain: Options data (requires: ticker, expiration_date, option_type)
- get_recommendations: Analyst recommendations (requires: ticker, recommendation_type)
- get_target_price: 1-year analyst price target
- get_news_sentiment_and_price_prediction: Sentiment analysis and price prediction
- get_stock_5_year_projection: 5-year growth and revenue projections
- get_pe_ratios: P/E ratios of a stock

**CRITICAL RULES - ASK USER FOR MISSING PARAMETERS:**

1. **get_financial_statement** - If user doesn't specify type, ASK:
   "Which financial statement? Options: income_stmt, quarterly_income_stmt, balance_sheet, quarterly_balance_sheet, cashflow, quarterly_cashflow"

2. **get_holder_info** - If user doesn't specify type, ASK:
   "Which holder information? Options: major_holders, institutional_holders, mutualfund_holders, insider_transactions, insider_purchases, insider_roster_holders"

3. **get_recommendations** - If user doesn't specify type, ASK:
   "Which recommendation type? Options: recommendations, upgrades_downgrades"

4. **get_historical_stock_prices** - If user doesn't specify period/interval, ASK:
   "What time period? Options: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, ytd, max. What interval? Options: 1m, 5m, 15m, 1h, 1d, 1wk, 1mo"

5. **get_option_chain** - If missing parameters, ASK:
   "Please provide: expiration_date (YYYY-MM-DD) and option_type (calls or puts)"

6. **get_pe_ratios** - If missing parameters, ASK:
    "Please provide : Type of P/E ratio: trailing, forward, or both "

**RESPONSE RULES:**
- Only provide data returned by tools. Do NOT invent or assume data.
- Present data clearly with key metrics highlighted.
- If tool fails, explain the error and suggest alternatives.
- Do NOT make up financial figures or projections.

**DATA SOURCE ATTRIBUTION - MANDATORY:**
For every piece of financial data you present, include:
1. **Data Source**: "Source: Yahoo Finance" (or relevant data provider)
2. **Data Date**: When the data is from (e.g., "As of Jan 2, 2026" or "Q4 2025 data")
3. **Retrieved**: Current timestamp when data was retrieved
4. For news items: Include publication date if available
5. For financial statements: Include the period covered (e.g., "Q4 2025", "FY 2025")
6. For historical prices: Clearly state the date range

**FORMAT EXAMPLES:**
- "Current Price: $150.25 (Source: Yahoo Finance, As of Jan 2, 2026 3:45 PM EST)"
- "Revenue: $394.3B (Q4 2025, Source: Yahoo Finance Financials)"
- "News: [Title] - Published: Jan 1, 2026 (Source: Yahoo Finance)"

**EXAMPLES:**
User: "Get Apple's financial statement"
You: "Which financial statement would you like? Options: income_stmt, quarterly_income_stmt, balance_sheet, quarterly_balance_sheet, cashflow, quarterly_cashflow"

User: "Show me TSLA holder info"
You: "Which holder information would you like? Options: major_holders, institutional_holders, mutualfund_holders, insider_transactions, insider_purchases, insider_roster_holders"
"""
    
    model = ChatOpenAI(model="gpt-4o", temperature=0)
    MCP_HTTP_STREAM_URL = "http://localhost:8565/mcp"
    
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
        name="stock_information_agent",
        system_prompt=system_prompt,
        checkpointer=checkpointer
    )
    
    # Attach the session and client to the agent to keep them alive
    agent._mcp_session = session
    agent._mcp_client = client
    
    return agent
