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
Search Yahoo Finance and return the stock ticker symbol for the given company name.

**RULES:**
1. Search using: "site:finance.yahoo.com [company name] stock ticker"
2. Return ONLY the ticker symbol (e.g., "AAPL", "TSLA", "MSFT")
3. Do NOT provide explanations or additional information
4. Prefer US-listed stocks and primary listings
5. If not found, return: "TICKER_NOT_FOUND: [reason]"

**EXAMPLES:**
- "Apple" → "AAPL"
- "Tesla" → "TSLA"  
- "Microsoft" → "MSFT"
- "Alphabet" → "GOOGL"
- "Unknown Company" → "TICKER_NOT_FOUND: Company not found on Yahoo Finance"

**INTERNATIONAL STOCKS:**
Include exchange suffix if specified (e.g., "0700.HK" for Tencent Hong Kong)

Your response must be ONLY the ticker symbol, nothing else."""
    
    model = ChatOpenAI(model="gpt-4o", temperature=0)
    
    # Create Tavily search tool configured for Yahoo Finance
    tavily_tool = TavilySearchResults(
        max_results=5,
        search_depth="advanced",
        include_answer=True,
        include_raw_content=False,
        include_images=False,
        include_domains=["https://finance.yahoo.com/"],
    )
    
    agent = create_agent(
        model=model,
        tools=[tavily_tool],
        name="ticker_finder_agent",
        system_prompt=system_prompt,
        checkpointer=checkpointer
    )
    
    return agent
