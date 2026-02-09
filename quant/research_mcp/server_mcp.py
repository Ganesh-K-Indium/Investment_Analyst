"""
Research MCP Server - Web Search, Analyst Ratings, and Scenario Generation
---------------------------------------------------------------------------
Provides tools for researching stocks via web search, scraping analyst ratings,
generating bull/bear scenarios, sentiment analysis, and summarization.
"""

import os
import json
import asyncio
import traceback
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from collections import defaultdict

from fastmcp import FastMCP
from dotenv import load_dotenv

# External APIs
from tavily import TavilyClient
from openai import AsyncOpenAI
import aiohttp
from textblob import TextBlob

load_dotenv()

# Initialize FastMCP server
research_server = FastMCP(
    "research",
    instructions="""
    # Stock Research MCP Server
    
    This server provides comprehensive stock research capabilities including:
    - Web search for analyst ratings and stock news
    - Analyst rating aggregation and normalization
    - Bull/Bear scenario generation
    - Sentiment analysis
    - Content summarization
    - Intelligent caching
    
    Available tools:
        - `web_search`: Search the web for stock-related information using Tavily
        - `search_analyst_ratings`: Search specifically for analyst ratings and price targets
        - `scrape_ratings_data`: Extract and normalize ratings from multiple sources
        - `aggregate_ratings`: Combine and normalize ratings from various sources
        - `analyze_sentiment`: Analyze sentiment of text content
        - `summarize_content`: Summarize long articles or content
        - `generate_scenarios`: Generate bull and bear scenarios for a stock
        - `get_cached_research`: Retrieve cached research data
        - `comprehensive_research`: Full research pipeline for a stock
    """
)

# ============================================================================
# CACHE SYSTEM
# ============================================================================

class ResearchCache:
    """Simple in-memory cache with TTL support."""
    
    def __init__(self, default_ttl: int = 3600):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._default_ttl = default_ttl
    
    def _make_key(self, prefix: str, *args) -> str:
        """Generate a cache key from prefix and arguments."""
        key_data = f"{prefix}:{':'.join(str(a) for a in args)}"
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Get item from cache if not expired."""
        if key in self._cache:
            item = self._cache[key]
            if datetime.now() < item['expires_at']:
                return item['data']
            else:
                del self._cache[key]
        return None
    
    def set(self, key: str, data: Any, ttl: Optional[int] = None) -> None:
        """Store item in cache with TTL."""
        ttl = ttl or self._default_ttl
        self._cache[key] = {
            'data': data,
            'expires_at': datetime.now() + timedelta(seconds=ttl),
            'created_at': datetime.now().isoformat()
        }
    
    def clear(self) -> None:
        """Clear all cached items."""
        self._cache.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        now = datetime.now()
        valid_items = sum(1 for item in self._cache.values() if now < item['expires_at'])
        return {
            'total_items': len(self._cache),
            'valid_items': valid_items,
            'expired_items': len(self._cache) - valid_items
        }


# Global cache instance
_cache = ResearchCache(default_ttl=3600)  # 1 hour default TTL


# ============================================================================
# RATING DATA STRUCTURES
# ============================================================================

@dataclass
class AnalystRating:
    """Normalized analyst rating structure."""
    source: str
    analyst: Optional[str]
    firm: Optional[str]
    rating: str  # buy, hold, sell, strong_buy, strong_sell
    rating_numeric: float  # 1-5 scale (1=strong sell, 5=strong buy)
    target_price: Optional[float]
    previous_target: Optional[float]
    date: str
    action: Optional[str]  # upgrade, downgrade, maintain, initiate
    summary: Optional[str]


def normalize_rating(rating_text: str) -> tuple:
    """
    Normalize rating text to canonical format.
    Returns (normalized_rating, numeric_score).
    """
    if rating_text is None or not rating_text:
        rating_text = "hold"
    rating_lower = rating_text.lower().strip()
    
    # Strong Buy mappings
    strong_buy_terms = ['strong buy', 'outperform', 'overweight', 'conviction buy', 
                        'top pick', 'aggressive buy', 'accumulate']
    # Buy mappings
    buy_terms = ['buy', 'positive', 'market outperform', 'sector outperform',
                 'add', 'attractive']
    # Hold mappings  
    hold_terms = ['hold', 'neutral', 'market perform', 'sector perform', 
                  'equal weight', 'in-line', 'peer perform', 'mixed']
    # Sell mappings
    sell_terms = ['sell', 'underperform', 'underweight', 'reduce', 'negative',
                  'market underperform', 'sector underperform']
    # Strong Sell mappings
    strong_sell_terms = ['strong sell', 'avoid', 'conviction sell']
    
    for term in strong_buy_terms:
        if term in rating_lower:
            return ('strong_buy', 5.0)
    
    for term in buy_terms:
        if term in rating_lower:
            return ('buy', 4.0)
    
    for term in hold_terms:
        if term in rating_lower:
            return ('hold', 3.0)
    
    for term in sell_terms:
        if term in rating_lower:
            return ('sell', 2.0)
    
    for term in strong_sell_terms:
        if term in rating_lower:
            return ('strong_sell', 1.0)
    
    # Default to hold if unknown
    return ('hold', 3.0)


def determine_action(current_rating: str, previous_rating: Optional[str]) -> str:
    """Determine if this is an upgrade, downgrade, initiate, or maintain."""
    if previous_rating is None:
        return 'initiate'
    
    current_score = normalize_rating(current_rating)[1]
    previous_score = normalize_rating(previous_rating)[1]
    
    if current_score > previous_score:
        return 'upgrade'
    elif current_score < previous_score:
        return 'downgrade'
    else:
        return 'maintain'


# ============================================================================
# WEB SEARCH TOOL
# ============================================================================

@research_server.tool(
    name="web_search",
    description="""
    Search the web for stock-related information using Tavily search API.
    
    Args:
        query: str - The search query (e.g., "AAPL analyst ratings 2024")
        max_results: int - Maximum number of results to return (default: 10)
        search_depth: str - "basic" or "advanced" (default: "advanced")
        include_domains: list - Optional list of domains to include
        exclude_domains: list - Optional list of domains to exclude
    
    Returns:
        dict - Search results with titles, URLs, content snippets, and scores
    """
)
async def web_search(
    query: str,
    max_results: int = 10,
    search_depth: str = "advanced",
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Search the web for stock-related information."""
    try:
        # Check cache first
        cache_key = _cache._make_key("web_search", query, max_results)
        cached = _cache.get(cache_key)
        if cached:
            return {**cached, "from_cache": True}
        
        # Initialize Tavily client
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            return {
                "error": "TAVILY_API_KEY not found in environment variables",
                "success": False
            }
        
        client = TavilyClient(api_key=api_key)
        
        # Perform search
        search_params = {
            "query": query,
            "max_results": max_results,
            "search_depth": search_depth,
            "topic": "news"  # Required to get published_date field
        }
        
        if include_domains:
            search_params["include_domains"] = include_domains
        if exclude_domains:
            search_params["exclude_domains"] = exclude_domains
        
        response = client.search(**search_params)
        
        # Process results
        results = []
        sources = []
        for item in response.get("results", []):
            pub_date = item.get("published_date", "")
            if not pub_date or pub_date == "":
                pub_date = "Not provided by source"
            
            result_item = {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
                "score": item.get("score", 0),
                "published_date": pub_date
            }
            results.append(result_item)
            
            # Build sources array for easy citation
            sources.append({
                "url": result_item["url"],
                "title": result_item["title"],
                "published_date": pub_date
            })
        
        output = {
            "success": True,
            "query": query,
            "results_count": len(results),
            "results": results,
            "sources": sources,
            "search_time": datetime.now().isoformat()
        }
        
        # Cache results
        _cache.set(cache_key, output, ttl=1800)  # 30 min cache
        
        return output
        
    except Exception as e:
        return {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "success": False
        }


# ============================================================================
# ANALYST RATINGS SEARCH
# ============================================================================

# Define the actual implementation function first (without decorator)
async def _search_analyst_ratings_impl(
    symbol: str,
    company_name: Optional[str] = None,
    days_back: int = 30
) -> Dict[str, Any]:
    """Search for analyst ratings for a specific stock."""
    try:
        cache_key = _cache._make_key("analyst_ratings", symbol, days_back)
        cached = _cache.get(cache_key)
        if cached:
            return {**cached, "from_cache": True}
        
        # Build search queries
        search_name = company_name or symbol
        queries = [
            f"{symbol} analyst rating price target {datetime.now().year}",
            f"{search_name} stock upgrade downgrade analyst",
            f"{symbol} Wall Street rating recommendation",
        ]
        
        # Financial domains to prioritize
        financial_domains = [
            "benzinga.com", "marketwatch.com", "seekingalpha.com",
            "tipranks.com", "investing.com", "barrons.com",
            "fool.com", "zacks.com", "thestreet.com", "cnbc.com",
            "bloomberg.com", "reuters.com", "yahoo.com"
        ]
        
        all_results = []
        api_key = os.getenv("TAVILY_API_KEY")
        
        if not api_key:
            return {
                "error": "TAVILY_API_KEY not found",
                "success": False
            }
        
        client = TavilyClient(api_key=api_key)
        
        for query in queries:
            try:
                response = client.search(
                    query=query,
                    max_results=5,
                    search_depth="advanced",
                    topic="news",  # Required to get published_date field
                    include_domains=financial_domains
                )
                
                for item in response.get("results", []):
                    all_results.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "content": item.get("content", ""),
                        "score": item.get("score", 0),
                        "published_date": item.get("published_date", ""),
                        "source_query": query
                    })
            except Exception as e:
                continue  # Continue with other queries if one fails
        
        # Deduplicate by URL
        seen_urls = set()
        unique_results = []
        sources = []
        for r in sorted(all_results, key=lambda x: x['score'], reverse=True):
            if r['url'] not in seen_urls:
                seen_urls.add(r['url'])
                unique_results.append(r)
                # Build sources array
                pub_date = r['published_date'] if r.get('published_date') and r['published_date'] != "" else "Not provided by source"
                sources.append({
                    "url": r['url'],
                    "title": r['title'],
                    "published_date": pub_date
                })
        
        output = {
            "success": True,
            "symbol": symbol,
            "company_name": company_name,
            "results_count": len(unique_results),
            "results": unique_results[:15],  # Top 15 results
            "sources": sources[:15],
            "search_time": datetime.now().isoformat()
        }
        
        _cache.set(cache_key, output, ttl=1800)
        return output
        
    except Exception as e:
        return {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "success": False
        }

# Now wrap it with the decorator for FastMCP
@research_server.tool(
    name="search_analyst_ratings",
    description="""
    Search specifically for analyst ratings, price targets, and recommendations for a stock.
    
    Args:
        symbol: str - Stock ticker symbol (e.g., "AAPL", "TSLA")
        company_name: str - Optional company name for better search results
        days_back: int - How many days back to search (default: 30)
    
    Returns:
        dict - Analyst ratings search results from multiple financial sources
    """
)
async def search_analyst_ratings(
    symbol: str,
    company_name: Optional[str] = None,
    days_back: int = 30
) -> Dict[str, Any]:
    """Search for analyst ratings for a specific stock."""
    return await _search_analyst_ratings_impl(symbol, company_name, days_back)


# ============================================================================
# RATINGS AGGREGATOR
# ============================================================================

# Implementation function (without decorator)
async def _aggregate_ratings_impl(
    symbol: str,
    search_results: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Aggregate analyst ratings from search results."""
    try:
        # If no search results provided, fetch them
        if not search_results:
            search_data = await _search_analyst_ratings_impl(symbol)
            if not search_data.get("success"):
                return search_data
            search_results = search_data.get("results", [])
        
        # Use LLM to extract structured ratings from content
        openai_key = os.getenv("OPENAI_API_KEY")
        if not openai_key:
            return {
                "error": "OPENAI_API_KEY not found",
                "success": False
            }
        
        client = AsyncOpenAI(api_key=openai_key)
        
        # Combine content for analysis
        combined_content = "\n\n---\n\n".join([
            f"Source: {r.get('url', 'unknown')}\n{r.get('content', '')}"
            for r in search_results[:10]  # Limit to top 10
        ])
        
        extraction_prompt = f"""Analyze the following content about {symbol} stock and extract analyst ratings.

For each rating mentioned, extract:
- analyst_firm: The firm name (e.g., "Morgan Stanley", "Goldman Sachs")
- analyst_name: Individual analyst name if mentioned
- rating: The rating (e.g., "Buy", "Hold", "Sell", "Outperform", "Underweight")
- target_price: Price target if mentioned (number only)
- date: Date of the rating if mentioned (YYYY-MM-DD format)
- action: "upgrade", "downgrade", "initiate", or "maintain"
- summary: Brief one-line summary

Content:
{combined_content[:8000]}

Respond with a JSON object containing:
{{
    "ratings": [
        {{
            "analyst_firm": "...",
            "analyst_name": "...",
            "rating": "...",
            "target_price": null or number,
            "date": "...",
            "action": "...",
            "summary": "..."
        }}
    ],
    "consensus": "...",  // overall consensus: "Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"
    "average_target": null or number,
    "analyst_count": number,
    "key_insights": ["...", "..."]
}}

Only include ratings you can clearly identify from the content. Do not make up data."""

        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": extraction_prompt}],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        
        extracted = json.loads(response.choices[0].message.content)
        
        # Process and normalize ratings
        normalized_ratings = []
        rating_scores = []
        target_prices = []
        
        for r in extracted.get("ratings", []):
            rating_text = r.get("rating") or "hold"
            norm_rating, score = normalize_rating(rating_text)
            rating_scores.append(score)
            
            if r.get("target_price"):
                try:
                    target_prices.append(float(r["target_price"]))
                except:
                    pass
            
            normalized_ratings.append({
                "firm": r.get("analyst_firm"),
                "analyst": r.get("analyst_name"),
                "rating": norm_rating,
                "rating_original": rating_text,
                "rating_score": score,
                "target_price": r.get("target_price"),
                "date": r.get("date"),
                "action": r.get("action"),
                "summary": r.get("summary")
            })
        
        # Calculate aggregates
        avg_score = sum(rating_scores) / len(rating_scores) if rating_scores else 3.0
        avg_target = sum(target_prices) / len(target_prices) if target_prices else None
        
        # Determine consensus
        if avg_score >= 4.5:
            consensus = "Strong Buy"
        elif avg_score >= 3.5:
            consensus = "Buy"
        elif avg_score >= 2.5:
            consensus = "Hold"
        elif avg_score >= 1.5:
            consensus = "Sell"
        else:
            consensus = "Strong Sell"
        
        # Rating distribution
        distribution = defaultdict(int)
        for nr in normalized_ratings:
            distribution[nr["rating"]] += 1
        
        # Extract source URLs and published dates from search_results
        sources = []
        for r in search_results[:10]:
            if r.get('url'):
                pub_date = r.get('published_date', '')
                if not pub_date or pub_date == "":
                    pub_date = "Not provided by source"
                sources.append({
                    "url": r.get('url'),
                    "title": r.get('title', ''),
                    "published_date": pub_date
                })
        
        # Also maintain source_urls for backward compatibility
        source_urls = [s['url'] for s in sources]
        
        output = {
            "success": True,
            "symbol": symbol,
            "consensus": consensus,
            "consensus_score": round(avg_score, 2),
            "average_target_price": round(avg_target, 2) if avg_target else None,
            "analyst_count": len(normalized_ratings),
            "rating_distribution": dict(distribution),
            "ratings": normalized_ratings,
            "key_insights": extracted.get("key_insights", []),
            "sources": sources,
            "source_urls": source_urls,
            "aggregated_at": datetime.now().isoformat()
        }
        
        return output
        
    except Exception as e:
        return {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "success": False
        }

# Wrap with decorator
@research_server.tool(
    name="aggregate_ratings",
    description="""
    Aggregate and normalize analyst ratings from search results into a structured format.
    
    Args:
        symbol: str - Stock ticker symbol
        search_results: list - List of search results containing rating information
    
    Returns:
        dict - Aggregated ratings with consensus, average target price, and individual ratings
    """
)
async def aggregate_ratings(
    symbol: str,
    search_results: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Aggregate analyst ratings from search results."""
    return await _aggregate_ratings_impl(symbol, search_results)


# ============================================================================
# SENTIMENT ANALYSIS
# ============================================================================

# Implementation function
async def _analyze_sentiment_impl(
    text: str,
    symbol: Optional[str] = None
) -> Dict[str, Any]:
    """Analyze sentiment of text content."""
    try:
        # Use TextBlob for basic sentiment
        blob = TextBlob(text)
        polarity = blob.sentiment.polarity  # -1 to 1
        subjectivity = blob.sentiment.subjectivity  # 0 to 1
        
        # Classify sentiment
        if polarity > 0.3:
            sentiment = "bullish"
        elif polarity > 0.1:
            sentiment = "slightly_bullish"
        elif polarity > -0.1:
            sentiment = "neutral"
        elif polarity > -0.3:
            sentiment = "slightly_bearish"
        else:
            sentiment = "bearish"
        
        # Confidence based on subjectivity (more objective = more confident)
        confidence = 1 - (subjectivity * 0.5)  # Scale down subjectivity impact
        
        return {
            "success": True,
            "symbol": symbol,
            "sentiment": sentiment,
            "polarity": round(polarity, 3),
            "subjectivity": round(subjectivity, 3),
            "confidence": round(confidence, 3),
            "text_length": len(text),
            "analyzed_at": datetime.now().isoformat()
        }
        
    except Exception as e:
        return {
            "error": str(e),
            "success": False
        }

# Wrap with decorator
@research_server.tool(
    name="analyze_sentiment",
    description="""
    Analyze sentiment of text content related to a stock.
    
    Args:
        text: str - Text content to analyze
        symbol: str - Optional stock symbol for context
    
    Returns:
        dict - Sentiment analysis with polarity, subjectivity, and classification
    """
)
async def analyze_sentiment(
    text: str,
    symbol: Optional[str] = None
) -> Dict[str, Any]:
    """Analyze sentiment of text content."""
    return await _analyze_sentiment_impl(text, symbol)


# ============================================================================
# SUMMARIZER
# ============================================================================

# Implementation function
async def _summarize_content_impl(
    content: str,
    symbol: str,
    max_length: int = 200,
    focus: str = "general"
) -> Dict[str, Any]:
    """Summarize content about a stock."""
    try:
        openai_key = os.getenv("OPENAI_API_KEY")
        if not openai_key:
            return {"error": "OPENAI_API_KEY not found", "success": False}
        
        client = AsyncOpenAI(api_key=openai_key)
        
        focus_instructions = {
            "ratings": "Focus on analyst ratings, price targets, upgrades, and downgrades.",
            "news": "Focus on recent news, events, and developments.",
            "analysis": "Focus on analysis, predictions, and market outlook.",
            "general": "Provide a balanced overview of all relevant information."
        }
        
        prompt = f"""Summarize the following content about {symbol} stock.
{focus_instructions.get(focus, focus_instructions['general'])}

Keep the summary under {max_length} words. Extract key points as bullet points.

Content:
{content[:10000]}

Respond with a JSON object:
{{
    "summary": "Concise paragraph summary...",
    "key_points": ["Point 1", "Point 2", ...],
    "sentiment_indication": "bullish" | "bearish" | "neutral",
    "notable_events": ["Event 1", ...]
}}"""

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3
        )
        
        result = json.loads(response.choices[0].message.content)
        
        return {
            "success": True,
            "symbol": symbol,
            "summary": result.get("summary", ""),
            "key_points": result.get("key_points", []),
            "sentiment_indication": result.get("sentiment_indication", "neutral"),
            "notable_events": result.get("notable_events", []),
            "focus": focus,
            "summarized_at": datetime.now().isoformat()
        }
        
    except Exception as e:
        return {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "success": False
        }

# Wrap with decorator
@research_server.tool(
    name="summarize_content",
    description="""
    Summarize long articles or content about a stock.
    
    Args:
        content: str - The content to summarize
        symbol: str - Stock symbol for context
        max_length: int - Maximum summary length in words (default: 200)
        focus: str - Focus area: "ratings", "news", "analysis", or "general"
    
    Returns:
        dict - Summarized content with key points
    """
)
async def summarize_content(
    content: str,
    symbol: str,
    max_length: int = 200,
    focus: str = "general"
) -> Dict[str, Any]:
    """Summarize content about a stock."""
    return await _summarize_content_impl(content, symbol, max_length, focus)


# ============================================================================
# SCENARIO GENERATOR
# ============================================================================

# Implementation function
async def _generate_scenarios_impl(
    symbol: str,
    company_name: Optional[str] = None,
    ratings_data: Optional[Dict[str, Any]] = None,
    news_summary: Optional[str] = None,
    current_price: Optional[float] = None,
    source_urls: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Generate bull and bear scenarios for a stock."""
    try:
        openai_key = os.getenv("OPENAI_API_KEY")
        if not openai_key:
            return {"error": "OPENAI_API_KEY not found", "success": False}
        
        client = AsyncOpenAI(api_key=openai_key)
        
        # Build context
        context_parts = [f"Stock: {symbol}"]
        if company_name:
            context_parts.append(f"Company: {company_name}")
        if current_price:
            context_parts.append(f"Current Price: ${current_price}")
        if ratings_data:
            context_parts.append(f"Analyst Consensus: {ratings_data.get('consensus', 'N/A')}")
            context_parts.append(f"Average Target: ${ratings_data.get('average_target_price', 'N/A')}")
            if ratings_data.get("key_insights"):
                context_parts.append(f"Key Insights: {', '.join(ratings_data['key_insights'][:3])}")
        if news_summary:
            context_parts.append(f"Recent News Summary: {news_summary}")
        
        context = "\n".join(context_parts)
        
        prompt = f"""As a senior equity research analyst, generate detailed bull and bear scenarios for this stock.

{context}

Create comprehensive scenarios with the following structure for each:

BULL CASE:
- Core thesis and investment rationale
- Key catalysts that could drive upside
- Probability estimate (0-100%)
- Upside target price and % gain
- Timeline for thesis to play out
- Key assumptions that must hold true

BEAR CASE:
- Core thesis and risk rationale
- Key risks and potential negative catalysts
- Probability estimate (0-100%)
- Downside target price and % loss
- Warning signs to watch for
- Key assumptions for downside

Respond with a detailed JSON object:
{{
    "symbol": "{symbol}",
    "bull_case": {{
        "thesis": "Detailed bull thesis...",
        "catalysts": ["Catalyst 1", "Catalyst 2", ...],
        "probability": 45,
        "target_price": null or number,
        "upside_percent": null or number,
        "timeline": "6-12 months",
        "key_assumptions": ["Assumption 1", ...],
        "confidence_level": "high" | "medium" | "low"
    }},
    "bear_case": {{
        "thesis": "Detailed bear thesis...",
        "risks": ["Risk 1", "Risk 2", ...],
        "probability": 30,
        "target_price": null or number,
        "downside_percent": null or number,
        "warning_signs": ["Sign 1", ...],
        "key_assumptions": ["Assumption 1", ...],
        "confidence_level": "high" | "medium" | "low"
    }},
    "base_case": {{
        "thesis": "Most likely scenario...",
        "probability": 25,
        "target_price": null or number,
        "expected_return_percent": null or number
    }},
    "overall_recommendation": "buy" | "hold" | "sell",
    "conviction_level": "high" | "medium" | "low",
    "key_metrics_to_watch": ["Metric 1", "Metric 2", ...],
    "upcoming_catalysts": ["Catalyst with approximate date", ...]
}}

Be specific, data-driven, and balanced in your analysis. Probabilities should sum to 100%."""

        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.4
        )
        
        scenarios = json.loads(response.choices[0].message.content)
        
        # Get sources from ratings_data if not provided
        sources = []
        if not source_urls and ratings_data:
            source_urls = ratings_data.get('source_urls', [])
            sources = ratings_data.get('sources', [])
        
        return {
            "success": True,
            "symbol": symbol,
            "company_name": company_name,
            "current_price": current_price,
            "scenarios": scenarios,
            "sources": sources,
            "source_urls": source_urls or [],
            "generated_at": datetime.now().isoformat()
        }
        
    except Exception as e:
        return {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "success": False
        }

# Wrap with decorator
@research_server.tool(
    name="generate_scenarios",
    description="""
    Generate detailed bull and bear scenarios for a stock based on research data.
    
    Args:
        symbol: str - Stock ticker symbol
        company_name: str - Company name for context
        ratings_data: dict - Optional aggregated ratings data
        news_summary: str - Optional summary of recent news
        current_price: float - Optional current stock price
        source_urls: list - Optional list of source URLs
    
    Returns:
        dict - Bull and bear scenarios with catalysts, risks, probabilities, and price targets
    """
)
async def generate_scenarios(
    symbol: str,
    company_name: Optional[str] = None,
    ratings_data: Optional[Dict[str, Any]] = None,
    news_summary: Optional[str] = None,
    current_price: Optional[float] = None,
    source_urls: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Generate bull and bear scenarios for a stock."""
    return await _generate_scenarios_impl(symbol, company_name, ratings_data, news_summary, current_price, source_urls)


# ============================================================================
# CACHE MANAGEMENT
# ============================================================================

@research_server.tool(
    name="get_cached_research",
    description="""
    Retrieve cached research data for a stock if available.
    
    Args:
        symbol: str - Stock ticker symbol
        data_type: str - Type of data: "ratings", "search", "scenarios", or "all"
    
    Returns:
        dict - Cached data if available, or indication that cache is empty
    """
)
async def get_cached_research(
    symbol: str,
    data_type: str = "all"
) -> Dict[str, Any]:
    """Get cached research data."""
    try:
        results = {}
        
        if data_type in ["ratings", "all"]:
            key = _cache._make_key("analyst_ratings", symbol, 30)
            data = _cache.get(key)
            if data:
                results["ratings"] = data
        
        if data_type in ["search", "all"]:
            # Try common search patterns
            for query_suffix in ["analyst rating", "stock news"]:
                key = _cache._make_key("web_search", f"{symbol} {query_suffix}", 10)
                data = _cache.get(key)
                if data:
                    results["search"] = data
                    break
        
        return {
            "success": True,
            "symbol": symbol,
            "data_type": data_type,
            "cached_data": results,
            "cache_stats": _cache.get_stats(),
            "has_data": len(results) > 0
        }
        
    except Exception as e:
        return {
            "error": str(e),
            "success": False
        }


# ============================================================================
# MD&A SENTIMENT ANALYSIS
# ============================================================================

# Keywords to track for MD&A sentiment
MDA_KEYWORDS = {
    "negative": [
        "headwinds", "headwind", "margin pressure", "cost pressure", "cost pressures",
        "challenges", "challenged", "difficult", "uncertainty", "uncertainties",
        "volatility", "volatile", "decline", "declining", "weakness", "weak",
        "slowdown", "slowing", "pressure", "pressured", "concern", "concerns",
        "risk", "risks", "deterioration", "deteriorating", "downturn", "competition"
    ],
    "positive": [
        "tailwinds", "tailwind", "momentum", "growth", "growing", "strong", 
        "strength", "strengths", "opportunity", "opportunities", "optimistic",
        "confidence", "confident", "improvement", "improving", "expansion",
        "expanding", "acceleration", "accelerating", "outperformance", "outperform",
        "favorable", "progress", "robust", "solid", "gains", "winning"
    ],
    "guidance": [
        "guidance", "outlook", "forecast", "forecasts", "expect", "expecting",
        "expectations", "anticipate", "anticipating", "project", "projecting",
        "projections", "targets", "goals", "objectives", "guidance raised",
        "guidance lowered", "guidance maintained", "guidance reaffirmed"
    ],
    "margins": [
        "margin", "margins", "margin expansion", "margin compression",
        "gross margin", "operating margin", "profit margin", "ebitda margin",
        "margin improvement", "margin deterioration", "margin pressure"
    ]
}


async def _extract_mda_keywords_impl(
    text: str,
    symbol: Optional[str] = None
) -> Dict[str, Any]:
    """Extract and analyze MD&A specific keywords from text."""
    try:
        text_lower = text.lower()
        word_count = len(text.split())
        
        # Count keyword occurrences
        keyword_counts = {
            "negative": {},
            "positive": {},
            "guidance": {},
            "margins": {}
        }
        
        total_negative = 0
        total_positive = 0
        
        for category, keywords in MDA_KEYWORDS.items():
            for keyword in keywords:
                count = text_lower.count(keyword.lower())
                if count > 0:
                    keyword_counts[category][keyword] = count
                    if category == "negative":
                        total_negative += count
                    elif category == "positive":
                        total_positive += count
        
        # Calculate sentiment score
        net_sentiment = total_positive - total_negative
        sentiment_ratio = (total_positive + 1) / (total_negative + 1)  # Avoid division by zero
        
        # Determine overall tone
        if sentiment_ratio > 1.5:
            tone = "bullish"
        elif sentiment_ratio > 1.1:
            tone = "slightly_bullish"
        elif sentiment_ratio > 0.9:
            tone = "neutral"
        elif sentiment_ratio > 0.67:
            tone = "slightly_bearish"
        else:
            tone = "bearish"
        
        return {
            "success": True,
            "symbol": symbol,
            "word_count": word_count,
            "keyword_counts": keyword_counts,
            "total_negative_keywords": total_negative,
            "total_positive_keywords": total_positive,
            "net_sentiment_score": net_sentiment,
            "sentiment_ratio": round(sentiment_ratio, 3),
            "overall_tone": tone,
            "analyzed_at": datetime.now().isoformat()
        }
        
    except Exception as e:
        return {
            "error": str(e),
            "success": False
        }


async def _extract_mda_from_web(symbol: str, quarter_hint: str = "latest") -> Dict[str, Any]:
    """
    Extract MD&A content from web using Tavily search and ChatOpenAI.
    
    Args:
        symbol: Stock ticker symbol
        quarter_hint: "latest" for current quarter, "previous" for prior quarter
    
    Returns:
        Dict with extracted_text, sources, and quarter_date
    """
    try:
        # Get Tavily API key and create client
        tavily_key = os.getenv("TAVILY_API_KEY")
        if not tavily_key:
            return {"error": "TAVILY_API_KEY not found", "success": False}
        
        tavily_client = TavilyClient(api_key=tavily_key)
        
        # Search for MD&A content
        if quarter_hint == "latest":
            search_query = f"{symbol} latest earnings call management discussion analysis MD&A transcript"
        else:
            search_query = f"{symbol} previous quarter earnings call management discussion analysis MD&A transcript"
        
        search_results = tavily_client.search(
            query=search_query,
            max_results=5,
            search_depth="advanced",
            include_domains=["seekingalpha.com", "fool.com", "finance.yahoo.com", "sec.gov", "investors.com"]
        )
        
        if not search_results or not search_results.get("results"):
            return {
                "error": f"Could not find MD&A content for {symbol} ({quarter_hint})",
                "success": False
            }
        
        # Combine search results
        combined_content = "\n\n---\n\n".join([
            f"Source: {r.get('url', 'N/A')}\nTitle: {r.get('title', 'N/A')}\nContent: {r.get('content', '')}"
            for r in search_results["results"][:4]
        ])
        
        # Use ChatOpenAI to extract MD&A
        openai_key = os.getenv("OPENAI_API_KEY")
        if not openai_key:
            return {"error": "OPENAI_API_KEY not found", "success": False}
        
        client = AsyncOpenAI(api_key=openai_key)
        
        extraction_prompt = f"""You are an expert financial analyst. Extract the Management Discussion & Analysis (MD&A) 
or management commentary from the provided earnings call transcripts and reports for {symbol}.

Focus on extracting:
- CEO and CFO commentary about business performance and outlook
- Forward-looking statements and guidance
- Discussion of challenges (headwinds), opportunities (tailwinds), and market conditions
- Margin discussions, cost pressures, and operational updates
- Strategic priorities and initiatives

Search Results:
{combined_content[:15000]}

Extract ONLY the MD&A/management commentary text. If you find multiple sections, combine them.
Preserve important quotes and key statements. Do not add your own analysis."""

        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": extraction_prompt}],
            temperature=0.1
        )
        
        extracted_text = response.choices[0].message.content
        
        # Try to identify the quarter/date from titles
        quarter_date = None
        for result in search_results["results"][:3]:
            title = result.get("title", "")
            if "Q1" in title or "Q2" in title or "Q3" in title or "Q4" in title:
                import re
                quarter_match = re.search(r'Q[1-4]\s*20\d{2}', title)
                if quarter_match:
                    quarter_date = quarter_match.group()
                    break
        
        return {
            "success": True,
            "extracted_text": extracted_text,
            "sources": [r.get("url") for r in search_results["results"][:4]],
            "quarter_date": quarter_date,
            "text_length": len(extracted_text)
        }
        
    except Exception as e:
        return {
            "error": f"MD&A extraction failed: {str(e)}",
            "traceback": traceback.format_exc(),
            "success": False
        }


async def _analyze_mda_sentiment_impl(
    current_quarter_text: Optional[str] = None,
    previous_quarter_text: Optional[str] = None,
    symbol: Optional[str] = None,
    current_quarter_date: Optional[str] = None,
    previous_quarter_date: Optional[str] = None,
    auto_extract: bool = True
) -> Dict[str, Any]:
    """
    Analyze MD&A sentiment and compare tone between quarters.
    Uses both keyword analysis and LLM-based sentiment extraction.
    
    If auto_extract=True and texts are not provided, will automatically
    extract MD&A from web using Tavily search.
    """
    try:
        openai_key = os.getenv("OPENAI_API_KEY")
        if not openai_key:
            return {"error": "OPENAI_API_KEY not found", "success": False}
        
        client = AsyncOpenAI(api_key=openai_key)
        
        # Auto-extract MD&A if not provided
        if auto_extract and not current_quarter_text and symbol:
            print(f"Auto-extracting current quarter MD&A for {symbol}...")
            current_extraction = await _extract_mda_from_web(symbol, "latest")
            if not current_extraction.get("success"):
                return current_extraction
            
            current_quarter_text = current_extraction["extracted_text"]
            if not current_quarter_date:
                current_quarter_date = current_extraction.get("quarter_date", "Latest Quarter")
            
            extraction_sources = current_extraction.get("sources", [])
        else:
            extraction_sources = []
        
        if not current_quarter_text:
            return {"error": "No current quarter text provided or extracted", "success": False}
        
        # Extract keywords from current quarter
        current_keywords = await _extract_mda_keywords_impl(current_quarter_text, symbol)
        
        # Basic sentiment analysis
        current_sentiment = await _analyze_sentiment_impl(current_quarter_text, symbol)
        
        # LLM-based deep analysis of current quarter
        current_analysis_prompt = f"""Analyze the following Management Discussion & Analysis (MD&A) section for {symbol or 'this company'}.

Focus on:
1. Management's tone and confidence level (1-10 scale, 10 being most confident)
2. Key concerns or challenges mentioned
3. Growth opportunities highlighted
4. Guidance commentary (raised, lowered, maintained, or not provided)
5. Discussion of margins, costs, and profitability
6. Overall strategic direction

MD&A Text (Current Quarter{' - ' + current_quarter_date if current_quarter_date else ''}):
{current_quarter_text[:12000]}

Respond with a JSON object:
{{
    "confidence_level": 1-10,
    "tone_description": "...",
    "key_concerns": ["...", "..."],
    "growth_opportunities": ["...", "..."],
    "guidance_status": "raised" | "lowered" | "maintained" | "not_provided",
    "guidance_details": "...",
    "margin_commentary": "...",
    "strategic_direction": "...",
    "notable_quotes": ["...", "..."],
    "overall_sentiment": "very_positive" | "positive" | "neutral" | "cautious" | "negative"
}}"""

        current_response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": current_analysis_prompt}],
            response_format={"type": "json_object"},
            temperature=0.2
        )
        
        current_analysis = json.loads(current_response.choices[0].message.content)
        
        result = {
            "success": True,
            "symbol": symbol,
            "current_quarter": {
                "date": current_quarter_date,
                "keyword_analysis": current_keywords,
                "sentiment_analysis": current_sentiment,
                "deep_analysis": current_analysis,
                "text_length": len(current_quarter_text)
            }
        }
        
        if extraction_sources:
            result["sources"] = extraction_sources
        
        # Auto-extract previous quarter if not provided
        if auto_extract and not previous_quarter_text and symbol:
            print(f"Auto-extracting previous quarter MD&A for {symbol}...")
            previous_extraction = await _extract_mda_from_web(symbol, "previous")
            if previous_extraction.get("success"):
                previous_quarter_text = previous_extraction["extracted_text"]
                if not previous_quarter_date:
                    previous_quarter_date = previous_extraction.get("quarter_date", "Previous Quarter")
                if "previous_sources" not in result:
                    result["previous_sources"] = previous_extraction.get("sources", [])
        
        # Compare with previous quarter if provided or extracted
        if previous_quarter_text:
            # Extract keywords from previous quarter
            previous_keywords = await _extract_mda_keywords_impl(previous_quarter_text, symbol)
            
            # Basic sentiment analysis
            previous_sentiment = await _analyze_sentiment_impl(previous_quarter_text, symbol)
            
            # LLM-based analysis of previous quarter
            previous_analysis_prompt = f"""Analyze the following Management Discussion & Analysis (MD&A) section for {symbol or 'this company'}.

Focus on the same aspects as before: tone, concerns, opportunities, guidance, margins, strategy.

MD&A Text (Previous Quarter{' - ' + previous_quarter_date if previous_quarter_date else ''}):
{previous_quarter_text[:12000]}

Respond with the same JSON structure as before."""

            previous_response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": previous_analysis_prompt}],
                response_format={"type": "json_object"},
                temperature=0.2
            )
            
            previous_analysis = json.loads(previous_response.choices[0].message.content)
            
            result["previous_quarter"] = {
                "date": previous_quarter_date,
                "keyword_analysis": previous_keywords,
                "sentiment_analysis": previous_sentiment,
                "deep_analysis": previous_analysis,
                "text_length": len(previous_quarter_text)
            }
            
            # Quarter-over-quarter comparison
            confidence_change = current_analysis.get("confidence_level", 5) - previous_analysis.get("confidence_level", 5)
            
            sentiment_map = {"very_positive": 5, "positive": 4, "neutral": 3, "cautious": 2, "negative": 1}
            current_sentiment_score = sentiment_map.get(current_analysis.get("overall_sentiment", "neutral"), 3)
            previous_sentiment_score = sentiment_map.get(previous_analysis.get("overall_sentiment", "neutral"), 3)
            sentiment_change = current_sentiment_score - previous_sentiment_score
            
            # Keyword trend
            keyword_sentiment_change = (
                current_keywords.get("net_sentiment_score", 0) - 
                previous_keywords.get("net_sentiment_score", 0)
            )
            
            # Determine overall trend
            if confidence_change >= 2 and sentiment_change >= 1:
                trend = "significantly_more_positive"
            elif confidence_change >= 1 or sentiment_change >= 1:
                trend = "more_positive"
            elif abs(confidence_change) < 1 and abs(sentiment_change) < 1:
                trend = "stable"
            elif confidence_change <= -1 or sentiment_change <= -1:
                trend = "more_negative"
            else:
                trend = "significantly_more_negative"
            
            result["quarter_over_quarter_comparison"] = {
                "confidence_change": confidence_change,
                "sentiment_change": sentiment_change,
                "keyword_sentiment_change": keyword_sentiment_change,
                "overall_trend": trend,
                "guidance_change": {
                    "current": current_analysis.get("guidance_status"),
                    "previous": previous_analysis.get("guidance_status")
                },
                "key_differences": {
                    "new_concerns": list(set(current_analysis.get("key_concerns", [])) - 
                                       set(previous_analysis.get("key_concerns", []))),
                    "resolved_concerns": list(set(previous_analysis.get("key_concerns", [])) - 
                                             set(current_analysis.get("key_concerns", []))),
                    "new_opportunities": list(set(current_analysis.get("growth_opportunities", [])) - 
                                             set(previous_analysis.get("growth_opportunities", [])))
                }
            }
            
            # Generate comparison summary
            comparison_summary_prompt = f"""Compare these two MD&A analyses from consecutive quarters for {symbol or 'this company'}.

Current Quarter Analysis:
{json.dumps(current_analysis, indent=2)}

Previous Quarter Analysis:
{json.dumps(previous_analysis, indent=2)}

Provide a brief 2-3 paragraph summary highlighting:
1. How management's tone and confidence has changed
2. Key shifts in concerns, opportunities, or strategic focus
3. What this means for investors

Keep it concise and actionable."""

            summary_response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": comparison_summary_prompt}],
                temperature=0.3
            )
            
            result["quarter_over_quarter_comparison"]["summary"] = summary_response.choices[0].message.content
        
        result["analyzed_at"] = datetime.now().isoformat()
        return result
        
    except Exception as e:
        return {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "success": False
        }


# Wrap with decorator
@research_server.tool(
    name="analyze_mda_sentiment",
    description="""
    Analyze Management Discussion & Analysis (MD&A) sentiment from earnings calls or reports.
    
    **AUTO-EXTRACTION MODE (Recommended):**
    - Just provide the symbol, and MD&A will be automatically extracted from web using Tavily
    - Automatically compares current quarter vs previous quarter
    
    **MANUAL MODE:**
    - Provide your own MD&A text if you have it
    
    Tracks keywords like 'headwinds', 'margin pressure', 'guidance' and compares management tone
    between quarters using ChatOpenAI analysis.
    
    Args:
        symbol: str - Stock ticker symbol (REQUIRED for auto-extraction)
        current_quarter_text: str - Optional: MD&A text from current quarter (if not provided, will auto-extract)
        previous_quarter_text: str - Optional: MD&A text from previous quarter (if not provided, will auto-extract)
        current_quarter_date: str - Optional: date for current quarter (e.g., "Q4 2024")
        previous_quarter_date: str - Optional: date for previous quarter (e.g., "Q3 2024")
        auto_extract: bool - Default True: automatically extract MD&A from web using Tavily
    
    Returns:
        dict - Comprehensive MD&A sentiment analysis including:
            - Keyword analysis (headwinds, margin pressure, guidance, etc.)
            - Management confidence level (1-10) and tone
            - Key concerns and opportunities
            - Quarter-over-quarter comparison
            - Overall sentiment trend and summary
            - Sources used for extraction (if auto-extracted)
    """
)
async def analyze_mda_sentiment(
    symbol: str,
    current_quarter_text: Optional[str] = None,
    previous_quarter_text: Optional[str] = None,
    current_quarter_date: Optional[str] = None,
    previous_quarter_date: Optional[str] = None,
    auto_extract: bool = True
) -> Dict[str, Any]:
    """Analyze MD&A sentiment and compare quarters. Auto-extracts from web if text not provided."""
    return await _analyze_mda_sentiment_impl(
        current_quarter_text, 
        previous_quarter_text, 
        symbol,
        current_quarter_date,
        previous_quarter_date,
        auto_extract
    )


# ============================================================================
# COMPREHENSIVE RESEARCH PIPELINE
# ============================================================================

@research_server.tool(
    name="comprehensive_research",
    description="""
    Run a full research pipeline for a stock: search, aggregate ratings, analyze sentiment,
    and generate bull/bear scenarios.
    
    Args:
        symbol: str - Stock ticker symbol
        company_name: str - Optional company name for better search
        current_price: float - Optional current stock price for scenario analysis
        include_scenarios: bool - Whether to generate bull/bear scenarios (default: True)
    
    Returns:
        dict - Complete research package with ratings, sentiment, and scenarios
    """
)
async def comprehensive_research(
    symbol: str,
    company_name: Optional[str] = None,
    current_price: Optional[float] = None,
    include_scenarios: bool = True
) -> Dict[str, Any]:
    """Run comprehensive research pipeline for a stock."""
    try:
        results = {
            "symbol": symbol,
            "company_name": company_name,
            "current_price": current_price,
            "research_started_at": datetime.now().isoformat()
        }
        
        # Collect all source URLs and dates
        all_sources = []
        
        # Step 1: Search for analyst ratings
        print(f"🔍 Searching for analyst ratings for {symbol}...")
        ratings_search = await _search_analyst_ratings_impl(symbol, company_name)
        results["ratings_search"] = {
            "success": ratings_search.get("success"),
            "results_count": ratings_search.get("results_count", 0)
        }
        
        # Collect URLs and dates from search results
        if ratings_search.get("results"):
            for r in ratings_search.get("results", []):
                if r.get('url'):
                    pub_date = r.get('published_date', '')
                    if not pub_date or pub_date == "":
                        pub_date = "Not provided by source"
                    all_sources.append({
                        "url": r.get('url'),
                        "title": r.get('title', ''),
                        "published_date": pub_date
                    })
        
        # Step 2: Aggregate ratings
        print(f"📊 Aggregating ratings for {symbol}...")
        if ratings_search.get("success") and ratings_search.get("results"):
            aggregated = await _aggregate_ratings_impl(symbol, ratings_search.get("results"))
            results["aggregated_ratings"] = aggregated
        else:
            results["aggregated_ratings"] = {"success": False, "error": "No ratings found to aggregate"}
        
        # Step 3: Analyze sentiment of combined content
        print(f"🎭 Analyzing sentiment for {symbol}...")
        if ratings_search.get("results"):
            combined_text = " ".join([r.get("content", "") for r in ratings_search.get("results", [])[:5]])
            if combined_text:
                sentiment = await _analyze_sentiment_impl(combined_text, symbol)
                results["sentiment"] = sentiment
        
        # Step 4: Generate summary
        print(f"📝 Generating summary for {symbol}...")
        if ratings_search.get("results"):
            combined_content = "\n\n".join([r.get("content", "") for r in ratings_search.get("results", [])[:5]])
            if combined_content:
                summary = await _summarize_content_impl(combined_content, symbol, focus="ratings")
                results["summary"] = summary
        
        # Step 5: Generate scenarios
        if include_scenarios:
            print(f"🎯 Generating bull/bear scenarios for {symbol}...")
            news_summary = results.get("summary", {}).get("summary", "")
            # Extract source_urls for backward compatibility
            all_source_urls = [s['url'] for s in all_sources]
            scenarios = await _generate_scenarios_impl(
                symbol=symbol,
                company_name=company_name,
                ratings_data=results.get("aggregated_ratings"),
                news_summary=news_summary,
                current_price=current_price,
                source_urls=all_source_urls
            )
            results["scenarios"] = scenarios
        
        # Add all sources with dates to results
        # Deduplicate by URL
        seen_urls = set()
        unique_sources = []
        for s in all_sources:
            if s['url'] not in seen_urls:
                seen_urls.add(s['url'])
                unique_sources.append(s)
        
        results["sources"] = unique_sources
        results["source_urls"] = [s['url'] for s in unique_sources]  # Backward compatibility
        results["success"] = True
        results["research_completed_at"] = datetime.now().isoformat()
        
        return results
        
    except Exception as e:
        return {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "success": False
        }


# ============================================================================
# SERVER STARTUP
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    print("🚀 Starting Research MCP Server...")
    print("=" * 60)
    print("📚 Available Tools:")
    print("  • web_search - General web search via Tavily")
    print("  • search_analyst_ratings - Find analyst ratings")
    print("  • aggregate_ratings - Normalize and aggregate ratings")
    print("  • analyze_sentiment - Sentiment analysis")
    print("  • analyze_mda_sentiment - MD&A sentiment analysis with Q/Q comparison")
    print("  • summarize_content - Summarize articles")
    print("  • generate_scenarios - Bull/bear scenario generation")
    print("  • get_cached_research - Retrieve cached data")
    print("  • comprehensive_research - Full research pipeline")
    print("=" * 60)
    
    # Run the MCP server
    research_server.run(transport="streamable-http", host="0.0.0.0", port=8567)
