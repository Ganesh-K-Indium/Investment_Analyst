"This module contains all info about about the nodes in the graph"
import logging
import re
from typing import Optional, Dict, Any, List
from datetime import datetime
from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq
from langchain_core.messages import AIMessage
from langchain_tavily import TavilySearch
from rag.prompts.prompts import (get_rag_chain,
                                                          get_financial_analyst_grader_chain,
                                                          MACRO_PLANNER_SYSTEM_PROMPT,
                                                          MACRO_SYNTHESIS_PROMPT,
                                                          MACRO_FEW_SHOT)
from rag.vectordb.client import load_vector_database
from app.utils.company_mapping import get_ticker, TICKER_TO_COMPANY, get_company_name as map_ticker_to_company

logger = logging.getLogger("rag.graph.nodes")

load_dotenv()

# Trusted financial data domains for web search
# Only these domains will be used for financial queries to ensure data quality and reliability
TRUSTED_FINANCIAL_DOMAINS = [
    "sec.gov",                  # SEC filings - official source
    "investor.com",             # Investor relations sites
    "finance.yahoo.com",        # Yahoo Finance
    "bloomberg.com",            # Bloomberg
    "reuters.com",              # Reuters
    "cnbc.com",                 # CNBC
    "marketwatch.com",          # MarketWatch
    "fool.com",                 # Motley Fool
    "seekingalpha.com",         # Seeking Alpha
    "morningstar.com",          # Morningstar
    "wsj.com",                  # Wall Street Journal
    "ft.com",                   # Financial Times
    "forbes.com",               # Forbes
    "investopedia.com",         # Investopedia
    "nasdaq.com",               # Nasdaq
    "nyse.com",                 # NYSE
    "gurufocus.com",            # GuruFocus
    "macrotrends.net",          # MacroTrends
    "stockanalysis.com",        # Stock Analysis
    "companiesmarketcap.com",   # Companies Market Cap
    "treasury.gov",        # US Treasury data

]

# Government sources for macro/liquidity data (Liquidity dimension of ALPHA)
GOVT_SOURCE_DOMAINS = [
    "fdic.gov",             # FDIC - bank liquidity, deposit, and risk data
    "federalreserve.gov",   # Federal Reserve - interest rates, monetary policy
    "treasury.gov",         # US Treasury - yields, debt data
    "bls.gov",              # Bureau of Labor Statistics - inflation, employment
    "bea.gov",              # Bureau of Economic Analysis - GDP, macro indicators
]


def generate_comparison_subqueries(companies: list, year: str = None) -> dict:
    """
    Generate optimized sub-queries for company comparison WITHOUT LLM.

    These queries are specifically designed to retrieve data from 10-K reports
    with maximum accuracy and minimal retrieval time.

    Args:
        companies: List of company names to compare
        year: Year for comparison. Defaults to the most recent fiscal year
            likely to actually have a filed 10-K (per the first company's
            fiscal calendar) rather than the current calendar year, which
            for most filers won't have one until Feb/Mar of next year.

    Returns:
        dict: Sub-query analysis with pre-generated queries
    """
    if year is None:
        from app.utils.company_mapping import get_ticker as _get_ticker_for_year, get_most_recent_filed_fiscal_year
        _first_ticker = _get_ticker_for_year(companies[0]) if companies else None
        if not _first_ticker and companies and 2 <= len(companies[0]) <= 5:
            _first_ticker = companies[0]
        year = str(get_most_recent_filed_fiscal_year(_first_ticker))

    sub_queries = []

    # Template structure optimized for 10-K reports
    # Using multiple search terms and specific document sections

    year_int = int(year) if isinstance(year, str) else year
    prior_year = year_int - 1

    for company in companies:
        # Fiscal-year-end phrasing: most filers use a calendar fiscal year
        # (year ended December 31), so keep that exact language for them —
        # this is the majority case and preserves existing retrieval output.
        # Known non-calendar filers (Apple, Microsoft, etc.) get generic
        # "fiscal year end {year}" phrasing instead, since asserting
        # "December 31" for them would be factually wrong and could bias
        # retrieval toward the wrong period.
        from app.utils.company_mapping import get_ticker as _get_ticker, get_fiscal_year_end_month as _get_fye_month
        _ticker = _get_ticker(company)
        _fye_month = _get_fye_month(_ticker) if _ticker else 12
        if _fye_month == 12:
            year_end_phrase = f"year ended December 31 {year}"
            balance_date_phrase = f"as of December 31 {year}"
        else:
            year_end_phrase = f"fiscal year {year} annual period"
            balance_date_phrase = f"as of fiscal year end {year}"

        # 1. REVENUE - Exact 10-K language
        sub_queries.append(
            f"{company} total revenues net revenues {year_end_phrase} consolidated statements of operations"
        )

        # 2. NET INCOME - Exact bottom-line metric
        sub_queries.append(
            f"{company} net income loss {year_end_phrase} per share diluted basic"
        )

        # 3. OPERATING INCOME - Before tax line
        sub_queries.append(
            f"{company} income from operations operating income {year_end_phrase}"
        )

        # 4. EARNINGS GROWTH - Explicit comparison language
        sub_queries.append(
            f"{company} earnings growth increased or decreased from {prior_year} to {year} compared to {prior_year} percentage change"
        )

        # 5. R&D EXPENSES - Operating cost breakout
        sub_queries.append(
            f"{company} research and development costs and expenses {year_end_phrase}"
        )

        # 6. TOTAL ASSETS - Balance sheet specific date
        sub_queries.append(
            f"{company} total assets {balance_date_phrase} consolidated balance sheets"
        )

        # 7. TOTAL DEBT - Long-term obligations
        sub_queries.append(
            f"{company} long-term debt total liabilities {balance_date_phrase} balance sheets"
        )

        # 8. PROFIT DRIVERS - MD&A results section
        sub_queries.append(
            f"{company} results of operations factors affecting our performance key business drivers {year}"
        )

        # 9. RISK FACTORS - Dedicated section
        sub_queries.append(
            f"{company} Item 1A risk factors risks and uncertainties that could affect our business {year}"
        )

    logger.info(f"[FIXED QUERIES] Generated {len(sub_queries)} optimized sub-queries for {len(companies)} companies")
    logger.warning(f"[FIXED QUERIES] Skipped LLM query generation - using 10-K-optimized templates")

    return {
        "needs_sub_queries": True,
        "query_type": "multi_company",
        "companies_detected": companies,
        "sub_queries": sub_queries,
        "requested_years": [year_int],
        "reasoning": f"Pre-optimized 10-K queries for {', '.join(companies)} (no LLM needed)",
        "generation_method": "template"  # vs "llm"
    }


def detect_segment_or_geographic_query(question: str) -> str:
    """
    Detect if a query is specifically about segment reporting or geographic information.

    Returns:
        "segment" if segment query, "geographic" if geographic query, "none" otherwise.
    """
    question_lower = question.lower()

    segment_keywords = [
        "segment", "segments", "reportable segment", "operating segment",
        "business segment", "segment revenue", "segment income", "segment profit",
        "revenue by segment", "income by segment", "segment assets",
        "segment capital expenditure", "segment depreciation", "segment amortization",
        "capex by segment", "segment performance", "segment results",
        "segment margin", "segment outlook", "segment trend",
        "product segment", "line of business", "disaggregation of revenue",
        "segment ebitda", "segment operating income", "segment net sales",
        "codm", "asc 280", "segment disclosure", "segment reporting"
    ]

    geographic_keywords = [
        "geographic", "geography", "by region", "by country",
        "revenue by geography", "revenue by region", "net sales by geography",
        "geographic revenue", "geographic distribution", "region country",
        "revenue concentration", "geographic information",
        "foreign operations", "international operations",
        "domestic vs international", "overseas operations", "global footprint",
        "foreign subsidiaries", "properties by location", "facilities by geography",
        "manufacturing locations", "data centers", "distribution centers",
        "assets by country", "geographic risk", "country risk", "regional risk",
        "currency risk", "foreign exchange exposure", "export controls",
        "sanctions", "customers by region", "customer concentration geography",
        "market concentration regional", "geographic market share",
        "long lived assets by geography", "revenue by country"
    ]

    # Check geographic first (more specific) then segment
    if any(kw in question_lower for kw in geographic_keywords):
        return "geographic"
    if any(kw in question_lower for kw in segment_keywords):
        return "segment"
    return "none"


def _extract_years_from_question(question: str, ticker: Optional[str] = None) -> list:
    """
    Extract explicitly mentioned 4-digit years (2000-2029) from the user question.

    Falls back to the most recent fiscal year likely to actually have a filed
    10-K, NOT the current calendar year — a 10-K for the current calendar
    year almost never exists yet (companies get ~60-90 days after fiscal
    year end to file), so an undated query like "break down Google's
    segments" would otherwise silently target an unfiled fiscal year and
    retrieve nothing useful.
    """
    years = sorted(set(int(y) for y in re.findall(r'\b(20[0-2][0-9])\b', question)))
    if years:
        return years
    if ticker:
        from app.utils.company_mapping import get_most_recent_filed_fiscal_year
        return [get_most_recent_filed_fiscal_year(ticker)]
    # No ticker resolved yet either — a company-agnostic calendar-year
    # assumption is still safer than "this year", which is correct for the
    # overwhelming majority (calendar-year) filers.
    return [datetime.now().year - 1]


# Pure keyword heuristic for filing-type inference — zero LLM/embedding cost.
# Order matters: check 8-K (event) and 10-Q (quarterly) before falling back to
# 10-K, since "10-K" mentions are the most common false-positive-free signal
# but quarterly/event language should win when both could plausibly apply.
_FILING_TYPE_10K_KEYWORDS = [
    "10-k", "10k", "annual report", "full year", "full-year", "fiscal year annual",
    "yearly results", "3-year", "three-year", "comparative financials",
]
_FILING_TYPE_10Q_KEYWORDS = [
    "10-q", "10q", "quarterly", "quarter", "q1", "q2", "q3", "q4",
    "latest quarter", "most recent quarter", "this quarter", "last quarter",
    "sequential", "qoq", "quarter-over-quarter",
]
_FILING_TYPE_8K_KEYWORDS = [
    "8-k", "8k", "material event", "press release", "announced", "announcement",
    "departure", "resignation", "appointed", "appointment", "acquisition of",
    "merger agreement", "guidance update", "restructuring announcement",
    "executive change", "ceo change", "cfo change",
]


def detect_filing_type_in_query(question: str) -> Optional[str]:
    """
    Infer which SEC filing type (10-K / 10-Q / 8-K) a query implies, using
    pure keyword heuristics — no LLM call. Returns None when the query
    doesn't clearly imply a specific filing type, which means "search all
    filing types" (the safe default that preserves pre-existing behavior for
    collections/queries where filing type isn't a meaningful signal).
    """
    if not question:
        return None
    q = question.lower()

    # Explicit type mentions win outright, regardless of order below.
    if re.search(r'\b10-?k\b', q):
        return "10-K"
    if re.search(r'\b10-?q\b', q):
        return "10-Q"
    if re.search(r'\b8-?k\b', q):
        return "8-K"

    if any(kw in q for kw in _FILING_TYPE_8K_KEYWORDS):
        return "8-K"
    if any(kw in q for kw in _FILING_TYPE_10Q_KEYWORDS):
        return "10-Q"
    if any(kw in q for kw in _FILING_TYPE_10K_KEYWORDS):
        return "10-K"

    return None


_QUARTER_WORDS = {
    "first quarter": 1, "1st quarter": 1,
    "second quarter": 2, "2nd quarter": 2,
    "third quarter": 3, "3rd quarter": 3,
    "fourth quarter": 4, "4th quarter": 4,
}


def extract_fiscal_quarter_from_question(question: str) -> Optional[int]:
    """
    Extract which fiscal quarter (1-4) the question refers to, if any —
    "Q1"/"Q1 2025"/"first quarter" all resolve to a plain quarter NUMBER.

    Deliberately does NOT attempt to resolve this to a calendar date range
    here — that would require guessing which company's fiscal calendar
    applies, and this function runs before tickers are even resolved. The
    quarter number itself is unambiguous (the user said "Q1"); per-ticker
    calendar interpretation happens later once we know which companies are
    involved (see get_fiscal_quarter_calendar_span in company_mapping.py).
    """
    if not question:
        return None
    q = question.lower()

    m = re.search(r'\bq([1-4])\b', q)
    if m:
        return int(m.group(1))

    for phrase, quarter in _QUARTER_WORDS.items():
        if phrase in q:
            return quarter

    return None


def generate_segment_subqueries(companies: list, question: str = "") -> dict:
    """
    Generate predefined sub-queries for segment reporting queries WITHOUT LLM.
    Optimized for 10-K segment disclosures (ASC 280).
    """
    requested_years = _extract_years_from_question(question, ticker=companies[0] if companies else None)
    year_suffix = "for years " + ", ".join(str(y) for y in requested_years)
    sub_queries = []

    for company in companies:
        # 1. Segment overview & structure
        sub_queries.append(
            f"{company} reportable segments operating segments business segments segment overview segment structure segment description chief operating decision maker CODM {year_suffix}"
        )
        # 2. Segment financial performance
        sub_queries.append(
            f"{company} segment revenue segment net sales segment results segment operating income segment profit segment EBITDA revenue by segment income by segment {year_suffix}"
        )
        # 3. Segment reporting notes (ASC 280)
        sub_queries.append(
            f"{company} note segment reporting reportable segments note ASC 280 segment disclosure segment accounting policy segment measurement basis {year_suffix}"
        )
        # 4. Product / business line disaggregation
        sub_queries.append(
            f"{company} geographic segments product segments line of business disaggregation of revenue segment categories product line revenue {year_suffix}"
        )
        # 5. Segment assets & capital allocation
        sub_queries.append(
            f"{company} segment assets segment capital expenditure segment depreciation segment amortization assets by segment capex by segment long lived assets by segment {year_suffix}"
        )
        # 6. Segment MD&A discussion
        sub_queries.append(
            f"{company} segment performance discussion MD&A segment results drivers of segment growth segment margins segment trends segment outlook {year_suffix}"
        )

    logger.info(f"[SEGMENT QUERIES] Generated {len(sub_queries)} predefined sub-queries for {len(companies)} companies")
    logger.info(f"[SEGMENT QUERIES] Requested years: {requested_years}")
    logger.warning(f"[SEGMENT QUERIES] Skipped LLM query generation - using 10-K segment templates")

    return {
        "needs_sub_queries": True,
        "query_type": "segment",
        "companies_detected": companies,
        "sub_queries": sub_queries,
        "requested_years": requested_years,
        "reasoning": f"Pre-optimized segment reporting queries for {', '.join(companies)} (no LLM needed)",
        "generation_method": "template"
    }


def generate_geographic_subqueries(companies: list, question: str = "") -> dict:
    """
    Generate predefined sub-queries for geographic/regional queries WITHOUT LLM.
    Optimized for 10-K geographic disclosures.
    """
    requested_years = _extract_years_from_question(question, ticker=companies[0] if companies else None)
    year_suffix = "for years " + ", ".join(str(y) for y in requested_years)
    sub_queries = []

    for company in companies:
        # 1. Revenue by geography
        sub_queries.append(
            f"{company} revenue by geography revenue by region net sales by geography geographic revenue distribution disaggregated revenue region country revenue concentration {year_suffix}"
        )
        # 2. Geographic notes & ASC 280
        sub_queries.append(
            f"{company} geographic information note segment reporting geography ASC 280 geographic disclosure foreign domestic revenue by country long lived assets by geograph {year_suffix}"
        )
        # 3. Foreign / international operations
        sub_queries.append(
            f"{company} foreign operations international operations domestic vs international revenue foreign subsidiaries overseas operations global footprint {year_suffix}"
        )
        # 4. Properties & facilities by location
        sub_queries.append(
            f"{company} properties by location facilities by geography manufacturing locations data centers offices distribution centers assets by country {year_suffix}"
        )
        # 5. Geographic risk factors
        sub_queries.append(
            f"{company} geographic risk country risk regional risk political risk currency risk foreign exchange exposure international regulatory risk sanctions export controls {year_suffix}"
        )
        # 6. Customer / market concentration by region
        sub_queries.append(
            f"{company} major customers by region customer concentration geography market concentration regional demand geographic market share {year_suffix}"
        )

    logger.info(f"[GEOGRAPHIC QUERIES] Generated {len(sub_queries)} predefined sub-queries for {len(companies)} companies")
    logger.info(f"[GEOGRAPHIC QUERIES] Requested years: {requested_years}")
    logger.warning(f"[GEOGRAPHIC QUERIES] Skipped LLM query generation - using 10-K geographic templates")

    return {
        "needs_sub_queries": True,
        "query_type": "geographic",
        "companies_detected": companies,
        "sub_queries": sub_queries,
        "requested_years": requested_years,
        "reasoning": f"Pre-optimized geographic queries for {', '.join(companies)} (no LLM needed)",
        "generation_method": "template"
    }


def preprocess_and_analyze_query(state):
    """
    PREPROCESSING NODE: Analyze query and generate sub-queries if needed.
    Context-free - no memory or conversation history.

    UNIVERSAL SUB-QUERY ANALYZER:
    - Single LLM call extracts companies AND generates optimal sub-queries
    - Works for ALL query types: single-company, multi-company, financial calculations, temporal comparisons

    COMPARISON MODE OPTIMIZATION:
    - For comparison queries, uses pre-optimized templates instead of LLM (faster, cheaper, better)

    SEGMENT / GEOGRAPHIC MODE OPTIMIZATION:
    - For segment or geographic queries, uses pre-optimized templates instead of LLM
    """
    logger.info("---QUERY ANALYSIS---")
    messages = state["messages"]
    question = messages[-1].content
    question_lower = question.lower()

    # -------------------------------------------------------------
    # COMPARISON MODE: Use fixed templates for known comparison queries
    # -------------------------------------------------------------
    is_comparison_mode = state.get("is_comparison_mode", False)

    if is_comparison_mode:
        logger.info(" COMPARISON MODE DETECTED - Using pre-optimized 10-K queries")

        # Extract companies from state
        comparison_companies = []
        if state.get("comparison_company1"):
            comparison_companies.append(state["comparison_company1"])
        if state.get("comparison_company2"):
            comparison_companies.append(state["comparison_company2"])
        if state.get("comparison_company3"):
            comparison_companies.append(state["comparison_company3"])

        logger.info(f" Companies: {', '.join(comparison_companies)}")

        # Generate fixed sub-queries using the year from state if given; leave
        # None otherwise so generate_comparison_subqueries applies its own
        # fiscal-year-aware fallback (most recent year likely to have a filed
        # 10-K) instead of blindly defaulting to the current calendar year.
        comparison_year = str(state["year_start"]) if state.get("year_start") else (
            str(state["year_end"]) if state.get("year_end") else None
        )
        logger.info(f" Comparison year: {comparison_year or '(unspecified — will resolve to most recent filed fiscal year)'}")
        sub_query_analysis = generate_comparison_subqueries(comparison_companies, year=comparison_year)

        return {
            "companies_detected": comparison_companies,
            "sub_query_analysis": sub_query_analysis,
            "requested_years": sub_query_analysis["requested_years"],
            "sub_query_results": {},
            "filing_type": detect_filing_type_in_query(question),
            "requested_fiscal_quarter": extract_fiscal_quarter_from_question(question)
        }

    # -------------------------------------------------------------
    # SEGMENT / GEOGRAPHIC MODE: Use fixed templates
    # -------------------------------------------------------------
    seg_geo_type = detect_segment_or_geographic_query(question)

    if seg_geo_type != "none":
        # Determine companies from state (company_filter / ticker)
        companies = []
        company_filter = state.get("company_filter", [])
        primary_ticker = state.get("ticker")

        if company_filter:
            companies = [c for c in company_filter if c and c.strip()]
        elif primary_ticker and primary_ticker.lower() != "string":
            companies = [primary_ticker]

        if companies:
            if seg_geo_type == "segment":
                logger.info(f" SEGMENT QUERY DETECTED - Using pre-optimized segment templates for {companies}")
                sub_query_analysis = generate_segment_subqueries(companies, question=question)
            else:
                logger.info(f" GEOGRAPHIC QUERY DETECTED - Using pre-optimized geographic templates for {companies}")
                sub_query_analysis = generate_geographic_subqueries(companies, question=question)

            return {
                "companies_detected": companies,
                "sub_query_analysis": sub_query_analysis,
                "requested_years": sub_query_analysis["requested_years"],
                "sub_query_results": {},
                "filing_type": detect_filing_type_in_query(question),
                "requested_fiscal_quarter": extract_fiscal_quarter_from_question(question)
            }
        else:
            logger.info(f"  {seg_geo_type.upper()} query detected but no companies identified, falling through to LLM analysis")

    # -------------------------------------------------------------
    # NORMAL MODE: Continue with existing logic
    # -------------------------------------------------------------

    # UNIVERSAL APPROACH: Single LLM call for sub-query analysis
    logger.info("---UNIVERSAL SUB-QUERY ANALYSIS---")
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    
    from rag.prompts.prompts import get_universal_sub_query_analyzer
    sub_query_analyzer = get_universal_sub_query_analyzer(llm)
    
    # Analyze the question
    analysis = sub_query_analyzer.invoke({"question": question})
    
    # filing_type: prefer the LLM's structured hint (free — rides the existing
    # call), fall back to the keyword heuristic if the LLM left it unresolved.
    filing_type = getattr(analysis, "filing_type_hint", None) or detect_filing_type_in_query(question)

    # Convert to dict for state storage
    sub_query_analysis = {
        "needs_sub_queries": analysis.needs_sub_queries,
        "query_type": analysis.query_type,
        "companies_detected": analysis.companies_detected,
        "sub_queries": analysis.sub_queries,
        "requested_years": analysis.requested_years,
        "reasoning": analysis.reasoning
    }

    # Log analysis results
    logger.info(f"[ANALYSIS] Query Type: {analysis.query_type}")
    logger.info(f"[ANALYSIS] Companies: {analysis.companies_detected if analysis.companies_detected else 'None'}")
    logger.info(f"[ANALYSIS] Needs Sub-Queries: {analysis.needs_sub_queries}")
    logger.info(f"[ANALYSIS] Requested Years: {analysis.requested_years}")
    logger.info(f"[ANALYSIS] Filing Type: {filing_type or 'unresolved (search all)'}")

    if analysis.needs_sub_queries:
        logger.info(f"[ANALYSIS] Generated {len(analysis.sub_queries)} sub-queries:")
        for i, sq in enumerate(analysis.sub_queries, 1):
            logger.info(f"   {i}. {sq}")
        logger.info(f"[ANALYSIS] Reasoning: {analysis.reasoning}")
    else:
        logger.info(f"[ANALYSIS] Direct retrieval recommended: {analysis.reasoning}")

    # Return state updates
    return {
        "companies_detected": analysis.companies_detected,
        "sub_query_analysis": sub_query_analysis,
        "requested_years": analysis.requested_years,
        "sub_query_results": {},
        "filing_type": filing_type,
        "requested_fiscal_quarter": extract_fiscal_quarter_from_question(question)
    }

def detect_tickers_in_query(query_text: str, allowed_tickers: set) -> set:
    """
    Intelligently detect which tickers from the allowed set are mentioned in the query.

    Detection strategies:
    1. Exact ticker match (e.g., "AAPL" or "aapl")
    2. Company name match (e.g., "Apple" → "AAPL", "Amazon" → "AMZN")
    3. Partial company name match (e.g., "Microsoft's revenue" → "MSFT")

    Args:
        query_text: The sub-query or question text
        allowed_tickers: Set of valid tickers to choose from (from company_filter)

    Returns:
        Set of matched tickers from the allowed set
    """
    query_lower = query_text.lower()
    matched_tickers = set()

    for ticker in allowed_tickers:
        ticker_lower = ticker.lower()

        # Strategy 1: Exact ticker match (as standalone word)
        # Check if ticker appears as a word boundary
        if re.search(r'\b' + re.escape(ticker_lower) + r'\b', query_lower):
            matched_tickers.add(ticker)
            continue

        # Strategy 2: Company name match
        # Get the company name for this ticker
        company_name = map_ticker_to_company(ticker_lower)
        if company_name and company_name != ticker_lower:
            # Check if company name appears in query
            if company_name in query_lower:
                matched_tickers.add(ticker)
                continue

            # Strategy 3: Partial company name match
            # For multi-word company names, check if any significant word matches
            company_words = company_name.split()
            for word in company_words:
                # Skip common words
                if len(word) > 3 and word not in ['corporation', 'company', 'group', 'inc']:
                    if re.search(r'\b' + re.escape(word) + r'\b', query_lower):
                        matched_tickers.add(ticker)
                        break

    return matched_tickers


async def _hybrid_search_with_quarter_fallback(db_instance, fiscal_quarter: Optional[int], **kwargs):
    """
    Wrap hybrid_search with a safe fallback for the fiscal_quarter filter:
    try WITH the quarter filter first (precise), but if it returns nothing,
    retry WITHOUT it. This protects against the exact scenario where a
    collection has real 10-Q data that predates fiscal_quarter tagging (or
    the ingested doc's fiscal_quarter didn't resolve at ingestion time) — a
    hard filter on an untagged field would otherwise silently return zero
    results for data that's actually there and relevant.
    """
    if fiscal_quarter:
        results = await db_instance.hybrid_search(fiscal_quarter=fiscal_quarter, **kwargs)
        if results:
            return results
        logger.info(f"    No results with fiscal_quarter={fiscal_quarter} filter (likely un-tagged older data) — retrying without it")
    return await db_instance.hybrid_search(**kwargs)


def _classify_qdrant_error(e: Exception, ticker: str) -> Optional[dict]:
    """
    Classify a Qdrant lookup exception raised while querying one ticker's
    collection during retrieve(). Returns None for a "collection not found"
    error (ticker just hasn't been ingested yet — log and let the caller
    continue to the next ticker/sub-query), or a ready-to-return error
    result dict for a genuine connectivity/availability problem.
    """
    err_str = str(e).lower()
    if any(k in err_str for k in ("not found", "404", "doesn't exist", "does not exist")):
        logger.error("       Collection not found for %s (not yet ingested) — skipping", ticker)
        return None
    logger.error("       Qdrant connection error for %s: %s", ticker, e)
    return {
        "documents": [],
        "vectorstore_searched": True,
        "sub_query_results": {},
        "qdrant_error": "Vector database is currently unavailable. Please try again shortly."
    }


async def retrieve(state, config):
    """
    Retrieve documents relevant to the question using ticker-based collections.
    Supports multi-company retrieval by querying separate collections.
    """
    logger.info("="*80)
    logger.info(" TICKER-BASED RETRIEVAL (TEXT + IMAGES)")
    logger.info("="*80)
    
    messages = state["messages"]
    question = messages[-1].content
    
    # Get configuration
    thread_id = config.get("configurable", {}).get("thread_id")
    
    # Get managers
    from app.services.vectordb_manager import get_vectordb_manager
    vectordb_mgr = get_vectordb_manager()
    
    # 1. Identify Target Ticker(s)
    # ----------------------------
    # Priority:
    # 1. Ticker explicitly provided in state (from API)
    # 2. Ticker derived from company_filter
    # 3. Ticker derived from question analysis
    
    # 1. Identify Target Ticker(s)
    # ----------------------------
    # Priority:
    # 1. Identify Target Ticker(s)
    # ----------------------------
    # STRICT LOGIC:
    # - /ask endpoint provides 'company_filter' (portfolio tickers)
    # - /compare endpoint provides 'company_filter' (input tickers)
    # - 'ticker' is an optional override
    
    primary_ticker = state.get("ticker")
    # Clean up primary ticker
    if primary_ticker and (primary_ticker.lower() == "string" or not primary_ticker.strip()):
        primary_ticker = None
        
    company_filter = state.get("company_filter", [])
    
    # Use cached sub-query analysis
    sub_query_analysis = state.get("sub_query_analysis", {})
    needs_sub_queries = sub_query_analysis.get("needs_sub_queries", False)
    sub_queries = sub_query_analysis.get("sub_queries", [])
    query_type = sub_query_analysis.get("query_type", "single_company")
    
    # Extract requested years from state (set by preprocess_and_analyze_query for all paths)
    # Fall back to sub_query_analysis for backward compatibility
    requested_years = state.get("requested_years") or sub_query_analysis.get("requested_years") or [2025]

    # filing_type: resolved by preprocess_and_analyze_query via keyword heuristic
    # (+ free LLM structured-output field). None means "unresolved — search all
    # filing types", which is the pre-existing behavior for every query today.
    filing_type = state.get("filing_type")

    comparison_spans_multiple_filings = False
    comparison_span_details = None

    # SEGMENT / GEOGRAPHIC OPTIMISATION:
    # A 10-K covers the filing year + 2 prior years (3-year comparative). This
    # collapse ONLY applies to 10-K-sourced data — 10-Qs are single-quarter
    # (no 3-year comparative table) and 8-Ks are single-event (no multi-year
    # structure at all). When filing_type is None/unresolved or explicitly
    # "10-K", behavior is byte-for-byte identical to before this branch existed.
    if query_type in ("segment", "geographic") and len(requested_years) > 1:
        if filing_type in (None, "10-K"):
            year_span = requested_years[-1] - requested_years[0]
            if year_span == 2:
                comparison_spans_multiple_filings = False
                requested_years = [requested_years[-1]]
                logger.info(f" Span=2y → querying only [{requested_years[0]}] (single 10-K covers all 3 years)")
            elif year_span > 2:
                comparison_spans_multiple_filings = True
                comparison_span_details = (
                    f"Requested years {requested_years[0]}-{requested_years[-1]} span more than one "
                    f"10-K's 3-year comparative window; queried the {requested_years[0]} and "
                    f"{requested_years[-1]} 10-Ks together to cover the full range."
                )
                requested_years = [requested_years[0], requested_years[-1]]
                logger.info(f" Span={year_span}y → querying [{requested_years[0]}, {requested_years[-1]}] (first+last 10-K covers full range)")
        elif filing_type == "10-Q":
            # No 3-year comparative window on a 10-Q — query each requested
            # year/quarter individually rather than collapsing.
            logger.info(f" filing_type=10-Q → no 3-year window collapse, querying each requested year individually: {requested_years}")
            if len(requested_years) > 1:
                comparison_spans_multiple_filings = True
                comparison_span_details = (
                    f"Requested years {requested_years} span multiple 10-Q filings (each a single "
                    f"quarter, not directly comparable to a 10-K's audited annual figures)."
                )
        elif filing_type == "8-K":
            # Single point-in-time event — a multi-year span is a no-op here,
            # just keep the requested years as-is (no comparative structure to collapse).
            logger.info(f" filing_type=8-K → single-event filing, no window collapse applied: {requested_years}")

    target_tickers = set()

    # Strategy: Strictly use provided inputs
    
    # 1. From Portfolio/Input (company_filter) - THIS IS THE SOURCE OF TRUTH
    if company_filter:
        for c in company_filter:
            if c and isinstance(c, str) and c.strip():
                # Try to map to ticker first (in case full name was provided)
                found_ticker = get_ticker(c.strip())
                if found_ticker:
                    target_tickers.add(found_ticker)
                else:
                    # Fallback: assume it might be a ticker already
                    target_tickers.add(c.strip().lower())
    
    # 2. From API Override (primary_ticker)
    # If provided, does it restrict the search or add to it?
    # Usually 'ticker' param in /ask is meant to focus on one company.
    if primary_ticker:
         # If primary_ticker is provided, we focus ONLY on it (override), 
         # or we add it? 
         # Given the user wants simplicity, if they explicitly asked for a ticker, 
         # they probably want that specific one. 
         # But to be safe and support "portfolio + specific question", let's just make sure it's included.
         target_tickers.add(primary_ticker)

    # 3. From LLM Analysis (companies_detected)
    companies_detected = state.get("companies_detected", [])
    if companies_detected:
        for company_name in companies_detected:
            if company_name and isinstance(company_name, str):
                # Try to map name to ticker
                found_ticker = get_ticker(company_name)
                if found_ticker:
                    target_tickers.add(found_ticker)
                else:
                    # If it's already a ticker-like string and from a trusted source, we can try using it directly 
                    # but only if it's 2-5 chars and all uppercase
                    if 2 <= len(company_name) <= 5 and company_name.isupper():
                        target_tickers.add(company_name.lower())

    logger.info(f" Identified Target Tickers: {list(target_tickers) or 'None'}")

    # If primary_ticker was empty, set it to the first found ticker for downstream consistency
    if not primary_ticker and target_tickers:
        primary_ticker = list(target_tickers)[0]

    # requested_fiscal_quarter: resolved by preprocess_and_analyze_query from
    # plain "Q1"/"first quarter" phrasing — an unambiguous quarter NUMBER.
    # Whether that number means the same calendar months for every company
    # in this query depends on each ticker's fiscal calendar, checked next.
    requested_fiscal_quarter = state.get("requested_fiscal_quarter")

    if requested_fiscal_quarter and len(target_tickers) > 1:
        from app.utils.company_mapping import get_fiscal_year_end_month, get_fiscal_quarter_calendar_span
        fye_months = {t: get_fiscal_year_end_month(t) for t in target_tickers}
        if len(set(fye_months.values())) > 1:
            # Different fiscal calendars → the SAME quarter number covers
            # DIFFERENT calendar months for each company. Flag it so the
            # prompt warns the user instead of presenting a clean comparison.
            comparison_spans_multiple_filings = True
            spans = ", ".join(
                f"{t.upper()} ({get_fiscal_quarter_calendar_span(t, requested_fiscal_quarter)})"
                for t in sorted(target_tickers)
            )
            note = (
                f"Requested fiscal Q{requested_fiscal_quarter} does not cover the same calendar "
                f"months for every company here, since they have different fiscal year ends: {spans}."
            )
            comparison_span_details = (
                f"{comparison_span_details} {note}" if comparison_span_details else note
            )
            logger.info(f" Fiscal quarter misalignment detected: {note}")

    # Only meaningful (and only ever tagged) on 10-Q chunks — applying it
    # when filing_type isn't resolved to "10-Q" would incorrectly exclude
    # 10-K/8-K chunks that never carry this field.
    fiscal_quarter_filter = requested_fiscal_quarter if filing_type == "10-Q" else None

    # ============================================================================
    # SUB-QUERY MODE: Targeted retrieval for each sub-query (Multi-Collection)
    # ============================================================================
    all_documents = []
    sub_query_results = {}
    seen_doc_ids = set()
    
    if needs_sub_queries and sub_queries:
        logger.info(f"\n SUB-QUERY MODE: {len(sub_queries)} data points")
        logger.info("-" * 80)
        
        for i, sq in enumerate(sub_queries, 1):
            logger.info(f"\n {i}/{len(sub_queries)}: {sq}")

            # Intelligently detect which tickers are mentioned in THIS sub-query
            sq_tickers_for_step = detect_tickers_in_query(sq, target_tickers)

            # If no specific ticker detected, query ALL allowed tickers
            # (This handles cases where the sub-query doesn't explicitly mention a company)
            if not sq_tickers_for_step:
                logger.info(f"     No specific company detected, querying all: {list(target_tickers)}")
                sq_tickers_for_step = target_tickers
            else:
                logger.info(f"    Detected companies: {list(sq_tickers_for_step)}")
            
            if not sq_tickers_for_step:
                logger.warning(f"    No allowed tickers found. Skipping vector search.")
                sub_query_results[sq] = {"found": False, "doc_count": 0, "preview": None, "companies": [], "content_types": {'text': 0, 'image': 0}}
                continue
            
            # Query each relevant ticker collection for this sub-query
            step_docs = []
            for t_ticker in sq_tickers_for_step:
                try:
                    company_name = map_ticker_to_company(t_ticker.lower())
                    logger.info(f"    Querying ticker_{t_ticker.lower()} ({company_name})...")

                    # Get instance for this ticker (DO NOT CREATE if missing)
                    db_instance = vectordb_mgr.get_instance(t_ticker, create_if_missing=False)

                    # Perform search per requested year to ensure representation
                    docs_from_ticker = 0
                    for year_filter in requested_years:
                        search_results = await _hybrid_search_with_quarter_fallback(
                            db_instance,
                            fiscal_quarter=fiscal_quarter_filter,
                            query=sq,
                            content_type=None,
                            years=[year_filter],
                            filing_type=filing_type,
                            limit=5, # Reduced limit per ticker/sub-query
                            dense_limit=50,
                            sparse_limit=50
                        )

                        # Convert to Document objects
                        for point in search_results:
                            if hasattr(point, 'payload'):
                                content = point.payload.get('page_content', '')
                                metadata = point.payload.get('metadata', {})
                                # Ensure company metadata is set if missing
                                if 'company' not in metadata: metadata['company'] = t_ticker
                                doc = Document(page_content=content, metadata=metadata)
                                step_docs.append(doc)
                                docs_from_ticker += 1

                    if docs_from_ticker > 0:
                        logger.info(f"       Found {docs_from_ticker} chunks")
                    else:
                        logger.info(f"        No chunks found")

                except Exception as e:
                    err_result = _classify_qdrant_error(e, t_ticker)
                    if err_result is not None:
                        return err_result

            # Deduplicate and Collect results for this sub-query
            companies_found = set()
            content_types = {'text': 0, 'image': 0}
            
            for doc in step_docs:
                doc_id = f"{doc.metadata.get('company','')}_{doc.metadata.get('source_file','')}_{doc.metadata.get('page_num','')}_{doc.page_content[:50]}"
                
                if doc_id not in seen_doc_ids:
                    seen_doc_ids.add(doc_id)
                    all_documents.append(doc)
                
                # Update stats for sub-query result
                companies_found.add(doc.metadata.get('company', 'Unknown'))
                ctype = doc.metadata.get('content_type', 'text')
                content_types[ctype] = content_types.get(ctype, 0) + 1

            sub_query_results[sq] = {
                "found": len(step_docs) > 0,
                "doc_count": len(step_docs),
                "preview": step_docs[0].page_content[:200] if step_docs else None,
                "companies": list(companies_found),
                "content_types": content_types
            }

            if len(step_docs) > 0:
                logger.info(f"    Total: {len(step_docs)} chunks from {len(companies_found)} companies")
            else:
                logger.info(f"    No chunks found for this sub-query")

    else:
        # ============================================================================
        # DIRECT MODE: Retrieval from one or more collections
        # ============================================================================
        logger.info(f"\n DIRECT RETRIEVAL MODE")
        logger.info("-" * 80)
        
        if not target_tickers:
             logger.info(" No target tickers identified. Cannot perform vector search.")
             logger.info(" Returning EMPTY (will trigger web search)")
             all_documents = []
        else:
            logger.info(f" Searching collections for tickers: {', '.join(target_tickers)}")
            
            # Iterate through all identified tickers and merge results
            for target_ticker in target_tickers:
                try:
                    logger.info(f"    Querying collection: ticker_{target_ticker}")
                    # DO NOT CREATE if missing
                    db_instance = vectordb_mgr.get_instance(target_ticker, create_if_missing=False)
                    
                    current_collection_docs = 0
                    for year_filter in requested_years:
                        search_results = await _hybrid_search_with_quarter_fallback(
                            db_instance,
                            fiscal_quarter=fiscal_quarter_filter,
                            query=question,
                            content_type=None,
                            years=[year_filter],
                            filing_type=filing_type,
                            limit=10,
                            dense_limit=100,
                            sparse_limit=100
                        )
                        
                        # Convert to Documents and Deduplicate
                        for point in search_results:
                            if hasattr(point, 'payload'):
                                content = point.payload.get('page_content', '')
                                metadata = point.payload.get('metadata', {})
                                
                                # Create a unique ID for deduplication
                                # Use source_file + page_num + content hash equivalent
                                doc_id = f"{metadata.get('company', target_ticker)}_{metadata.get('source_file','')}_{metadata.get('page_num','')}_{content[:50]}"
                                
                                if doc_id not in seen_doc_ids:
                                    seen_doc_ids.add(doc_id)
                                    doc = Document(page_content=content, metadata=metadata)
                                    all_documents.append(doc)
                                    current_collection_docs += 1
                                    
                    logger.info(f"       Found {current_collection_docs} unique chunks across requested years")
                    
                except Exception as e:
                    err_result = _classify_qdrant_error(e, target_ticker)
                    if err_result is not None:
                        return err_result
            
            # Final stats
            content_types = {'text': 0, 'image': 0}
            companies_found = set()
            for doc in all_documents:
                if hasattr(doc, 'metadata'):
                    ctype = doc.metadata.get('content_type', 'text')
                    content_types[ctype] = content_types.get(ctype, 0) + 1
                    companies_found.add(doc.metadata.get('company', 'Unknown'))

            logger.info(f"\nRetrieved {len(all_documents)} chunks total from {len(target_tickers)} collections")
            logger.info(f"    {content_types['text']} text,  {content_types['image']} images")
            logger.info(f"    {', '.join(sorted(companies_found))}")

    # Final summary
    logger.info(f"\n{'='*80}")
    logger.info(f" FINAL: {len(all_documents)} chunks ready")
    logger.info(f"{'='*80}\n")
    
    return {
        "documents": all_documents,
        "vectorstore_searched": True,
        "sub_query_results": sub_query_results,
        "ticker": primary_ticker,  # Store resolved ticker in state
        "filing_type": filing_type,
        "comparison_spans_multiple_filings": comparison_spans_multiple_filings,
        "comparison_span_details": comparison_span_details,
        "requested_fiscal_quarter": requested_fiscal_quarter
    }


def generate(state):
    logger.info("---GENERATE---")
    messages = state["messages"]
    question = messages[-1].content
    documents = state["documents"]

    # Short-circuit: surface Qdrant connection errors instead of hallucinating
    qdrant_error = state.get("qdrant_error")
    if qdrant_error:
        logger.error(f" QDRANT ERROR — returning user-facing message: {qdrant_error}")
        return {"Intermediate_message": qdrant_error, "retry_count": state.get("retry_count", 0) + 1}
    
    # Enhanced logging for debugging
    logger.info(f" Question: {question[:100]}...")
    logger.info(f" Number of chunks: {len(documents) if documents else 0}")

    # Log chunk content preview for debugging
    if documents:
        for i, doc in enumerate(documents[:3]):  # Preview first 3 chunks
            if hasattr(doc, 'page_content'):
                content_preview = doc.page_content[:200].replace('\n', ' ')
            else:
                content_preview = str(doc)[:200].replace('\n', ' ')
            logger.info(f" Chunk {i+1} preview: {content_preview}...")
    else:
        logger.warning(" WARNING: No chunks available for generation!")
    
    # Context-free mode - no conversation memory
    enriched_question = question
    
    logger.info("---USING STANDARD GENERATION---")
    
    # CRITICAL: Smart truncate documents to prevent context overflow
    # GPT-4o has 128k token limit (~96k chars safe limit)
    total_chars = sum(len(doc.page_content) for doc in documents)
    MAX_TOTAL_CHARS = 150000  # Safe limit for generation
    
    if total_chars > MAX_TOTAL_CHARS:
        logger.info(f"[DOC SIZE] {total_chars:,} chars exceeds limit ({MAX_TOTAL_CHARS:,}). Truncating ONLY web search documents.")
        
        # separate docs by source
        vector_docs = []
        web_docs = []
        for doc in documents:
            source = doc.metadata.get("source", "")
            if source in ["web_search", "integrate_web_search"]:
                web_docs.append(doc)
            else:
                vector_docs.append(doc)
                
        vector_chars = sum(len(d.page_content) for d in vector_docs)
        remaining_budget = MAX_TOTAL_CHARS - vector_chars
        
        if remaining_budget <= 0:
            # If vector docs alone exceed budget (very rare), we have to proportionally truncate everything
            logger.warning(f"[DOC SIZE] WARNING: Vectorstore docs exceed total budget ({vector_chars:,} chars). Absolute truncation required.")
            budget_per_doc = MAX_TOTAL_CHARS // max(len(vector_docs), 1)
            documents = [Document(page_content=d.page_content[:budget_per_doc], metadata=d.metadata) for d in vector_docs]
        elif web_docs:
            logger.info(f"[DOC SIZE] Vectorstore docs take {vector_chars:,} chars. Truncating {len(web_docs)} web chunks into remaining {remaining_budget:,} chars.")
            budget_per_web_doc = remaining_budget // len(web_docs)
            truncated_web = []
            for doc in web_docs:
                if len(doc.page_content) <= budget_per_web_doc:
                    truncated_web.append(doc)
                else:
                    truncated_web.append(Document(
                        page_content=doc.page_content[:budget_per_web_doc] + "...[TRUNCATED]",
                        metadata=doc.metadata
                    ))
            documents = vector_docs + truncated_web
            
        total_chars = sum(len(doc.page_content) for doc in documents)
        logger.info(f"[DOC SIZE] After truncation: {total_chars:,} chars")
    else:
        logger.info(f"[DOC SIZE] {total_chars:,} chars (limit: {MAX_TOTAL_CHARS:,})")
    
    llm = ChatOpenAI(
        model="gpt-4o",
        temperature=0.3,
        timeout=30,  # Set timeout to prevent hanging
        request_timeout=30,
        max_retries=2
    )
    # Extract query type and alpha pillar to format prompt appropriately
    sub_query_analysis = state.get("sub_query_analysis", {})
    query_type = sub_query_analysis.get("query_type", "general")
    alpha_pillar = state.get("alpha_pillar")  # e.g. "insider_trading", "alignment", etc.

    if alpha_pillar:
        logger.info(f" [GENERATE] Alpha pillar mode: {alpha_pillar}")

    comparison_span_note = state.get("comparison_span_details") if state.get("comparison_spans_multiple_filings") else None
    rag_chain = get_rag_chain(llm, query_type=query_type, alpha_pillar=alpha_pillar, comparison_span_note=comparison_span_note)
    
    generation_input = {
        "documents": documents,
        "question": enriched_question
    }
    
    Intermediate_message = rag_chain.invoke(generation_input)

    retry_count = state.get("retry_count", 0)

    return {
        "Intermediate_message": Intermediate_message,
        "retry_count": retry_count + 1
    }


def grade_documents(state):
    """
    FINANCIAL ANALYST CHUNK GRADING: Evaluates retrieved chunks like a financial analyst.

    1. Identifies what financial metrics the question needs
    2. Scans chunks to find which metrics ARE present
    3. Identifies which metrics are MISSING
    4. Returns grading result used by decide_to_generate
    """
    logger.info("---FINANCIAL ANALYST CHUNK GRADING---")
    messages = state["messages"]
    question = messages[-1].content
    documents = state["documents"]
    web_searched = state.get("web_searched", False)
    
    # Get query context
    sub_query_analysis = state.get("sub_query_analysis", {})
    query_type = sub_query_analysis.get("query_type", "single_company")
    companies_detected = sub_query_analysis.get("companies_detected", [])
    
    logger.info(f"Query Type: {query_type}")
    
    # Context-aware Company Detection
    # If no companies detected in question, use context from portfolio/state
    if not companies_detected:
        ctx_ticker = state.get("ticker")
        ctx_filter = state.get("company_filter", [])
        
        if ctx_ticker:
            companies_detected = [ctx_ticker]
            logger.info(f"Using context ticker: {ctx_ticker}")
        elif ctx_filter:
            companies_detected = ctx_filter
            logger.info(f"Using portfolio context companies: {ctx_filter}")
            
    logger.info(f"Companies Detected: {companies_detected}")
    logger.info(f"Chunks to grade: {len(documents)}")

    # CRITICAL: Handle empty chunks case (e.g., company not in DB)
    if not documents or len(documents) == 0:
        logger.info(" NO CHUNKS TO GRADE")
        logger.info(" Returning INSUFFICIENT grade → Will trigger web search")

        return {
            "documents": [],
            "financial_grading": {
                "overall_grade": "insufficient",
                "can_answer": False,
                "missing_data_summary": "No chunks found in vector database",
                "company_coverage": [],
                "documents_graded_count": 0
            }
        }
    
    # Initialize financial analyst grader with gpt-4o
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    analyst_grader = get_financial_analyst_grader_chain(llm)

    # Concatenate all documents into a single massive context window
    # gpt-4o has a 128k context window, allowing us to pass up to ~80k-100k chars easily
    doc_previews = []
    total_chars = 0
    MAX_CHARS = 100000 
    
    for i, doc in enumerate(documents, 1):
        if hasattr(doc, 'page_content'):
            content = doc.page_content
        elif isinstance(doc, dict) and 'page_content' in doc:
            content = doc['page_content']
        else:
            content = str(doc)

        metadata_str = ""
        if hasattr(doc, 'metadata'):
            company = doc.metadata.get("company", "Unknown")
            source = doc.metadata.get("source", "Unknown")
            metadata_str = f" [Company: {company}, Source: {source}]"
            
        preview = f"--- Document {i} ---{metadata_str}\n{content}\n"
        
        if total_chars + len(preview) > MAX_CHARS:
            # Add a truncated version of the last document that fits
            remaining = MAX_CHARS - total_chars
            if remaining > 100:
                doc_previews.append(preview[:remaining] + "...[TRUNCATED TO FIT CONTEXT]")
            break
            
        doc_previews.append(preview)
        total_chars += len(preview)

    doc_preview_text = "\n".join(doc_previews)
    logger.info(f"  Sending {len(doc_previews)} documents ({total_chars} chars) to gpt-4o grader...")

    sub_queries = "\n".join([f"- {sq}" for sq in sub_query_analysis.get("sub_queries", [])]) if sub_query_analysis.get("sub_queries") else "None"

    try:
        # Perform single LLM call
        grade = analyst_grader.invoke({
            "question": question,
            "sub_queries": sub_queries,
            "doc_content": doc_preview_text
        })
        
        logger.info(f"\n FINANCIAL ANALYST GRADE:")
        logger.info(f"  Is Sufficient: {grade.is_sufficient}")
        if grade.missing_data_summary:
            logger.warning(f"  Missing Data: {grade.missing_data_summary}")
        
        overall_grade = "sufficient" if grade.is_sufficient else "insufficient"
        
        # Store grading result in state for decision-making
        grading_result = {
            "overall_grade": overall_grade,
            "can_answer": grade.is_sufficient,
            "missing_data_summary": grade.missing_data_summary,
            "company_coverage": [], # Removed complex coverage tracking
            "documents_graded_count": len(doc_previews)
        }

        logger.info(f"\n GRADING COMPLETE: {len(documents)} chunks evaluated")
        logger.info(f"   Next: Decision node will use this grading to determine if web search needed")

        return {
            "documents": documents,
            "financial_grading": grading_result
    }

    except Exception as e:
        logger.error(f" Financial analyst grading failed: {e}")
        logger.error("  Falling back to keeping all chunks")

        # Fallback: keep all chunks
        return {
            "documents": documents,
            "financial_grading": {"overall_grade": "partial", "can_answer": False, "error": str(e)}
    }


def web_search(state):
    """
    Direct web search when question needs current/real-time data.
    Uses enriched query for better search results with proper context.
    Restricted to trusted financial domains for data quality.
    Creates separate documents per source for better context utilization.
    
    NOW WITH UNIVERSAL SUB-QUERY SUPPORT:
    - Works for ALL query types (financial calculations, multi-company, multi-part)
    - Individual searches for each sub-query with deduplication
    - Tracks missing data for fallback handling
    """
    logger.info("---WEB SEARCH (TRUSTED FINANCIAL DOMAINS ONLY)---")
    messages = state["messages"]
    question = messages[-1].content
    enriched_query = state.get("enriched_query", question)
    
    # Use universal sub-query analysis
    sub_query_analysis = state.get("sub_query_analysis", {})
    needs_sub_queries = sub_query_analysis.get("needs_sub_queries", False)
    sub_queries = sub_query_analysis.get("sub_queries", [])
    companies_detected = sub_query_analysis.get("companies_detected", [])
    
    # Optimize search query for SEC filings
    search_query = enriched_query if enriched_query != question else question
    question_lower = question.lower()
    is_sec_filing_query = any(kw in question_lower for kw in
        ['10-k', '10k', '10-q', '10q', '8-k', '8k', 'annual report', 'md&a', 'mda',
         'management discussion', 'sec filing', 'edgar'])

    target_company = companies_detected[0] if companies_detected else None

    if is_sec_filing_query and target_company:
        logger.info(f"---SEC FILING QUERY DETECTED FOR {target_company.upper()}---")
        years = re.findall(r'\b(20\d{2})\b', question)

        # Vary the SEC filing type in the search query by what's actually
        # detected/inferred, instead of always assuming 10-K — a "latest
        # quarter" or "recent announcement" question shouldn't be forced
        # into a 10-K-only web search.
        detected_filing_type = state.get("filing_type") or detect_filing_type_in_query(question) or "10-K"

        if 'md&a' in question_lower or 'management discussion' in question_lower:
            search_query = f"{target_company} MD&A Management Discussion Analysis {' '.join(years) if years else ''} SEC {detected_filing_type} site:sec.gov"
        else:
            search_query = f"{target_company} {detected_filing_type} {' '.join(years) if years else ''} site:sec.gov"
        logger.info(f"✓ Optimized search: {search_query}")
    
    # UNIVERSAL SUB-QUERY WEB SEARCH
    web_search_tool = TavilySearch(
        max_results=5, 
        include_raw_content=True,
        include_domains=TRUSTED_FINANCIAL_DOMAINS
    )
    
    documents = []
    total_chars = 0
    
    if sub_queries:
        logger.info(f"---SUB-QUERY MODE: Searching individually for {len(sub_queries)} specific data points---")
        seen_doc_ids = set()
        
        for i, sq in enumerate(sub_queries, 1):
            logger.info(f"   {i}. Web searching for: {sq}")
            
            # Search specifically for this data point
            docs = web_search_tool.invoke({"query": sq})
            sources = _parse_tavily_response(docs, sq)
            
            for source in sources:
                doc_content = f"**Source: {source['title']}**\n"
                if source['url']:
                    doc_content += f"URL: {source['url']}\n\n"
                doc_content += source['content']
                
                # Deduplicate by URL
                doc_id = source['url'] if source['url'] else doc_content[:100]
                if doc_id not in seen_doc_ids:
                    seen_doc_ids.add(doc_id)
                    doc = Document(
                        page_content=doc_content,
                        metadata={
                            "source": "web_search",
                            "title": source['title'],
                            "url": source['url']
                        }
                    )
                    documents.append(doc)
                    total_chars += len(source['content'])
            
            logger.info(f"      → Found {len(sources)} sources, {len(documents)} chunks unique total")

        logger.info(f" ✓ Retrieved {len(documents)} unique chunks across all sub-queries")
    else:
        # Standard single search
        if search_query != question:
            logger.info(f"Using optimized query for web search: {search_query[:150]}")
        else:
            logger.info(f"Using original question for web search: {search_query[:150]}")

        logger.info(f" Restricting search to {len(TRUSTED_FINANCIAL_DOMAINS)} trusted financial domains")
        docs = web_search_tool.invoke({"query": search_query})

        # Parse Tavily response into source chunks
        sources = _parse_tavily_response(docs, search_query)

        for source in sources:
            doc_content = f"**Source: {source['title']}**\n"
            if source['url']:
                doc_content += f"URL: {source['url']}\n\n"
            doc_content += source['content']

            doc = Document(
                page_content=doc_content,
                metadata={
                    "source": "web_search",
                    "title": source['title'],
                    "url": source['url']
                }
            )
            documents.append(doc)
            total_chars += len(source['content'])

        logger.info(f"Web search produced {len(documents)} chunks ({total_chars} total chars)")
    
    if not documents or total_chars < 100:
        logger.warning("WARNING: Web search returned minimal content, response may be incomplete")

    # Track sub-query results from web search
    sub_query_results = state.get("sub_query_results", {})
    if sub_queries and documents:
        logger.info("---EXTRACTING SUB-QUERY RESULTS FROM WEB SEARCH---")
        for sq in sub_queries:
            if sq not in sub_query_results:
                sub_query_results[sq] = {"found": False, "doc_count": 0, "sources": []}
            
            matched_docs = 0
            for doc in documents:
                sq_keywords = sq.lower().split()
                doc_content = doc.page_content.lower()
                if any(keyword in doc_content for keyword in sq_keywords if len(keyword) > 3):
                    sub_query_results[sq]["sources"].append(doc.page_content[:500])
                    matched_docs += 1
            
            if matched_docs > 0:
                sub_query_results[sq]["found"] = True
                sub_query_results[sq]["doc_count"] = matched_docs
        
        found_count = sum(1 for sq_data in sub_query_results.values() if isinstance(sq_data, dict) and sq_data.get("found", False))
        logger.info(f" Updated sub-query results: {found_count}/{len(sub_queries)} have data")

    return {
        "documents": documents,
        "web_searched": True,
        "sub_query_results": sub_query_results
    }


def _parse_tavily_response(docs, query):
    """
    Helper function to properly parse Tavily search response.
    Handles various response formats from TavilySearch.
    Returns list of individual source documents instead of combined content.
    """
    sources = []
    
    # Debug: Log raw response type
    logger.info(f"Tavily response type: {type(docs)}")

    # ── Error detection ────────────────────────────────────────────────────────
    # TavilySearch wraps API / network errors as {'error': <Exception>} instead
    # of raising.  Detect this early so callers' except-blocks log a clean
    # message instead of the misleading "Could not parse" warning.
    if isinstance(docs, dict) and 'error' in docs and len(docs) == 1:
        err = docs['error']
        err_msg = str(err) if not isinstance(err, str) else err
        raise RuntimeError(f"Tavily API error: {err_msg}")
    
    if isinstance(docs, str):
        # Already a string, return as single source
        return [{"title": "Web Search Result", "url": "", "content": docs}]
    
    if isinstance(docs, dict):
        # Handle dict response (may have 'results' key or direct content)
        if 'results' in docs:
            results = docs['results']
        elif 'answer' in docs and docs['answer']:
            # Tavily can return a direct answer
            sources.append({
                "title": "Direct Answer",
                "url": "",
                "content": docs['answer']
            })
            results = docs.get('results', [])
        else:
            results = [docs]  # Treat the whole dict as a single result
        
        for i, result in enumerate(results, 1):
            if isinstance(result, dict):
                title = result.get('title', 'No Title')
                url = result.get('url', '')
                # Try multiple content fields - Tavily uses different field names
                content = (
                    result.get('raw_content') or 
                    result.get('content') or 
                    result.get('snippet') or 
                    result.get('text') or
                    result.get('description', '')
                )
                
                if content:
                    sources.append({
                        "title": title,
                        "url": url,
                        "content": content
                    })
                    logger.info(f"  Source {i}: {title[:50]}... ({len(content)} chars)")
    
    elif isinstance(docs, list):
        # Handle list of results directly
        for i, d in enumerate(docs, 1):
            if isinstance(d, dict):
                title = d.get('title', 'No Title')
                url = d.get('url', '')
                content = (
                    d.get('raw_content') or
                    d.get('content') or 
                    d.get('snippet') or 
                    d.get('text') or
                    d.get('description', '')
                )
                
                if content:
                    sources.append({
                        "title": title,
                        "url": url,
                        "content": content
                    })
                    logger.info(f"  Source {i}: {title[:50]}... ({len(content)} chars)")
            elif isinstance(d, str):
                sources.append({
                    "title": "Web Search Result",
                    "url": "",
                    "content": d
                })
    
    if not sources:
        # Fallback: convert entire response to string
        logger.warning("WARNING: Could not parse Tavily response structure, using raw output")
        return [{"title": "Web Search Result", "url": "", "content": str(docs)}]
    
    return sources


def integrate_web_search(state):
    """
    WEB SEARCH INTEGRATION: Builds a single query from missing data + ticker + company name,
    executes one search, and combines results with existing vectorstore documents.
    """
    logger.info("---INTEGRATE WEB SEARCH---")
    messages = state["messages"]
    question = messages[-1].content
    existing_documents = state.get("documents", [])

    # Resolve company and ticker identifiers
    company_filter = state.get("company_filter", [])
    companies_detected = state.get("sub_query_analysis", {}).get("companies_detected", []) or state.get("companies_detected", [])
    ticker = state.get("ticker", "")

    company = ""
    if company_filter:
        company = company_filter[0] if isinstance(company_filter, list) else company_filter
    elif companies_detected:
        company = companies_detected[0]

    # Build a single combined query using ticker, company name, and missing data summary
    financial_grading = state.get("financial_grading", {})
    missing_summary = financial_grading.get("missing_data_summary", "")

    query_parts = []
    # Avoid duplicate terms (like repeating company name multiple times)
    if company and company.lower() not in [q.lower() for q in query_parts]:
        query_parts.append(company)
    
    if ticker and ticker.lower() not in [q.lower() for q in query_parts] and ticker.lower() != company.lower():
        query_parts.append(ticker)

    missing_summary_str = ""
    if missing_summary:
        missing_summary_str = str(missing_summary).strip()
        
    logger.warning(f"  [DEBUG] grading missing_summary: {repr(missing_summary)}")
    
    # Is there a valid missing data summary? (Not None, not empty, and not specifically 'no chunks found in vector database')
    has_valid_missing_target = (
        bool(missing_summary_str) and 
        missing_summary_str.lower() != "none" and 
        "no chunks found" not in missing_summary_str.lower()
    )

    if has_valid_missing_target:
        # Missing data summary is the target - use it directly
        logger.warning("  [DEBUG] Using missing data summary for web search target.")
        query_parts.append(missing_summary_str)
    else:
        # Fallback to the original question only if there is no explicit missing data summary
        logger.info("  [DEBUG] Using original question for web search fallback.")
        query_parts.append(question)

    search_query = " ".join(query_parts)
    logger.info(f"  Search query: {search_query}")

    web_search_tool = TavilySearch(
        max_results=5,
        include_raw_content=True,
        include_domains=TRUSTED_FINANCIAL_DOMAINS
    )

    web_documents = []
    seen_doc_ids = set()
    total_chars = 0

    try:
        docs = web_search_tool.invoke({"query": search_query})
        sources = _parse_tavily_response(docs, search_query)
        logger.info(f"  Found {len(sources)} sources")

        for source in sources:
            doc_id = source["url"] if source["url"] else source["content"][:100]
            if doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)
            doc_content = f"**Source: {source['title']}**\n"
            if source["url"]:
                doc_content += f"URL: {source['url']}\n\n"
            doc_content += source["content"]
            web_documents.append(Document(
                page_content=doc_content,
                metadata={
                    "source": "integrate_web_search",
                    "title": source["title"],
                    "url": source["url"],
                    "search_query": search_query
    }
            ))
            total_chars += len(source["content"])
    except Exception as e:
        logger.error(f"  ERROR during web search: {e}")

    combined_documents = existing_documents + web_documents
    logger.info(f"  Existing chunks: {len(existing_documents)} | New web chunks: {len(web_documents)} | Total: {len(combined_documents)}")

    return {
        "documents": combined_documents,
        "web_searched": True
    }


_NUMERIC_CLAIM_PATTERN = re.compile(
    r'\$\s?[\d,]+(?:\.\d+)?|\b\d+(?:\.\d+)?\s?%|\b\d+(?:\.\d+)?\s?(?:million|billion|trillion)\b',
    re.IGNORECASE,
)


def verify_grounding(state):
    """
    Lightweight post-generation grounding check for NUMERIC claims only —
    replaces the old get_hallucination_chain/get_answer_quality_chain, which
    were built but never actually wired into the graph (confirmed dead code
    during a repo audit). This one runs for real, right after generate().

    Deliberately narrow (numeric claims only, not a full hallucination
    grader) rather than deliberately cheap — accuracy matters more than
    model cost here, since a wrong "correction" is worse than not checking
    at all (see below), and the regex gate already bounds how often this
    runs at all:
    1. A regex gate skips the entire check (zero added latency/cost) when
       the generated answer makes no numeric claims at all — most
       qualitative/MD&A-style answers hit this and pay nothing extra.
    2. When numeric claims ARE present, a full-strength gpt-4o call verifies
       them against the source documents. gpt-4o-mini was tried first and
       reliably false-positived on unit conversions (flagging "$37.8B" as
       unsupported when the source said "$37,791 million" — the same
       number) — not accurate enough for arithmetic-sensitive verification.
    3. If any claim is unsupported, ONE targeted correction pass rewrites
       just the flagged claims (not a full regeneration). This node sits on
       a straight-line edge (generate -> verify_grounding -> decide_chart),
       so it only ever runs once per query — no loop/retry-counter needed.
    4. Any failure in the check/correction itself fails OPEN (returns the
       original answer unchanged) rather than blocking the response —
       a grounding-check outage must never take down normal answers.
    """
    generation = state.get("Intermediate_message", "")
    documents = state.get("documents", [])

    if not generation or not documents:
        return {}

    if not _NUMERIC_CLAIM_PATTERN.search(generation):
        logger.info("[GROUNDING] No numeric claims in answer — skipping check (zero added latency)")
        return {}

    from rag.prompts.prompts import get_grounding_check_chain, get_grounding_correction_chain

    grounding_llm = ChatOpenAI(model="gpt-4o", temperature=0, timeout=20, max_retries=1)

    doc_text = "\n\n".join(
        (doc.page_content[:1500] if hasattr(doc, "page_content") else str(doc)[:1500])
        for doc in documents[:15]
    )

    try:
        check_chain = get_grounding_check_chain(grounding_llm)
        result = check_chain.invoke({"documents": doc_text, "generation": generation})
    except Exception as e:
        logger.warning(f"[GROUNDING] Check failed, skipping (fail-open): {e}")
        return {}

    if result.is_grounded or not result.unsupported_claims:
        logger.info("[GROUNDING] All numeric claims verified against source documents")
        return {}

    logger.warning(f"[GROUNDING] Unsupported claims found: {result.unsupported_claims}")

    try:
        correction_chain = get_grounding_correction_chain(grounding_llm)
        corrected = correction_chain.invoke({
            "documents": doc_text,
            "generation": generation,
            "unsupported_claims": "\n".join(f"- {c}" for c in result.unsupported_claims),
        })
        logger.info("[GROUNDING] Answer corrected — unsupported claims fixed/flagged as not disclosed")
        return {"Intermediate_message": corrected}
    except Exception as e:
        logger.warning(f"[GROUNDING] Correction pass failed — flagging original answer instead: {e}")
        flagged = (
            generation
            + "\n\n---\n**Note**: the following figures could not be verified against the source "
            + "documents — please double-check: " + "; ".join(result.unsupported_claims)
        )
        return {"Intermediate_message": flagged}


def show_result(state):
    logger.info("---SHOW RESULT---")
    Final_answer = AIMessage(content=state["Intermediate_message"])

    logger.info(f'SHOWING THE RESULTS: {Final_answer}')
    return {
        "messages": Final_answer
    }



def parse_markdown_table(text):
    """
    Parse markdown table from the response text.
    Returns a dictionary of metrics with values for 2 or 3 companies.
    Automatically detects the number of companies based on table columns.
    """
    lines = text.split('\n')
    table_started = False
    metrics_data = {}
    num_companies = None
    header_cells = []
    
    for line in lines:
        # Check if this is a table row (must have at least 2 pipes and content)
        if '|' in line and line.count('|') >= 3:  # At least | col1 | col2 | col3 |
            # Skip separator lines
            if line.strip().startswith('|---') or set(line.replace('|', '').replace('-', '').replace(' ', '').replace(':', '')) == set():
                continue
                
            cells = [cell.strip() for cell in line.split('|')]
            # Remove empty cells from start/end (markdown tables often have | at both ends)
            cells = [c for c in cells if c]
            
            if len(cells) >= 3:
                # Check if this is the header row
                first_cell_lower = cells[0].lower()
                if 'metric' in first_cell_lower and not table_started:
                    table_started = True
                    header_cells = cells
                    # Count actual company columns (exclude Metric and Comparison columns)
                    num_companies = 0
                    for i, cell in enumerate(cells[1:], 1):  # Skip first column (Metric)
                        cell_lower = cell.lower()
                        # Check if this is a company column (not "comparison" or similar)
                        if 'comparison' not in cell_lower and cell_lower not in ['', 'difference', 'notes']:
                            num_companies += 1
                        else:
                            break  # Stop counting when we hit comparison/notes columns
                    
                    logger.info(f" Detected {num_companies} company/companies in table")
                    logger.info(f"  Header columns: {cells}")
                    continue
                
                # Process data rows
                if table_started and num_companies:
                    metric_name = cells[0].replace('**', '').strip()
                    
                    # Skip empty or non-quantitative metrics
                    if not metric_name or metric_name.lower() in ['risk factors', 'profit drivers', 'profit/loss contributing factors']:
                        continue
                    
                    # Extract company values based on detected number of companies
                    if num_companies == 2 and len(cells) >= 3:
                        metrics_data[metric_name] = {
                            'company1': cells[1].strip(),
                            'company2': cells[2].strip(),
                            'company3': None
                        }
                    elif num_companies == 3 and len(cells) >= 4:
                        metrics_data[metric_name] = {
                            'company1': cells[1].strip(),
                            'company2': cells[2].strip(),
                            'company3': cells[3].strip()
                        }
                    elif num_companies >= 3 and len(cells) >= 4:
                        # Fallback for edge cases
                        logger.info(f" Processing 3-company row: {metric_name}")
                        metrics_data[metric_name] = {
                            'company1': cells[1].strip(),
                            'company2': cells[2].strip(),
                            'company3': cells[3].strip()
                        }
    
    if metrics_data:
        logger.info(f"✓ Successfully parsed {len(metrics_data)} metrics from table")
        # Debug: check first metric to see company3 data
        if metrics_data:
            first_metric = list(metrics_data.keys())[0]
            first_data = metrics_data[first_metric]
            logger.info(f"   Sample metric '{first_metric}': company3='{first_data.get('company3')}'")
    else:
        logger.info(" No metrics extracted from table")
    
    return metrics_data


def extract_numeric_value(value_str):
    """
    Extract numeric value from string like "$350.018 billion" or "32%" or "-52.69%".
    Returns None if value is not numeric or not specified.
    """
    if not value_str:
        return None
    
    value_str = value_str.lower().strip()
    
    # Check for non-numeric indicators
    if any(indicator in value_str for indicator in ['not specified', 'n/a', 'various', 'brief summary']):
        return None
    
    # Extract number - match patterns like: 350.018, 32%, $11.870, -52.69%, etc.
    # Pattern captures optional negative sign followed by digits with optional decimal
    match = re.search(r'(-?[\d,]+\.?\d*)', value_str)
    if not match:
        return None
    
    try:
        # Remove commas and convert to float (preserves negative sign)
        num = float(match.group(1).replace(',', ''))
        return num
    except (ValueError, AttributeError):
        return None


def prepare_chart_data(metrics_data, company1_name, company2_name, company3_name=None, max_metrics=8):
    """
    Prepare data for chart generation.
    Supports both 2 and 3 company comparisons.
    Only includes metrics that have valid numeric values for at least one company.
    """
    chart_data = {
        'metrics': [],
        'company1_values': [],
        'company2_values': [],
        'company3_values': [],
        'num_companies': 2 if company3_name is None else 3
    }
    
    count = 0
    for metric_name, values in metrics_data.items():
        if count >= max_metrics:
            break
        
        val1 = extract_numeric_value(values['company1'])
        val2 = extract_numeric_value(values['company2'])
        val3 = extract_numeric_value(values.get('company3')) if company3_name else None
        
        # Include metric if at least one value is valid
        if val1 is not None or val2 is not None or val3 is not None:
            chart_data['metrics'].append(metric_name)
            chart_data['company1_values'].append(val1 if val1 is not None else 0)
            chart_data['company2_values'].append(val2 if val2 is not None else 0)
            chart_data['company3_values'].append(val3 if val3 is not None else 0)
            count += 1
    
    return chart_data


def _save_and_upload_chart(fig, filename_prefix: str, width: int = 800, height: int = 500, label: str = "Chart") -> dict:
    """
    Save a plotly figure locally under generated_charts/ and, if Cloudinary
    is configured, upload it too. Shared by every chart-generation node
    (comparison chart, dynamic macro chart, yield curve chart) — this exact
    ~15-line save+upload sequence was previously duplicated 3 times.

    Returns {"chart_url": str|None, "chart_filename": str}.
    """
    import os
    import datetime

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{filename_prefix}_{timestamp}.png"
    output_dir = "generated_charts"
    os.makedirs(output_dir, exist_ok=True)
    local_path = os.path.join(output_dir, filename)

    try:
        fig.write_image(local_path, width=width, height=height)
        logger.info(f"✓ {label} saved locally: {local_path}")
    except Exception as e:
        logger.error(f"Failed to save {label.lower()} locally: {str(e)}")
        return {"chart_url": None, "chart_filename": filename}

    chart_url = None
    if os.getenv("CLOUDINARY_CLOUD_NAME"):
        try:
            from app.cloudinary import upload_to_cloudinary
            logger.info(f"Uploading {label.lower()} to Cloudinary...")
            result = upload_to_cloudinary(local_path)
            if result.get("success"):
                chart_url = result.get("url")
                logger.info(f"✓ {label} uploaded: {chart_url}")
            else:
                logger.error(f"Cloudinary upload failed: {result.get('error')}")
        except Exception as e:
            logger.error(f"Cloudinary upload skipped: {str(e)}")
    else:
        logger.info(f"Cloudinary not configured - {label.lower()} saved locally only")

    return {"chart_url": chart_url, "chart_filename": filename}


def generate_comparison_chart(state):
    """
    SYNCHRONOUS chart generation node for company comparison.
    Supports both 2 and 3 company comparisons.
    Parses tabular data, creates bar chart, and uploads to Cloudinary.
    
    Note: This is a SYNCHRONOUS function for LangGraph compatibility.
    All async operations are handled internally where needed.
    """
    logger.info("---GENERATING COMPARISON CHART---")
    
    try:
        import plotly.graph_objects as go
        import datetime
        
        # Get the generated answer
        answer = state.get("Intermediate_message", "")
        company1 = state.get("comparison_company1", "")
        company2 = state.get("comparison_company2", "")
        company3 = state.get("comparison_company3", None)
        
        # Debug logging
        logger.info(f" DEBUG: company1='{company1}', company2='{company2}', company3='{company3}'")
        logger.info(f" DEBUG: company3 type={type(company3)}, is None={company3 is None}, is empty={company3 == ''}")
        
        if not answer or not company1 or not company2:
            logger.warning(" Missing data for chart generation")
            return {"chart_url": None, "chart_filename": None}
        
        # Treat empty string as None for company3
        if company3 == "":
            company3 = None
        
        if company3:
            logger.info(f"Generating chart for {company1} vs {company2} vs {company3}")
        else:
            logger.info(f"Generating chart for {company1} vs {company2}")
        
        # Step 1: Parse table
        metrics_data = parse_markdown_table(answer)
        if not metrics_data:
            logger.info("No metrics found in answer")
            return {"chart_url": None, "chart_filename": None}
        
        logger.info(f"✓ Parsed {len(metrics_data)} metrics from table")
        
        # Step 2: Prepare chart data
        chart_data = prepare_chart_data(metrics_data, company1, company2, company3, max_metrics=8)
        if not chart_data['metrics']:
            logger.info("No valid numeric metrics for charting")
            return {"chart_url": None, "chart_filename": None}
        
        logger.info(f"✓ Prepared {len(chart_data['metrics'])} metrics for charting")
        
        # Step 3: Create grouped bar chart
        bars = [
            go.Bar(
                name=company1,
                x=chart_data['metrics'],
                y=chart_data['company1_values'],
                marker_color='#1f77b4',
                text=[f"{v:.2f}" for v in chart_data['company1_values']],
                textposition='auto',
            ),
            go.Bar(
                name=company2,
                x=chart_data['metrics'],
                y=chart_data['company2_values'],
                marker_color='#ff7f0e',
                text=[f"{v:.2f}" for v in chart_data['company2_values']],
                textposition='auto',
            )
        ]
        
        # Add third company if present
        if company3:
            bars.append(
                go.Bar(
                    name=company3,
                    x=chart_data['metrics'],
                    y=chart_data['company3_values'],
                    marker_color='#2ca02c',
                    text=[f"{v:.2f}" for v in chart_data['company3_values']],
                    textposition='auto',
                )
            )
        
        fig = go.Figure(data=bars)
        
        # Update layout - with support for negative values
        chart_year = str(state.get("year_start") or state.get("year_end") or datetime.now().year)
        title = f'Financial Comparison: {company1} vs {company2}'
        if company3:
            title += f" vs {company3}"
        title += f" ({chart_year})"
        
        fig.update_layout(
            title=title,
            xaxis_title='Financial Metrics',
            yaxis_title='Value',
            barmode='group',
            template='plotly_white',
            font=dict(size=12),
            height=600,
            width=1000 if not company3 else 1200,  # Wider for 3 companies
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1
            ),
            xaxis=dict(tickangle=-45),
            yaxis=dict(
                zeroline=True,
                zerolinewidth=2,
                zerolinecolor='gray',
                gridwidth=1,
                gridcolor='lightgray'
            ),
            hovermode='x unified'
        )
        
        logger.info("✓ Chart created successfully")

        filename_prefix = (
            f"comparison_{company1}_{company2}_{company3}" if company3
            else f"comparison_{company1}_{company2}"
        )
        return _save_and_upload_chart(
            fig, filename_prefix,
            width=1200 if company3 else 1000, height=600,
            label="Chart",
        )
    
    except ImportError as e:
        logger.error(f"Missing required package: {e}")
        logger.error("Install with: pip install plotly kaleido")
        return {"chart_url": None, "chart_filename": None}
    except Exception as e:
        logger.error(f"Chart generation error: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"chart_url": None, "chart_filename": None}


# ============================================================================
# ALPHA FRAMEWORK NODES - Stock Buy Timing Analysis
# ============================================================================

def _extract_ticker_from_free_text(question: str) -> Optional[str]:
    """
    Best-effort ticker extraction from a raw free-text question, for the fast
    keyword-detector nodes (ALPHA/scenario) that run BEFORE the full LLM-based
    company-extraction node, so `companies_detected` isn't populated yet.

    A naive "first all-caps word <=5 chars" scan (the old approach) matches
    ordinary sentence-initial words just as readily as a real ticker — e.g.
    "Give me a bull/bear/base case for MSFT" would match "GIVE" before ever
    reaching "MSFT", silently producing a report for the wrong company. This
    checks candidates against the real ticker/company-name table FIRST, and
    only falls back to the old weak heuristic as a last resort.
    """
    if not question:
        return None

    words = [w.strip(",.?!\"'") for w in question.split()]

    # 1. Any word that's already a KNOWN ticker (e.g. "MSFT", "AAPL").
    for word in words:
        if word.lower() in TICKER_TO_COMPANY:
            return word.upper()

    # 2. Any known company name appearing as a whole word/phrase in the
    #    question (e.g. "microsoft" in "...case for Microsoft stock").
    #    Word-boundary match, not a plain substring check — otherwise a short
    #    company name like "meta" would false-match inside "metadata".
    question_lower = question.lower()
    for ticker, company in TICKER_TO_COMPANY.items():
        if re.search(r'\b' + re.escape(company) + r'\b', question_lower):
            return ticker.upper()

    # 3. Last resort: the old weak heuristic (first short all-caps-able word).
    #    Kept only as a fallback for tickers not in our mapping table.
    for word in words:
        cleaned = word.upper()
        if 2 <= len(cleaned) <= 5 and cleaned.isalpha():
            return cleaned

    return None


def detect_alpha_query(state):
    """
    Detect if the query is asking about stock buy timing (ALPHA Framework trigger).
    
    Patterns detected:
    - "is it a good time to buy [company/ticker] stock?"
    - "should i buy [company/ticker]?"
    - "is now a good entry point for [ticker]?"
    """
    logger.info("="*80)
    logger.info(" ALPHA QUERY DETECTION")
    logger.info("="*80)

    # Explicit trigger: a dedicated caller (e.g. the /alpha endpoint) can seed
    # alpha_mode/ticker directly in the initial state, bypassing keyword
    # detection entirely. This is the only way ALPHA mode gets activated now.
    if state.get("alpha_mode") and state.get("ticker"):
        logger.info(" ALPHA MODE (explicit trigger)")
        logger.info(f"   Target: {state.get('ticker')}")
        logger.info("="*80 + "\n")
        return {
            "alpha_mode": True,
            "alpha_pillar": state.get("alpha_pillar"),
            "ticker": state.get("ticker"),
            "alpha_dimensions": {},
            "alpha_report": ""
        }

    # ------------------------------------------------------------------------
    # Keyword-based FULL ALPHA (5-dimension buy-timing) detection from
    # free-text chat queries — DISABLED. That framework only triggers
    # explicitly now (via the dedicated /alpha endpoint with an explicit
    # ticker), never inferred from general chat text, since phrases like
    # "should I buy" were firing the full framework unpredictably.
    #
    # The single-pillar INSIDER TRADING (Form 4) lookup is still allowed from
    # general chat, though — it's a targeted factual lookup (not the
    # multi-dimension buy/sell recommendation), so accidental triggers are
    # low-risk and it's genuinely useful as a plain chat question.
    # ------------------------------------------------------------------------
    messages = state["messages"]
    question = messages[-1].content.lower()

    # # Buy timing patterns (disabled — see comment above)
    # alpha_patterns = [
    #     "good time to buy",
    #     "should i buy",
    #     "should i invest in",
    #     "entry point",
    #     "right time to buy",
    #     "buy now",
    #     "time to invest",
    #     "good buy",
    #     "worth buying",
    #     "ialpha analysis of",
    #     "alpha analysis of",
    #     "a 360 degree analysis"
    # ]

    insider_patterns = [
        "insider trading",
        "open-market buying",
        "open market buying",
        "open-market selling",
        "open market selling",
        "promoters",
        "key management",
        "form 4",
        "form4"
    ]

    is_insider_query = any(pattern in question for pattern in insider_patterns)

    if is_insider_query:
        logger.info(" ALPHA MODE ACTIVATED (insider_trading pillar)")
        logger.info(f"   Query: {question}")

        # Try to extract ticker from state first
        ticker = state.get("ticker")
        company_filter = state.get("company_filter", [])

        if ticker:
            target_ticker = ticker
        elif company_filter and len(company_filter) > 0:
            target_ticker = company_filter[0]
        else:
            # Fallback: extract from the raw question text (checks known
            # tickers/company names before falling back to a weak guess —
            # see _extract_ticker_from_free_text for why the naive "first
            # short all-caps word" approach is unreliable on its own).
            target_ticker = _extract_ticker_from_free_text(question)

        if not target_ticker:
            logger.warning(" WARNING: Could not extract ticker/company")
            target_ticker = "unknown"

        logger.info(f"   Target: {target_ticker}")
        logger.info("="*80 + "\n")

        return {
            "alpha_mode": True,
            "alpha_pillar": "insider_trading",
            "ticker": target_ticker,
            "alpha_dimensions": {},
            "alpha_report": ""
        }

    logger.info(" Normal RAG query (not ALPHA)")
    logger.info("="*80 + "\n")
    return {
        "alpha_mode": False
    }


async def alpha_dimension_retrieve(state):
    """
    Retrieve dimension-specific data for ALPHA Framework.
    
    Fixed retrieval strategies per dimension:
    - Alignment: VectorDB only
    - Liquidity: VectorDB (60%) + Web (40%)
    - Performance: VectorDB only
    - Horizon: Web only
    - Action: Web only
    """
    logger.info("="*80)
    logger.info(" ALPHA DIMENSIONAL RETRIEVAL")
    logger.info("="*80)
    
    ticker = state.get("ticker", "").upper()
    company_filter = state.get("company_filter", [])
    
    if not ticker and company_filter:
        ticker = company_filter[0].upper()
    
    logger.info(f" Target: {ticker}\n")

    alpha_pillar = state.get("alpha_pillar")

    if alpha_pillar == "insider_trading":
        logger.info(" [SINGLE PILLAR] Insider Trading (Form 4)")
        try:
            from rag.utils.Insights_Form4.advisory_hub import get_advisory_report
            form4_report = await get_advisory_report(ticker)
            sections = []
            if form4_report and "status" not in form4_report and "error" not in form4_report:
                for issuer_name, detail in form4_report.items():
                    if issuer_name in ("error", "status", "message", "ticker"):
                        continue

                    cur_price = detail.get("Current_Price")
                    price_str = f"${cur_price:,.2f}" if isinstance(cur_price, float) else "N/A"

                    # ── Build the final response directly ──────────────────
                    acq_count  = int(detail.get("Acquired_Txn_Count", 0))
                    disp_count = int(detail.get("Disposed_Txn_Count", 0))
                    acq_shares = int(detail.get("Total_Acquired_Shares", 0))
                    disp_shares = int(detail.get("Total_Disposed_Shares", 0))

                    section = []
                    section.append(f"## Insider Trading Analysis — {issuer_name} ({ticker})")
                    section.append(f"*Source: SEC Form 4 filings | Current Market Price: {price_str}*\n")

                    section.append(
                        f"**Acquisitions:** {acq_count} transaction(s) — {acq_shares:,} shares   |   "
                        f"**Dispositions:** {disp_count} transaction(s) — {disp_shares:,} shares\n"
                    )

                    section.append("### Summary")
                    section.append(detail.get("Reason", "No analysis available"))
                    section.append("")

                    sections.append("\n".join(section))

            final_response = "\n\n---\n\n".join(sections) if sections else f"No Form 4 data found for {ticker}."
            logger.info("    Form4 data retrieved and formatted successfully.")
            return {
                "Intermediate_message": final_response,
                "alpha_dimensions": {"insider_trading": {}},
                "documents": [],
            }
        except Exception as e:
            logger.error(f"    Error: {e}")
            import traceback; traceback.print_exc()
            return {
                "Intermediate_message": f"Error retrieving insider trading data for {ticker}: {e}",
                "documents": [],
                "alpha_dimensions": {},
            }
    
    from app.services.vectordb_manager import get_vectordb_manager
    from langchain_tavily import TavilySearch
    
    vectordb_mgr = get_vectordb_manager()
    from app.utils.company_mapping import get_most_recent_filed_fiscal_year
    _cur_yr = get_most_recent_filed_fiscal_year(ticker)
    # All web searches restricted to trusted financial domains, capped to the last 1 year
    web_search = TavilySearch(max_results=3, include_domains=TRUSTED_FINANCIAL_DOMAINS, time_range="year")
    # Trends / notable trends (Horizon) fetched exclusively from SeekingAlpha, capped to the last 1 year
    web_search_seekingalpha = TavilySearch(max_results=3, include_domains=["seekingalpha.com"], time_range="year")
    # Liquidity: latest macro/rate data straight from FDIC and other government sources
    web_search_govt = TavilySearch(max_results=3, include_domains=GOVT_SOURCE_DOMAINS, time_range="year")

    alpha_dimensions = {}

    # -------------------------------------------------------------------------
    # ALIGNMENT: VectorDB (MD&A, Governance) + Form4 Insider Trading
    # -------------------------------------------------------------------------
    logger.info(" [1/5] Alignment (Stakeholder Interests) - VectorDB + Form4 Insider Data")
    try:
        db_instance = vectordb_mgr.get_instance(ticker, create_if_missing=False)

        # Query for MD&A and governance documents — latest filing only
        alignment_queries = [
            f"{ticker} management discussion analysis MD&A latest fiscal year {_cur_yr}",
            f"{ticker} governance board independence proxy statement latest {_cur_yr}",
            f"{ticker} related party transactions latest {_cur_yr}"
        ]

        alignment_docs = []
        for query in alignment_queries:
            results = await db_instance.hybrid_search(query=query, content_type="text", limit=3)
            for point in results:
                if hasattr(point, 'payload'):
                    from langchain_core.documents import Document
                    doc = Document(
                        page_content=point.payload.get('page_content', ''),
                        metadata=point.payload.get('metadata', {})
                    )
                    alignment_docs.append(doc)

        # ── Form 4 insider trading advisory data ──────────────────────────────
        logger.info("    Fetching Form 4 insider trading data…")
        try:
            from rag.utils.Insights_Form4.advisory_hub import get_advisory_report

            form4_report = await get_advisory_report(ticker)

            if form4_report and "status" not in form4_report and "error" not in form4_report:
                lines = [f"INSIDER TRADING ANALYSIS (SEC Form 4) — {ticker}\n"]
                for issuer_name, detail in form4_report.items():
                    if issuer_name in ("error", "status", "message", "ticker"):
                        continue
                    lines.append(f"\nAnalyst Insight:\n{detail.get('Reason', 'No analysis available')}\n")

                from langchain_core.documents import Document
                insider_doc = Document(
                    page_content="\n".join(lines),
                    metadata={"source": "form4_insider_trading", "company": ticker, "content_type": "insider_trading"}
                )
                # Insert first so it's never cut off by the docs[:5] slice in format_docs
                alignment_docs.insert(0, insider_doc)
                logger.info(f"    Form4 insider doc added ({len(form4_report)} issuer(s))")
            else:
                logger.warning(f"    No Form4 data in DB for {ticker} — skipping insider doc")
        except Exception as form4_err:
            logger.error(f"    Form4 fetch error (non-fatal): {form4_err}")
        # ─────────────────────────────────────────────────────────────────────

        alpha_dimensions['alignment'] = {
            'source': 'vectordb+form4',
            'documents': alignment_docs[:5],  # Form4 doc at [0], then vectordb docs
            'query_count': len(alignment_queries)
        }
        logger.info(f"    Total alignment docs: {len(alignment_docs[:5])}")

    except Exception as e:
        logger.error(f"    Error: {e}")
        alpha_dimensions['alignment'] = {'source': 'vectordb+form4', 'documents': [], 'query_count': 0}
    
    # -------------------------------------------------------------------------
    # LIQUIDITY: VectorDB (risk factors) + Web (latest FDIC / govt macro data)
    # -------------------------------------------------------------------------
    logger.info(" [2/5] Liquidity (Macro/Micro Environment) - VectorDB + Govt Sources")
    try:
        # VectorDB: Risk factors, commodity exposure
        liquidity_docs = []
        vdb_queries = [
            f"{ticker} risk factors competitive pressures",
            f"{ticker} commodity input cost exposure raw materials"
        ]

        for query in vdb_queries:
            results = await db_instance.hybrid_search(query=query, content_type="text", limit=2)
            for point in results:
                if hasattr(point, 'payload'):
                    from langchain_core.documents import Document
                    doc = Document(
                        page_content=point.payload.get('page_content', ''),
                        metadata=point.payload.get('metadata', {})
                    )
                    liquidity_docs.append(doc)

        # Web: Latest macro/liquidity conditions straight from FDIC and other govt sources
        web_queries = [
            f"latest FDIC bank liquidity deposit risk data affecting {ticker} sector",
            f"latest Federal Reserve interest rate policy debt structure impact on {ticker}"
        ]

        for query in web_queries:
            web_results = web_search_govt.invoke({"query": query})
            # Parse Tavily response using helper
            sources = _parse_tavily_response(web_results, query)
            for source in sources:
                from langchain_core.documents import Document
                doc = Document(
                    page_content=source['content'],
                    metadata={'source': 'govt_source', 'url': source['url'], 'title': source['title']}
                )
                liquidity_docs.append(doc)

        alpha_dimensions['liquidity'] = {
            'source': 'vectordb+govt',
            'documents': liquidity_docs,
            'query_count': len(vdb_queries) + len(web_queries)
        }
        logger.info(f"    Retrieved {len(liquidity_docs)} chunks (vectordb + govt sources)")

    except Exception as e:
        logger.error(f"    Error: {e}")
        alpha_dimensions['liquidity'] = {'source': 'vectordb+govt', 'documents': [], 'query_count': 0}
    
    # -------------------------------------------------------------------------
    # PERFORMANCE: VectorDB only (current year 10-K vs prior 2 years for comparison)
    # -------------------------------------------------------------------------
    logger.info(" [3/5] Performance (Earnings & Fundamentals) - VectorDB")
    try:
        _prior_yr, _prior_yr2 = _cur_yr - 1, _cur_yr - 2
        performance_queries = [
            f"{ticker} revenue net income annual fiscal year {_cur_yr} {_prior_yr} {_prior_yr2} financial results comparison",
            f"{ticker} operating cash flow free cash flow income statement annual {_cur_yr} {_prior_yr} {_prior_yr2}",
            f"{ticker} EBITDA margins ROE profitability metrics annual fiscal year {_cur_yr} {_prior_yr} {_prior_yr2}"
        ]

        performance_docs = []
        for query in performance_queries:
            results = await db_instance.hybrid_search(query=query, content_type="text", limit=4)
            for point in results:
                if hasattr(point, 'payload'):
                    from langchain_core.documents import Document
                    doc = Document(
                        page_content=point.payload.get('page_content', ''),
                        metadata=point.payload.get('metadata', {})
                    )
                    performance_docs.append(doc)

        alpha_dimensions['performance'] = {
            'source': 'vectordb',
            'documents': performance_docs[:9],
            'query_count': len(performance_queries)
        }
        logger.info(f"    Retrieved {len(performance_docs[:9])} chunks ({_cur_yr}/{_prior_yr}/{_prior_yr2} comparison)")

    except Exception as e:
        logger.error(f"    Error: {e}")
        alpha_dimensions['performance'] = {'source': 'vectordb', 'documents': [], 'query_count': 0}
    
    # -------------------------------------------------------------------------
    # HORIZON: SeekingAlpha only (trends, competitive positioning, moat)
    # -------------------------------------------------------------------------
    logger.info(" [4/5] Horizon (Structural Opportunity & Moat) - SeekingAlpha")
    try:
        horizon_queries = [
            f"{ticker} operating margins vs industry average pricing power",
            f"{ticker} R&D expenditure vs peers innovation",
            f"{ticker} market share trends competitive positioning",
            f"{ticker} competitive moat network effects switching costs"
        ]

        horizon_docs = []
        for query in horizon_queries:
            # All trends and notable trends fetched exclusively from SeekingAlpha
            web_results = web_search_seekingalpha.invoke({"query": query})
            sources = _parse_tavily_response(web_results, query)
            for source in sources:
                from langchain_core.documents import Document
                doc = Document(
                    page_content=source['content'],
                    metadata={'source': 'seekingalpha', 'url': source['url'], 'title': source['title']}
                )
                horizon_docs.append(doc)

        alpha_dimensions['horizon'] = {
            'source': 'seekingalpha',
            'documents': horizon_docs,
            'query_count': len(horizon_queries)
        }
        logger.info(f"    Retrieved {len(horizon_docs)} chunks (SeekingAlpha)")

    except Exception as e:
        logger.error(f"    Error: {e}")
        alpha_dimensions['horizon'] = {'source': 'seekingalpha', 'documents': [], 'query_count': 0}
    
    # -------------------------------------------------------------------------
    # ACTION: Web (RSI, SMA200, price, P/E, EBITDA) — all from trusted sources
    # -------------------------------------------------------------------------
    logger.info(" [5/5] Action (RSI / SMA200 / Price / P/E / EBITDA) - Web")
    try:
        action_docs = []

        # Domains that reliably display live technical indicators, capped to the last 1 year
        web_search_technical = TavilySearch(
            max_results=3,
            include_domains=TRUSTED_FINANCIAL_DOMAINS,
            time_range="year"
        )

        web_search_technical_stock_price = TavilySearch(
            max_results=5,
            include_domains=TRUSTED_FINANCIAL_DOMAINS,
            time_range="year"
        )

        # -- RSI(14) and current price from web --------------------------------
        logger.info("    Fetching RSI(14) and current price from web...")
        try:
            rsi_query = f"{ticker} RSI 14 relative strength index current technical indicators"
            rsi_results = web_search_technical.invoke({"query": rsi_query})
            rsi_sources = _parse_tavily_response(rsi_results, rsi_query)
            for source in rsi_sources:
                from langchain_core.documents import Document
                doc = Document(
                    page_content=source['content'],
                    metadata={
                        'source': 'web_search',
                        'url': source['url'],
                        'title': source['title'],
                        'data_type': 'technical'
                    }
                )
                action_docs.append(doc)
            logger.info(f"    Retrieved {len(rsi_sources)} RSI/price docs from web")
        except Exception as rsi_err:
            logger.error(f"    RSI web search error (non-fatal): {rsi_err}")

        # -- SMA200 from web ---------------------------------------------------
        logger.info("    Fetching SMA200 from web...")
        try:
            sma_query = f"{ticker} 200 day moving average SMA200 current stock price technical"
            sma_results = web_search_technical.invoke({"query": sma_query})
            sma_sources = _parse_tavily_response(sma_results, sma_query)
            for source in sma_sources:
                from langchain_core.documents import Document
                doc = Document(
                    page_content=source['content'],
                    metadata={
                        'source': 'web_search',
                        'url': source['url'],
                        'title': source['title'],
                        'data_type': 'technical'
                    }
                )
                action_docs.append(doc)
            logger.info(f"    Retrieved {len(sma_sources)} SMA200 docs from web")
        except Exception as sma_err:
            logger.error(f"    SMA200 web search error (non-fatal): {sma_err}")
        
        # -- Current Stock Price from web ---------------------------------------------------
        logger.info("    Fetching Current Stock Price from web...")
        try:
            sma_query = f"{ticker} today's stock price current stock price"
            sma_results = web_search_technical_stock_price.invoke({"query": sma_query})
            sma_sources = _parse_tavily_response(sma_results, sma_query)
            for source in sma_sources:
                from langchain_core.documents import Document
                doc = Document(
                    page_content=source['content'],
                    metadata={
                        'source': 'web_search',
                        'url': source['url'],
                        'title': source['title'],
                        'data_type': 'technical'
                    }
                )
                action_docs.append(doc)
            logger.info(f"    Retrieved {len(sma_sources)} Current Stock Price docs from web")
        except Exception as sma_err:
            logger.error(f"    Current Stock Price web search error (non-fatal): {sma_err}")

        # -- EBITDA from web search (trusted financial domains) ---------------
        logger.info("    Fetching EBITDA from web (trusted domains)...")
        try:
            ebitda_query = f"{ticker} EBITDA annual earnings current"
            ebitda_results = web_search.invoke({"query": ebitda_query})
            ebitda_sources = _parse_tavily_response(ebitda_results, ebitda_query)
            for source in ebitda_sources:
                from langchain_core.documents import Document
                doc = Document(
                    page_content=source['content'],
                    metadata={
                        'source': 'web_search',
                        'url': source['url'],
                        'title': source['title'],
                        'data_type': 'ebitda'
                    }
                )
                action_docs.append(doc)
            logger.info(f"    Retrieved {len(ebitda_sources)} EBITDA docs from web")
        except Exception as ebitda_err:
            logger.error(f"    EBITDA web search error (non-fatal): {ebitda_err}")

        # -- P/E ratio from web search (trusted financial domains) ------------
        logger.info("    Fetching P/E ratio from web (trusted domains)...")
        try:
            pe_query = f"{ticker} P/E ratio price to earnings current valuation"
            pe_results = web_search.invoke({"query": pe_query})
            pe_sources = _parse_tavily_response(pe_results, pe_query)
            for source in pe_sources:
                from langchain_core.documents import Document
                doc = Document(
                    page_content=source['content'],
                    metadata={
                        'source': 'web_search',
                        'url': source['url'],
                        'title': source['title'],
                        'data_type': 'pe_ratio'
                    }
                )
                action_docs.append(doc)
            logger.info(f"    Retrieved {len(pe_sources)} P/E docs from web")
        except Exception as pe_err:
            logger.error(f"    P/E web search error (non-fatal): {pe_err}")

        alpha_dimensions['action'] = {
            'source': 'web',
            'documents': action_docs,
            'query_count': 5
        }
        logger.info(f"    Total action docs: {len(action_docs)}")

    except Exception as e:
        logger.error(f"    Error: {e}")
        alpha_dimensions['action'] = {
            'source': 'web',
            'documents': [],
            'query_count': 0
        }
    
    logger.info("\n" + "="*80)
    logger.info(f" RETRIEVAL COMPLETE: {sum(len(d.get('documents', [])) for d in alpha_dimensions.values())} total chunks")
    logger.info("="*80 + "\n")
    
    return {
        "alpha_dimensions": alpha_dimensions
    }


def alpha_generate_report(state):
    """
    Generate ALPHA Framework report from dimensional analysis.
    Creates <100 word summaries for each dimension and combines into final report.
    """
    logger.info("="*80)
    logger.info(" ALPHA REPORT GENERATION")
    logger.info("="*80 + "\n")
    
    ticker = state.get("ticker", "UNKNOWN")
    alpha_dimensions = state.get("alpha_dimensions", {})
    
    from langchain_openai import ChatOpenAI
    from rag.prompts.prompts import (
        get_alpha_alignment_chain,
        get_alpha_liquidity_chain,
        get_alpha_performance_chain,
        get_alpha_horizon_chain,
        get_alpha_action_chain,
        get_alpha_report_combiner_chain
    )
    
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    
    # Helper to format documents
    def format_docs(docs):
        if not docs:
            return "No documents available."
        parts = []
        for d in docs[:5]:  # Limit to avoid token overload
            is_form4 = d.metadata.get('content_type') == 'insider_trading'
            source_label = (
                "SEC Form 4 — Insider Trading Analysis"
                if is_form4
                else d.metadata.get('source_file', d.metadata.get('title', d.metadata.get('source', 'Unknown')))
            )
            char_limit = 1500 if is_form4 else 500
            parts.append(f"Source: {source_label}\n{d.page_content[:char_limit]}")
        return "\n\n---\n\n".join(parts)
    
    dimension_outputs = {}
    
    # Generate each dimension
    dimensions = [
        ('alignment', get_alpha_alignment_chain, "Alignment"),
        ('liquidity', get_alpha_liquidity_chain, "Liquidity"),
        ('performance', get_alpha_performance_chain, "Performance"),
        ('horizon', get_alpha_horizon_chain, "Horizon"),
        ('action', get_alpha_action_chain, "Action")
    ]
    
    for dim_key, chain_func, dim_name in dimensions:
        logger.info(f" Generating {dim_name}...")

        dim_data = alpha_dimensions.get(dim_key, {})
        docs = dim_data.get('documents', [])

        if dim_key == 'action':
            technical_docs = [d for d in docs if d.metadata.get('data_type') == 'technical']
            pe_docs        = [d for d in docs if d.metadata.get('data_type') == 'pe_ratio']
            ebitda_docs    = [d for d in docs if d.metadata.get('data_type') == 'ebitda']
            logger.info(f"    ACTION docs → {len(technical_docs)} technical, {len(pe_docs)} P/E, {len(ebitda_docs)} EBITDA")

            action_document = (
                f"=== TECHNICAL INDICATORS (web-sourced: RSI-14, SMA200, current price) ===\n"
                f"{format_docs(technical_docs)}\n\n"
                f"=== P/E RATIO (web-sourced) ===\n"
                f"{format_docs(pe_docs)}\n\n"
                f"=== EBITDA (web-sourced) ===\n"
                f"{format_docs(ebitda_docs)}"
            )
            invoke_kwargs = {
                "company": ticker,
                "ticker": ticker,
                "documents": action_document
            }
        else:
            # For alignment: pass Form4 content as a dedicated variable so the
            # LLM receives full data, while governance/MD&A docs go in {documents}.
            invoke_kwargs = {
                "company": ticker,
                "ticker": ticker,
                "documents": format_docs(
                    [d for d in docs if d.metadata.get('content_type') != 'insider_trading']
                )
            }
            if dim_key == 'alignment':
                form4_doc = next(
                    (d for d in docs if d.metadata.get('content_type') == 'insider_trading'),
                    None
                )
                invoke_kwargs['form4_analysis'] = (
                    form4_doc.page_content if form4_doc
                    else "No SEC Form 4 insider trading data available for this ticker."
                )

        try:
            chain = chain_func(llm, ticker=ticker) if dim_key == 'performance' else chain_func(llm)
            result = chain.invoke(invoke_kwargs)

            analysis = result.analysis

            dimension_outputs[dim_key] = {
                'analysis': analysis,
                'key_points': result.key_points,
                'recommendation': getattr(result, 'recommendation', '')
            }
            logger.info(f"    ✓ {dim_name}: {len(analysis)} chars, {len(result.key_points)} points")

        except Exception as e:
            logger.error(f"    ✗ Error: {e}")
            dimension_outputs[dim_key] = {
                'analysis': f"Analysis unavailable due to insufficient data.",
                'key_points': [],
                'recommendation': ''
            }
    
    # Combine into final report
    logger.info("\n Combining dimensions into final report...")

    try:
        combiner_chain = get_alpha_report_combiner_chain(llm)
        final_report = combiner_chain.invoke({
            "company": ticker,
            "ticker": ticker,
            "alignment": dimension_outputs.get('alignment', {}).get('analysis', 'N/A'),
            "liquidity": dimension_outputs.get('liquidity', {}).get('analysis', 'N/A'),
            "performance": dimension_outputs.get('performance', {}).get('analysis', 'N/A'),
            "horizon": dimension_outputs.get('horizon', {}).get('analysis', 'N/A'),
            "action": dimension_outputs.get('action', {}).get('analysis', 'N/A')
        })
        logger.info(f"    ✓ Final report: {len(final_report)} chars")

    except Exception as e:
        logger.error(f"    ✗ Error: {e}")
        final_report = f"# ALPHA Framework Analysis: {ticker}\n\nError generating report: {str(e)}"
    
    logger.info("\n" + "="*80)
    logger.info(" ALPHA REPORT COMPLETE")
    logger.info("="*80 + "\n")
    
    # Return final report as AIMessage
    from langchain_core.messages import AIMessage

    return {
        "messages": [AIMessage(content=final_report)],
        "alpha_report": final_report,
        "Intermediate_message": final_report  # For compatibility with show_result node
    }


# ============================================================================
# SCENARIO FRAMEWORK – Bull / Bear / Base Case (web-search only)
# ============================================================================

# Domains specifically useful for analyst ratings & brokerage research aggregators
SCENARIO_SEARCH_DOMAINS = [
    # Analyst-rating aggregators
    "tipranks.com",
    "marketbeat.com",
    "benzinga.com",
    "barrons.com",
    "thestreet.com",
    "zacks.com",
    "finviz.com",
    # Credit-rating agencies (public pages)
    "spglobal.com",
    "moodys.com",
    "fitchratings.com",
    "dbrs.com",
    # Already-trusted general financial domains
    "seekingalpha.com",
    "finance.yahoo.com",
    "bloomberg.com",
    "reuters.com",
    "wsj.com",
    "ft.com",
    "marketwatch.com",
    "morningstar.com",
    "stockanalysis.com",
    "fool.com",
    "cnbc.com",
    "investopedia.com",
    "gurufocus.com",
    "macrotrends.net",
]

# Scenario detection keyword patterns
SCENARIO_PATTERNS = [
    "bull case",
    "bear case",
    "base case",
    "bull scenario",
    "bear scenario",
    "base scenario",
    "upside case",
    "downside case",
    "bull and bear",
    "bull/bear",
    "bull bear base",
    "scenarios for",
    "investment scenario",
    "price target scenario",
    "upside downside",
    "scenario analysis",
    "three scenarios",
    "3 scenarios",
]


def detect_scenario_query(state):
    """
    Detect if the query is asking for Bull / Bear / Base scenario analysis.

    This node is chained AFTER detect_alpha_query so that alpha queries are
    handled first; scenario detection only fires when alpha_mode is False.

    Returns:
        scenario_mode: True   → graph routes to scenario_retrieve
        scenario_mode: False  → graph routes normally
    """
    logger.info("=" * 80)
    logger.info(" SCENARIO QUERY DETECTION")
    logger.info("=" * 80)

    # If alpha mode already active, skip scenario detection
    if state.get("alpha_mode", False):
        logger.warning(" Alpha mode active – skipping scenario detection")
        logger.info("=" * 80 + "\n")
        return {"scenario_mode": False}

    messages = state["messages"]
    question = messages[-1].content.lower()

    is_scenario_query = any(pattern in question for pattern in SCENARIO_PATTERNS)

    if is_scenario_query:
        logger.info(" SCENARIO MODE ACTIVATED")
        logger.info(f"   Query: {question}")

        # Resolve ticker from state (set by portfolio/session context)
        ticker = state.get("ticker")
        company_filter = state.get("company_filter", [])

        if not ticker and company_filter:
            ticker = company_filter[0].upper()

        if not ticker:
            # Fallback: extract from the raw question text (checks known
            # tickers/company names before falling back to a weak guess —
            # see _extract_ticker_from_free_text for why the naive "first
            # short all-caps word" approach is unreliable on its own).
            ticker = _extract_ticker_from_free_text(question)

        if not ticker:
            ticker = "UNKNOWN"

        logger.info(f"   Target ticker: {ticker}")
        logger.info("=" * 80 + "\n")
        return {
            "scenario_mode": True,
            "ticker": ticker,
            "scenario_data": {},
            "scenario_report": ""
    }
    else:
        logger.info(" Normal query (not a Scenario request)")
        logger.info("=" * 80 + "\n")
        return {"scenario_mode": False}


def scenario_data_retrieve(state):
    """
    Retrieve data for Bull / Bear / Base scenario analysis using Tavily web search.

    Data buckets collected:
      1. analyst_data   – ratings, price targets, brokerage views
      2. valuation_data – P/E, EV/EBITDA, DCF, historical valuation
      3. catalyst_data  – growth drivers, new products, market expansion
      4. risk_data      – downside risks, competition, regulatory
      5. credit_data    – S&P, Moody's, Fitch, DBRS rating commentary
      6. macro_data     – sector trends, interest rates, macro environment
    """
    logger.info("=" * 80)
    logger.info(" SCENARIO DATA RETRIEVAL (Web-Search Only)")
    logger.info("=" * 80)

    ticker = state.get("ticker", "UNKNOWN").upper()
    logger.info(f" Target: {ticker}\n")

    web_search_tool = TavilySearch(
        max_results=4,
        include_raw_content=True,
        include_domains=SCENARIO_SEARCH_DOMAINS,
    )

    scenario_data = {
        "analyst_data": [],
        "valuation_data": [],
        "catalyst_data": [],
        "risk_data": [],
        "credit_data": [],
        "macro_data": []
    }

    # -------------------------------------------------------------------------
    # 1. Analyst Ratings & Brokerage Price Targets
    # -------------------------------------------------------------------------
    logger.info(" [1/6] Analyst ratings & brokerage price targets")
    _s_yr = datetime.now().year
    analyst_queries = [
        f"{ticker} analyst rating consensus buy sell hold price target {_s_yr}",
        f"{ticker} Goldman Sachs Morgan Stanley JPMorgan BofA Citi analyst recommendation",
        f"{ticker} Wells Fargo Barclays UBS Bernstein Wolfe Evercore analyst price target",
        f"{ticker} analyst upgrade downgrade rating change latest",
    ]
    for q in analyst_queries:
        try:
            results = web_search_tool.invoke({"query": q})
            sources = _parse_tavily_response(results, q)
            for s in sources:
                scenario_data["analyst_data"].append({
                    "title": s["title"],
                    "url": s["url"],
                    "content": s["content"][:1500]
    })
        except Exception as e:
            logger.error(f"    Warning: {e}")
    logger.info(f"    {len(scenario_data['analyst_data'])} analyst sources collected")

    # -------------------------------------------------------------------------
    # 2. Valuation Metrics
    # -------------------------------------------------------------------------
    logger.info(" [2/6] Valuation metrics")
    valuation_queries = [
        f"{ticker} P/E ratio EV/EBITDA price to sales valuation {_s_yr}",
        f"{ticker} fair value DCF intrinsic value analyst estimate",
    ]
    for q in valuation_queries:
        try:
            results = web_search_tool.invoke({"query": q})
            sources = _parse_tavily_response(results, q)
            for s in sources:
                scenario_data["valuation_data"].append({
                    "title": s["title"],
                    "url": s["url"],
                    "content": s["content"][:1500]
    })
        except Exception as e:
            logger.error(f"    Warning: {e}")
    logger.info(f"    {len(scenario_data['valuation_data'])} valuation sources collected")

    # -------------------------------------------------------------------------
    # 3. Growth Catalysts (Bull drivers)
    # -------------------------------------------------------------------------
    logger.info(" [3/6] Growth catalysts & bull drivers")
    catalyst_queries = [
        f"{ticker} growth drivers catalysts bullish case upside {_s_yr} {_s_yr + 1}",
        f"{ticker} new product launch market expansion revenue growth opportunity",
        f"{ticker} competitive advantage pricing power margin expansion",
    ]
    for q in catalyst_queries:
        try:
            results = web_search_tool.invoke({"query": q})
            sources = _parse_tavily_response(results, q)
            for s in sources:
                scenario_data["catalyst_data"].append({
                    "title": s["title"],
                    "url": s["url"],
                    "content": s["content"][:1500]
    })
        except Exception as e:
            logger.error(f"    Warning: {e}")
    logger.info(f"    {len(scenario_data['catalyst_data'])} catalyst sources collected")

    # -------------------------------------------------------------------------
    # 4. Downside Risks (Bear drivers)
    # -------------------------------------------------------------------------
    logger.info(" [4/6] Downside risks & bear headwinds")
    risk_queries = [
        f"{ticker} risks headwinds bearish case downside {_s_yr}",
        f"{ticker} competition market share loss regulatory risk",
        f"{ticker} margin compression debt leverage concern analyst warning",
    ]
    for q in risk_queries:
        try:
            results = web_search_tool.invoke({"query": q})
            sources = _parse_tavily_response(results, q)
            for s in sources:
                scenario_data["risk_data"].append({
                    "title": s["title"],
                    "url": s["url"],
                    "content": s["content"][:1500]
    })
        except Exception as e:
            logger.error(f"    Warning: {e}")
    logger.info(f"    {len(scenario_data['risk_data'])} risk sources collected")

    # -------------------------------------------------------------------------
    # 5. Credit Ratings
    # -------------------------------------------------------------------------
    logger.info(" [5/6] Credit rating agency reports")
    credit_queries = [
        f"{ticker} credit rating S&P Moody's Fitch rating outlook {_s_yr}",
        f"{ticker} bond rating investment grade speculative debt outlook",
    ]
    for q in credit_queries:
        try:
            results = web_search_tool.invoke({"query": q})
            sources = _parse_tavily_response(results, q)
            for s in sources:
                scenario_data["credit_data"].append({
                    "title": s["title"],
                    "url": s["url"],
                    "content": s["content"][:1500]
    })
        except Exception as e:
            logger.error(f"    Warning: {e}")
    logger.info(f"    {len(scenario_data['credit_data'])} credit sources collected")

    # -------------------------------------------------------------------------
    # 6. Macro & Sector Environment
    # -------------------------------------------------------------------------
    logger.info(" [6/6] Macro & sector environment")
    macro_queries = [
        f"{ticker} sector macro outlook interest rate impact {_s_yr}",
        f"{ticker} industry trends tailwinds headwinds economic environment",
    ]
    for q in macro_queries:
        try:
            results = web_search_tool.invoke({"query": q})
            sources = _parse_tavily_response(results, q)
            for s in sources:
                scenario_data["macro_data"].append({
                    "title": s["title"],
                    "url": s["url"],
                    "content": s["content"][:1500]
    })
        except Exception as e:
            logger.error(f"    Warning: {e}")
    logger.info(f"    {len(scenario_data['macro_data'])} macro sources collected")

    total = sum(len(v) for v in scenario_data.values())
    logger.info(f"\n Retrieval complete: {total} total sources across 6 buckets")
    logger.info("=" * 80 + "\n")

    return {"scenario_data": scenario_data}


def scenario_generate_report(state):
    """
    Generate the final Bull / Bear / Base scenario report from collected web data.

    Steps:
      1. Format each data bucket into readable text
      2. Run Bull / Bear / Base case chains in sequence
      3. Run the combiner chain to produce the final markdown report
      4. Return as AIMessage for show_result compatibility
    """
    logger.info("=" * 80)
    logger.info(" SCENARIO REPORT GENERATION")
    logger.info("=" * 80 + "\n")

    ticker = state.get("ticker", "UNKNOWN").upper()
    scenario_data = state.get("scenario_data", {})

    from langchain_openai import ChatOpenAI
    from rag.prompts.prompts import (
        get_scenario_bull_chain,
        get_scenario_bear_chain,
        get_scenario_base_chain,
        get_scenario_report_combiner_chain,
    )

    llm = ChatOpenAI(model="gpt-4o", temperature=0)

    def _format_bucket(bucket_key, max_items=6, max_chars=1200):
        """Format a data bucket into a single readable string."""
        items = scenario_data.get(bucket_key, [])
        if not items:
            return "No data available from web search."
        parts = []
        for item in items[:max_items]:
            title = item.get("title", "Source")
            url = item.get("url", "")
            content = item.get("content", "")[:max_chars]
            parts.append(f"**{title}**\n{url}\n{content}")
        return "\n\n---\n\n".join(parts)

    analyst_text = _format_bucket("analyst_data")
    valuation_text = _format_bucket("valuation_data")
    catalyst_text = _format_bucket("catalyst_data")
    risk_text = _format_bucket("risk_data")
    credit_text = _format_bucket("credit_data")
    macro_text = _format_bucket("macro_data")

    # ── Bull Case ─────────────────────────────────────────────────────────────
    logger.info(" Generating Bull Case...")
    bull_result = None
    try:
        bull_chain = get_scenario_bull_chain(llm)
        bull_result = bull_chain.invoke({
            "ticker": ticker,
            "analyst_data": analyst_text,
            "valuation_data": valuation_text,
            "catalyst_data": catalyst_text
    })
        logger.info(f"    Bull target: {bull_result.price_target}  upside: {bull_result.upside_downside}")
    except Exception as e:
        logger.error(f"    Error: {e}")

    # ── Bear Case ─────────────────────────────────────────────────────────────
    logger.info(" Generating Bear Case...")
    bear_result = None
    try:
        bear_chain = get_scenario_bear_chain(llm)
        bear_result = bear_chain.invoke({
            "ticker": ticker,
            "analyst_data": analyst_text,
            "risk_data": risk_text,
            "credit_data": credit_text
    })
        logger.info(f"    Bear target: {bear_result.price_target}  downside: {bear_result.upside_downside}")
    except Exception as e:
        logger.error(f"    Error: {e}")

    # ── Base Case ─────────────────────────────────────────────────────────────
    logger.info(" Generating Base Case...")
    base_result = None
    try:
        base_chain = get_scenario_base_chain(llm)
        base_result = base_chain.invoke({
            "ticker": ticker,
            "analyst_data": analyst_text,
            "valuation_data": valuation_text,
            "macro_data": macro_text
    })
        logger.info(f"    Base target: {base_result.price_target}  return: {base_result.upside_downside}")
    except Exception as e:
        logger.error(f"    Error: {e}")

    def _fmt_list(lst):
        if not lst:
            return "N/A"
        return "\n".join(f"• {item}" for item in lst)

    # Fallback defaults if any case failed
    def _safe(result, field, default="N/A"):
        if result is None:
            return default
        return getattr(result, field, default) or default

    # ── Cross-case sanity checks ──────────────────────────────────────────────
    # Bull/Bear/Base run as three INDEPENDENT LLM calls with no visibility into
    # each other's output, so nothing enforces bull >= base >= bear price
    # targets or that the three probabilities sum to ~100% — both have been
    # observed to drift (e.g. probabilities summing to 105%). Rather than
    # silently trusting three uncoordinated numbers, check them here: rescale
    # probabilities proportionally when they don't sum close to 100%, and flag
    # (don't hide) an out-of-order price target set for the reader to verify.
    def _parse_numeric(value):
        if not value:
            return None
        cleaned = re.sub(r'[^0-9.]', '', str(value))
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None

    scenario_warnings = []

    bull_target_val = _parse_numeric(_safe(bull_result, "price_target"))
    base_target_val = _parse_numeric(_safe(base_result, "price_target"))
    bear_target_val = _parse_numeric(_safe(bear_result, "price_target"))

    if None not in (bull_target_val, base_target_val, bear_target_val):
        if not (bull_target_val >= base_target_val >= bear_target_val):
            logger.warning(
                "Scenario price targets out of expected order: bull=%s base=%s bear=%s",
                bull_target_val, base_target_val, bear_target_val,
            )
            scenario_warnings.append(
                f"**Note**: the generated price targets did not follow the expected "
                f"bull ≥ base ≥ bear ordering (bull ${bull_target_val:g}, base ${base_target_val:g}, "
                f"bear ${bear_target_val:g}) — cross-check against the underlying sources before relying on it."
            )

    bull_prob_str = _safe(bull_result, "probability")
    base_prob_str = _safe(base_result, "probability")
    bear_prob_str = _safe(bear_result, "probability")

    bull_prob_val = _parse_numeric(bull_prob_str)
    base_prob_val = _parse_numeric(base_prob_str)
    bear_prob_val = _parse_numeric(bear_prob_str)

    if None not in (bull_prob_val, base_prob_val, bear_prob_val):
        total_prob = bull_prob_val + base_prob_val + bear_prob_val
        if total_prob > 0 and abs(total_prob - 100) > 1:
            scale = 100 / total_prob
            bull_prob_str = f"{bull_prob_val * scale:.0f}%"
            base_prob_str = f"{base_prob_val * scale:.0f}%"
            bear_prob_str = f"{bear_prob_val * scale:.0f}%"
            logger.warning(
                "Scenario probabilities summed to %.0f%%, not 100%% — rescaled to bull %s, base %s, bear %s",
                total_prob, bull_prob_str, base_prob_str, bear_prob_str,
            )

    # ── Combine into final report ─────────────────────────────────────────────
    logger.info("\n Combining into final scenario report...")
    final_report = ""
    try:
        combiner_chain = get_scenario_report_combiner_chain(llm)
        final_report = combiner_chain.invoke({
            "ticker": ticker,
            # Bull
            "bull_target": _safe(bull_result, "price_target"),
            "bull_upside": _safe(bull_result, "upside_downside"),
            "bull_probability": bull_prob_str,
            "bull_drivers": _fmt_list(_safe(bull_result, "key_drivers", [])),
            "bull_assumptions": _fmt_list(_safe(bull_result, "assumptions", [])),
            "bull_analysis": _safe(bull_result, "analysis"),
            # Base
            "base_target": _safe(base_result, "price_target"),
            "base_upside": _safe(base_result, "upside_downside"),
            "base_probability": base_prob_str,
            "base_drivers": _fmt_list(_safe(base_result, "key_drivers", [])),
            "base_assumptions": _fmt_list(_safe(base_result, "assumptions", [])),
            "base_analysis": _safe(base_result, "analysis"),
            # Bear
            "bear_target": _safe(bear_result, "price_target"),
            "bear_upside": _safe(bear_result, "upside_downside"),
            "bear_probability": bear_prob_str,
            "bear_drivers": _fmt_list(_safe(bear_result, "key_drivers", [])),
            "bear_assumptions": _fmt_list(_safe(bear_result, "assumptions", [])),
            "bear_analysis": _safe(bear_result, "analysis"),
            # Summaries
            "analyst_summary": analyst_text[:2000] if analyst_text else "N/A",
            "credit_summary": credit_text[:1000] if credit_text else "N/A"
    })
        logger.info(f"    Final report: {len(final_report)} chars")
    except Exception as e:
        logger.error(f"    Error generating combined report: {e}")
        final_report = (
            f"# Bull / Bear / Base Scenario Analysis: {ticker}\n\n"
            f"Error generating combined report: {e}\n\n"
            f"**Bull Case**: Target {_safe(bull_result, 'price_target')} "
            f"({_safe(bull_result, 'upside_downside')} upside)\n\n"
            f"**Base Case**: Target {_safe(base_result, 'price_target')} "
            f"({_safe(base_result, 'upside_downside')})\n\n"
            f"**Bear Case**: Target {_safe(bear_result, 'price_target')} "
            f"({_safe(bear_result, 'upside_downside')} downside)\n"
        )

    if scenario_warnings:
        final_report += "\n\n---\n\n" + "\n\n".join(scenario_warnings)

    logger.info("\n" + "=" * 80)
    logger.info(" SCENARIO REPORT COMPLETE")
    logger.info("=" * 80 + "\n")

    return {
        "messages": [AIMessage(content=final_report)],
        "scenario_report": final_report,
        "Intermediate_message": final_report,
        "web_searched": True
    }

# ============================================================================
# MACRO FRAMEWORK
# ============================================================================

def detect_macro_query(state):
    """Detect if the query is asking for macroeconomic data."""
    logger.info("=" * 80)
    logger.info(" MACRO QUERY DETECTION")
    logger.info("=" * 80)

    # If alpha or scenario mode already active, skip macro detection
    if state.get("alpha_mode", False) or state.get("scenario_mode", False):
        logger.warning(" Higher priority mode active – skipping macro detection")
        logger.info("=" * 80 + "\n")
        return {"macro_mode": False}

    messages = state["messages"]
    question = messages[-1].content.lower()

    # Simple keyword detection
    macro_keywords = [
        "macro", "gdp", "inflation", "cpi", "pce", "ppi", "eci",
        "gross domestic product", "consumer price index", "employment cost",
        "producer price index", "economy", "interest rate",
        "yield curve", "yield spread", "treasury yield", "treasury curve",
        "bond yield", "fed funds", "federal funds", "fedfunds",
        "maturity", "10-year", "2-year", "30-year", "t-bill", "t-bond"
    ]
    
    is_macro = any(kw in question for kw in macro_keywords)
    
    if is_macro:
        logger.info(" MACRO MODE ACTIVATED")
        logger.info(f"   Query: {question}")
        logger.info("=" * 80 + "\n")
        return {"macro_mode": True}
    else:
        logger.info(" Normal query (not a Macro request)")
        logger.info("=" * 80 + "\n")
        return {"macro_mode": False}

def macro_analyze_query(state):
    """
    MACRO STEP 1: Understand the user's macro query.
    
    Uses an LLM to extract structured parameters from the natural language query:
    - Which indicator(s) to fetch
    - Time granularity (monthly vs quarterly)
    - Specific periods (or 'latest' if none mentioned)
    - Comparison type (YoY, QoQ/MoM)
    
    Output is fully inspectable in state['macro_analysis'] before any data is fetched.
    """
    logger.info("=" * 80)
    logger.info(" MACRO STEP 1: QUERY ANALYSIS")
    logger.info("=" * 80)
    
    messages = state["messages"]
    question = messages[-1].content
    
    from pydantic import BaseModel, Field
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage
    
    class MacroExtraction(BaseModel):
        indicator: str = Field(description=(
            "The macro indicator code: GDP, GDPCA, CPI, PCE, PPI, ECI, FEDFUNDS, "
            "GS1M, GS3M, GS6M, GS1, GS2, GS3, GS5, GS7, GS10, GS20, GS30, or 'ALL'."
        ))
        period1: Optional[str] = Field(None, description=(
            "The primary period. Use EXACT format from the user's query: "
            "'Q1 2026' for quarters, 'January 2025' for months. "
            "Leave None if no date is mentioned (will use latest available)."
        ))
        period2: Optional[str] = Field(None, description=(
            "The comparison period. Same format rules as period1. "
            "Leave None to auto-calculate based on comparison_type."
        ))
        granularity: str = Field("native", description=(
            "'annual' if user specifies a year (e.g. 2025), "
            "'monthly' if user specifies months (January, Feb, etc.), "
            "'quarterly' if user specifies quarters (Q1, Q2), "
            "'native' if no specific period is mentioned (system uses the metric's default frequency)."
        ))
        comparison_type: str = Field("YoY", description=(
            "The comparison type to use ('YoY' or 'QoQ'). "
            "If the user does NOT explicitly specify a type: for GDP default to 'QoQ', for all others (CPI, ECI, etc) default to 'YoY'."
        ))
        duration: Optional[str] = Field(None, description=(
            "If the user asks for a historical trend or a chart over time, extract the duration "
            "(e.g., '12M' for 12 months, '5Y' for 5 years, '10Y' for 10 years). Leave None if no trend is requested."
        ))

    class MacroQueryPlan(BaseModel):
        queries: List[MacroExtraction] = Field(description="List of macro queries to execute.")

    planner_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    structured_llm = planner_llm.with_structured_output(MacroQueryPlan)
    
    system_prompt = MACRO_PLANNER_SYSTEM_PROMPT
    
    try:
        plan = structured_llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=question)
        ])
        queries_list = [q.model_dump() for q in plan.queries]
    except Exception as e:
        logger.error(f"   ✗ Analysis failed: {e}. Falling back to ALL indicators.")
        queries_list = [{"indicator": "ALL", "period1": None, "period2": None,
                         "granularity": "native", "comparison_type": "YoY", "duration": None}]
    
    # Log the analysis for transparency
    logger.info(f"   Extracted {len(queries_list)} query/queries:")
    for i, q in enumerate(queries_list, 1):
        logger.info(f"     [{i}] {q['indicator']} | {q['granularity']} | "
              f"{q.get('period1', 'latest')} vs {q.get('period2', 'auto')} | {q['comparison_type']}")
    
    logger.info("=" * 80 + "\n")
    
    return {
        "macro_analysis": {
            "original_question": question,
            "queries": queries_list
        }
    }


def macro_fetch_and_calculate(state):
    """
    MACRO STEP 2: Fetch data and run deterministic calculations.
    
    This node is 100% deterministic Python — NO LLM involved.
    Takes the structured analysis from Step 1, fetches the correct data
    from local CSVs at the correct granularity, and computes the math.
    
    Rules enforced here (not by LLM):
    - No date mentioned → uses latest available data, records actual period used
    - Future/unavailable date → falls back to latest, records explicit warning
    - Monthly granularity → skips quarterly aggregation, uses raw monthly values
    """
    logger.info("=" * 80)
    logger.info(" MACRO STEP 2: FETCH & CALCULATE (deterministic)")
    logger.info("=" * 80)
    
    from app.utils.macro_utils import get_macro_comparison, get_all_macro_latest
    
    analysis = state.get("macro_analysis", {})
    queries = analysis.get("queries", [])
    
    calculation_results = []
    
    for i, q in enumerate(queries, 1):
        indicator = q["indicator"].upper()
        period1 = q.get("period1")
        period2 = q.get("period2")
        granularity = q.get("granularity", "native")
        comparison_type = q.get("comparison_type", "YoY")
        duration = q.get("duration")
        
        logger.info(f"   [{i}/{len(queries)}] {indicator} | {granularity} | "
              f"{period1 or 'latest'} vs {period2 or 'auto'} | {comparison_type} | duration={duration}")
        
        if indicator == "ALL":
            result = get_all_macro_latest(comparison_type=comparison_type)
        else:
            result = get_macro_comparison(
                indicator=indicator,
                period1=period1,
                period2=period2,
                comparison_type=comparison_type,
                granularity=granularity
            )
        
        # Log what actually happened
        if "error" in result:
            logger.error(f"       ✗ Error: {result['error']}")
        else:
            actual_p1 = result.get("period1", "N/A")
            actual_p2 = result.get("period2", "N/A")
            val1 = result.get("val1", "N/A")
            val2 = result.get("val2", "N/A")
            logger.info(f"       ✓ {actual_p1}={val1} vs {actual_p2}={val2}")
            if "info" in result:
                logger.info(f"       ⚠ Fallback: {result['info']}")
                
        # If a trend/duration was requested, fetch the historical data so the LLM can write a factual summary
        if duration and indicator != "ALL":
            from app.utils.macro_utils import load_indicator_data
            import pandas as pd
            df = load_indicator_data(indicator)
            if df is not None and not df.empty:
                df = df.sort_values(by="date")
                duration_upper = duration.upper()
                months = int(duration_upper[:-1]) if duration_upper.endswith("M") else (int(duration_upper[:-1]) * 12 if duration_upper.endswith("Y") else 12)
                
                latest_date = df['date'].max()
                start_date = latest_date - pd.DateOffset(months=months)
                df_history = df[df['date'] >= start_date]
                
                history_dict = {}
                for _, row in df_history.iterrows():
                    history_dict[row['date'].strftime('%Y-%m')] = row['value']
                result["history_trend"] = history_dict
        
        calculation_results.append({
            "requested": {
                "indicator": indicator,
                "period1": period1,
                "period2": period2,
                "granularity": granularity,
                "comparison_type": comparison_type,
                "duration": duration
            },
            "result": result
        })
    
    # Post-process for Yield Spread (if exactly 2 rate queries)
    if len(calculation_results) == 2:
        res1 = calculation_results[0]["result"]
        res2 = calculation_results[1]["result"]
        
        from app.utils.macro_utils import calculate_yield_spread
        spread_info = calculate_yield_spread(res1, res2)
        
        if spread_info:
            calculation_results.append({
                "requested": {"special": "yield_spread_calculation"},
                "result": spread_info
            })

    logger.info(f"\n   Completed {len(calculation_results)} calculation(s)")
    logger.info("=" * 80 + "\n")
    
    return {"macro_calculation_results": calculation_results}


def macro_format_answer(state):
    """
    MACRO STEP 3: Format the calculation results into a professional answer.
    
    Takes the deterministic calculation results from Step 2 and uses an LLM
    ONLY for natural language formatting. The numbers are already computed
    and final — the LLM cannot change them, only present them.
    
    Also handles yield curve chart generation if applicable.
    """
    logger.info("=" * 80)
    logger.info(" MACRO STEP 3: FORMAT ANSWER")
    logger.info("=" * 80)
    
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
    
    analysis = state.get("macro_analysis", {})
    question = analysis.get("original_question", "")
    calc_results = state.get("macro_calculation_results", [])
    
    # Build data context from deterministic results
    data_context = ""
    for i, item in enumerate(calc_results, 1):
        data_context += f"Query {i}:\n"
        data_context += f"  Requested: {item['requested']}\n"
        data_context += f"  Result: {item['result']}\n\n"
    
    # ──── Source Attribution: feed citation metadata to LLM ────
    from app.utils.macro_utils import build_source_attribution_context
    
    attribution_text = build_source_attribution_context(calc_results)
    if attribution_text:
        data_context += attribution_text
        logger.info("   Source attribution attached")

    synthesis_prompt = MACRO_SYNTHESIS_PROMPT + "\n\n" + MACRO_FEW_SHOT
    
    generator_llm = ChatOpenAI(model="gpt-4o", temperature=0)
    response = generator_llm.invoke([
        SystemMessage(content=synthesis_prompt),
        HumanMessage(content=f"Question: {question}\n\nCalculated Data:\n{data_context}")
    ])
    final_answer = response.content
    logger.info(f"   Generated response: {len(final_answer)} chars")
    
    # Dynamic Chart Generation via Tag Parsing
    chart_info = {"chart_url": None, "chart_filename": None}
    
    # Find any [CHART: ...] tag in the final answer
    chart_tag_pattern = re.compile(r'\[CHART:(.*?)\]', re.IGNORECASE)
    match = chart_tag_pattern.search(final_answer)
    
    if match:
        tag_content = match.group(1)
        # Parse key=value pairs, allowing for optional quotes
        kv_pattern = re.compile(r'(\w+)=["\']?([^"\'\s\]]+)["\']?')
        params = dict(kv_pattern.findall(tag_content))
        
        if "type" in params:
            c_type = params["type"]
            c_metrics = params.get("metrics", "").split(',') if params.get("metrics") else []
            c_duration = params.get("duration", "12M")
            c_period1 = params.get("period1")
            c_period2 = params.get("period2")
            
            # Fallback to state queries if period parameters are not in the tag
            if not c_period1 or not c_period2:
                queries = analysis.get("queries", [])
                for q in queries:
                    if not c_period1 and q.get("period1"):
                        c_period1 = q.get("period1")
                    if not c_period2 and q.get("period2"):
                        c_period2 = q.get("period2")
            
            logger.info(f"   Extracted CHART tag parameters: {params} | Resolved periods: period1={c_period1}, period2={c_period2}")
            chart_info = generate_dynamic_chart(c_type, c_metrics, c_duration, c_period1, c_period2)
            
            if chart_info and chart_info.get("chart_url"):
                md_image = f"\n![{c_type} chart]({chart_info['chart_url']})\n"
                final_answer = final_answer[:match.start()] + md_image + final_answer[match.end():]
            else:
                # Remove the tag silently if generation fails
                final_answer = final_answer[:match.start()] + final_answer[match.end():]
        else:
            # Tag malformed, remove it silently
            final_answer = final_answer[:match.start()] + final_answer[match.end():]
            
    logger.info("=" * 80 + "\n")
    
    return {
        "messages": [AIMessage(content=final_answer)],
        "macro_report": final_answer,
        "Intermediate_message": final_answer,
        "chart_url": chart_info.get("chart_url"),
        "chart_filename": chart_info.get("chart_filename")
    }

def generate_dynamic_chart(c_type: str, c_metrics: list, c_duration: str, period1: Optional[str] = None, period2: Optional[str] = None) -> dict:
    """
    Generate a dynamic macro chart based on type, metrics, and duration.
    Returns a dict with 'chart_url' and 'chart_filename'.
    """
    try:
        import plotly.graph_objects as go
        import pandas as pd
        import datetime
        import os
        from app.utils.macro_utils import load_indicator_data
        
        fig = go.Figure()
        
        def resolve_chart_dates(max_avail, period_str, duration_str):
            end_dt = max_avail
            if period_str:
                try:
                    from app.utils.macro_utils import parse_period_str
                    gran = "quarterly" if "Q" in period_str.upper() else "monthly"
                    p_end = parse_period_str(period_str, gran)
                    parsed_end = p_end.to_timestamp(how='start')
                    if parsed_end <= max_avail:
                        end_dt = parsed_end
                except Exception as ex:
                    logger.error(f"Failed to parse chart end period {period_str}: {ex}")
            
            try:
                if duration_str and duration_str.endswith("M"):
                    months = int(duration_str[:-1])
                    start_dt = end_dt - pd.DateOffset(months=months)
                elif duration_str and duration_str.endswith("Y"):
                    years = int(duration_str[:-1])
                    start_dt = end_dt - pd.DateOffset(years=years)
                else:
                    start_dt = end_dt - pd.DateOffset(months=12)
            except ValueError:
                logger.error(f"Failed to parse duration {duration_str}, defaulting to 12M")
                start_dt = end_dt - pd.DateOffset(months=12)
                
            return start_dt, end_dt
        
        if c_type == "yield_curve":
            # For yield curve, we forward the specific periods to show snapshot comparison
            return generate_yield_curve_chart(period1, period2)
            
        elif c_type == "spread_trend":
            if len(c_metrics) < 2:
                logger.info("spread_trend requires at least 2 metrics")
                return {"chart_url": None, "chart_filename": None}
                
            df1 = load_indicator_data(c_metrics[0])
            df2 = load_indicator_data(c_metrics[1])
            
            if df1 is None or df2 is None or df1.empty or df2.empty:
                return {"chart_url": None, "chart_filename": None}
                
            # Base end_date on the max date found in either dataset to handle lagging/offline data gracefully
            max_avail_date = max(df1['date'].max(), df2['date'].max())
            start_date, end_date = resolve_chart_dates(max_avail_date, period1, c_duration)
                
            df1 = df1.set_index('date').sort_index()
            df2 = df2.set_index('date').sort_index()
            
            # Combine the full datasets first, then forward-fill to align dates perfectly
            df_combined = pd.concat([df1['value'], df2['value']], axis=1, keys=['val1', 'val2']).ffill()
            
            # Slice by start_date and end_date, then drop remaining NaNs (points before both series started)
            df_combined = df_combined[(df_combined.index >= start_date) & (df_combined.index <= end_date)].dropna()
            
            if df_combined.empty:
                logger.info("No overlapping data found in the specified duration")
                return {"chart_url": None, "chart_filename": None}
                
            df_combined['spread'] = df_combined['val1'] - df_combined['val2']
            
            fig.add_trace(go.Scatter(
                x=df_combined.index,
                y=df_combined['spread'],
                mode='lines',
                name=f"{c_metrics[0]} - {c_metrics[1]} Spread",
                line=dict(color='#d62728', width=3)
            ))
            
            title = f"Yield Spread: {c_metrics[0]} minus {c_metrics[1]} ({c_duration})"
            y_title = "Spread (%)"
            
        elif c_type == "historical_trend":
            # Load each metric's DataFrame once and cache it — avoids double disk reads
            loaded_dfs = {}
            for metric in c_metrics:
                df = load_indicator_data(metric)
                if df is not None and not df.empty:
                    loaded_dfs[metric] = df
            
            max_avail_date = max(df['date'].max() for df in loaded_dfs.values()) if loaded_dfs else pd.Timestamp.now()
            start_date, end_date = resolve_chart_dates(max_avail_date, period1, c_duration)
                
            has_data = False
            for metric, df in loaded_dfs.items():
                # Filter and sort using the cached DataFrame
                df_filtered = df[(df['date'] >= start_date) & (df['date'] <= end_date)].sort_values('date')
                if not df_filtered.empty:
                    fig.add_trace(go.Scatter(
                        x=df_filtered['date'],
                        y=df_filtered['value'],
                        mode='lines',
                        name=metric,
                        line=dict(width=2)
                    ))
                    has_data = True
            
            if not has_data:
                logger.info("No data found for any of the metrics in the specified duration")
                return {"chart_url": None, "chart_filename": None}
                
            title = f"Historical Trend: {', '.join(c_metrics)} ({c_duration})"
            y_title = "Value"
            
        else:
            logger.info(f"Unknown chart type: {c_type}")
            return {"chart_url": None, "chart_filename": None}
            
        fig.update_layout(
            title=title,
            xaxis_title="Date",
            yaxis_title=y_title,
            template="plotly_white",
            height=500,
            width=800,
            hovermode="x unified"
        )
        
        return _save_and_upload_chart(fig, c_type, width=800, height=500, label="Dynamic chart")
        
    except Exception as e:
        logger.error(f"Dynamic chart generation error: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"chart_url": None, "chart_filename": None}


def generate_yield_curve_chart(period1: Optional[str] = None, period2: Optional[str] = None) -> dict:
    """
    Helper function to generate a Plotly yield curve chart for the specified period(s).
    Plots maturities from 1-Month to 30-Year.
    """
    try:
        import plotly.graph_objects as go
        import datetime
        import os
        from app.utils.macro_utils import get_macro_comparison
        
        maturities = [
            ("GS1M", "1M"),
            ("GS3M", "3M"),
            ("GS6M", "6M"),
            ("GS1", "1Y"),
            ("GS2", "2Y"),
            ("GS3", "3Y"),
            ("GS5", "5Y"),
            ("GS7", "7Y"),
            ("GS10", "10Y"),
            ("GS20", "20Y"),
            ("GS30", "30Y")
        ]
        
        x_labels = []
        y_val1 = []
        y_val2 = []
        
        actual_period1 = None
        actual_period2 = None
        
        for indicator, label in maturities:
            res = get_macro_comparison(indicator, period1, period2)
            if "error" not in res:
                if not actual_period1:
                    actual_period1 = res.get("period1")
                if not actual_period2 and period2:
                    actual_period2 = res.get("period2")
                
                x_labels.append(label)
                y_val1.append(res["val1"])
                if period2 and "val2" in res:
                    y_val2.append(res["val2"])
        
        if not y_val1:
            logger.info("No yield curve data found to plot")
            return {"chart_url": None, "chart_filename": None}
            
        fig = go.Figure()
        
        # Add primary period line
        fig.add_trace(go.Scatter(
            x=x_labels,
            y=y_val1,
            mode='lines+markers',
            name=str(actual_period1 or period1 or "Latest"),
            line=dict(color='#1f77b4', width=3),
            marker=dict(size=8, symbol='circle')
        ))
        
        # Add secondary period line if present
        if y_val2 and len(y_val2) == len(y_val1):
            fig.add_trace(go.Scatter(
                x=x_labels,
                y=y_val2,
                mode='lines+markers',
                name=str(actual_period2 or period2),
                line=dict(color='#ff7f0e', width=3, dash='dash'),
                marker=dict(size=8, symbol='diamond')
            ))
            
        title = "U.S. Treasury Yield Curve"
        if actual_period2:
            title += f": {actual_period1} vs {actual_period2}"
        elif actual_period1:
            title += f": {actual_period1}"
            
        fig.update_layout(
            title=title,
            xaxis_title="Maturity",
            yaxis_title="Yield (%)",
            template="plotly_white",
            height=500,
            width=800,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1
            ),
            hovermode="x unified"
        )
        
        return _save_and_upload_chart(fig, "yield_curve", width=800, height=500, label="Yield curve chart")
        
    except Exception as e:
        logger.error(f"Yield curve chart generation error: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"chart_url": None, "chart_filename": None}

