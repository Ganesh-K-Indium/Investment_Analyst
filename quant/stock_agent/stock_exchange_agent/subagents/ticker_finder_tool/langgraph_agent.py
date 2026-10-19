"""
Ticker Finder Agent - LangGraph Implementation
Finds stock ticker symbols from company names using Tavily search
"""
import asyncio
from langchain_openai import ChatOpenAI
from langchain_community.tools import TavilySearchResults
from langchain.agents import create_agent
from dotenv import load_dotenv
import os
import warnings
from langchain_core._api import LangChainDeprecationWarning

warnings.filterwarnings("ignore", category=LangChainDeprecationWarning)

load_dotenv()


async def create_ticker_finder_agent(checkpointer=None):
    """Create the Ticker Finder sub-agent using Tavily search."""
    system_prompt = """You are a ticker finder agent. Convert company names to stock ticker symbols.

**YOUR ONLY TASK:**
Search Yahoo Finance and return the stock ticker symbol for each company named in the request.

**RULES:**
1. Search using: "site:finance.yahoo.com [company name] stock ticker"
2. Return ONLY the ticker symbol (e.g., "AAPL", "TSLA", "MSFT")
3. Do NOT provide explanations or additional information
4. Prefer US-listed stocks and primary listings
5. If the request names MORE THAN ONE company (e.g., "Sanofi and Johnson & Johnson", "Apple vs Microsoft"), split it into separate companies and return one ticker per company, comma-separated, in the order given (e.g., "SNY, JNJ")
6. Only return TICKER_NOT_FOUND for a company you genuinely cannot identify. Never return it because the request contains several companies or uses "and", "&", "vs" or commas to join them
7. If some companies are found and others are not, return the found tickers and append "TICKER_NOT_FOUND: [company] not found" for the missing ones

**EXAMPLES:**
- "Apple" → "AAPL"
- "Tesla" → "TSLA"
- "Microsoft" → "MSFT"
- "Alphabet" → "GOOGL"
- "Sanofi and Johnson & Johnson" → "SNY, JNJ"
- "Apple vs Microsoft" → "AAPL, MSFT"
- "Unknown Company" → "TICKER_NOT_FOUND: Company not found on Yahoo Finance"

**INTERNATIONAL STOCKS:**
Include exchange suffix if specified (e.g., "0700.HK" for Tencent Hong Kong)

Your response must be ONLY the ticker symbol(s), nothing else."""
    
    model = ChatOpenAI(model="gpt-4o", temperature=0)
    
    # Create Tavily search tool configured for Yahoo Finance
    tavily_tool = TavilySearchResults(
        max_results=5,
        search_depth="advanced",
        include_answer=True,
        include_raw_content=False,
        include_images=False,
        include_domains=["finance.yahoo.com"],
    )
    
    agent = create_agent(
        model=model,
        tools=[tavily_tool],
        name="ticker_finder_agent",
        system_prompt=system_prompt,
        checkpointer=checkpointer
    )
    
    return agent
