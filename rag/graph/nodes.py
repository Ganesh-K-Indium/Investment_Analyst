"This module contains all info about about the nodes in the graph"
import re
from datetime import datetime
from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq
from langchain_core.messages import AIMessage
from langchain_tavily import TavilySearch
from rag.prompts.prompts import (get_rag_chain,
                                                          get_question_rewriter_chain,
                                                          get_financial_analyst_grader_chain,
                                                          get_financial_data_extractor_chain)
from rag.vectordb.client import load_vector_database
from app.utils.company_mapping import get_ticker, TICKER_TO_COMPANY, get_company_name as map_ticker_to_company
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


def extract_financial_metrics_from_documents(documents, metrics_list):
    """
    Extract specific financial metrics from documents using intelligent parsing.
    Looks for patterns like "Current Assets: $X", "Total Liabilities $Y", etc.
    
    Args:
        documents: List of Document objects to search
        metrics_list: List of metric names to search for (e.g., ['current assets', 'current liabilities'])
    
    Returns:
        dict: Mapping of metric names to extracted values with their sources
    """
    import re
    
    extracted_data = {}
    
    # Common financial metric patterns
    metric_patterns = {
        'current assets': [r'current assets[:\s]+\$?([\d,]+(?:\.\d+)?)', r'total current assets[:\s]+\$?([\d,]+(?:\.\d+)?)'],
        'current liabilities': [r'current liabilities[:\s]+\$?([\d,]+(?:\.\d+)?)', r'total current liabilities[:\s]+\$?([\d,]+(?:\.\d+)?)'],
        'total assets': [r'total assets[:\s]+\$?([\d,]+(?:\.\d+)?)'],
        'total liabilities': [r'total liabilities[:\s]+\$?([\d,]+(?:\.\d+)?)'],
        'inventory': [r'inventory[:\s]+\$?([\d,]+(?:\.\d+)?)', r'inventories[:\s]+\$?([\d,]+(?:\.\d+)?)'],
        'shareholders equity': [r'shareholders[\']? equity[:\s]+\$?([\d,]+(?:\.\d+)?)', r'total equity[:\s]+\$?([\d,]+(?:\.\d+)?)'],
        'net income': [r'net income[:\s]+\$?([\d,]+(?:\.\d+)?)'],
        'revenue': [r'total revenue[:\s]+\$?([\d,]+(?:\.\d+)?)', r'revenue[:\s]+\$?([\d,]+(?:\.\d+)?)']
    }
    
    for doc in documents:
        content = doc.page_content.lower() if hasattr(doc, 'page_content') else str(doc).lower()
        
        for metric in metrics_list:
            metric_lower = metric.lower()
            
            # Find matching patterns for this metric
            patterns = []
            for key, pattern_list in metric_patterns.items():
                if key in metric_lower or metric_lower in key:
                    patterns.extend(pattern_list)
            
            # Try each pattern
            for pattern in patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                if matches:
                    # Clean the value (remove commas)
                    value = matches[0].replace(',', '')
                    try:
                        numeric_value = float(value)
                        if metric_lower not in extracted_data:
                            extracted_data[metric_lower] = {
                                'value': numeric_value,
                                'raw': matches[0],
                                'source': doc.metadata.get('title', 'Unknown') if hasattr(doc, 'metadata') else 'Unknown'
                            }
                            print(f"   ✓ Found {metric_lower}: ${value} (from {extracted_data[metric_lower]['source']})")
                            break
                    except ValueError:
                        continue
    
    return extracted_data




def generate_comparison_subqueries(companies: list, year: str = None) -> dict:
    if year is None:
        year = str(datetime.now().year)
    """
    Generate optimized sub-queries for company comparison WITHOUT LLM.

    These queries are specifically designed to retrieve data from 10-K reports
    with maximum accuracy and minimal retrieval time.

    Args:
        companies: List of company names to compare
        year: Year for comparison (default: 2024)

    Returns:
        dict: Sub-query analysis with pre-generated queries
    """
    sub_queries = []

    # Template structure optimized for 10-K reports
    # Using multiple search terms and specific document sections

    year_int = int(year) if isinstance(year, str) else year
    prior_year = year_int - 1

    for company in companies:
        # 1. REVENUE - Exact 10-K language
        sub_queries.append(
            f"{company} total revenues net revenues year ended December 31 {year} consolidated statements of operations"
        )

        # 2. NET INCOME - Exact bottom-line metric
        sub_queries.append(
            f"{company} net income loss year ended December 31 {year} per share diluted basic"
        )

        # 3. OPERATING INCOME - Before tax line
        sub_queries.append(
            f"{company} income from operations operating income year ended December 31 {year}"
        )

        # 4. EARNINGS GROWTH - Explicit comparison language
        sub_queries.append(
            f"{company} earnings growth increased or decreased from {prior_year} to {year} compared to {prior_year} percentage change"
        )

        # 5. R&D EXPENSES - Operating cost breakout
        sub_queries.append(
            f"{company} research and development costs and expenses year ended December 31 {year}"
        )

        # 6. TOTAL ASSETS - Balance sheet specific date
        sub_queries.append(
            f"{company} total assets as of December 31 {year} consolidated balance sheets"
        )

        # 7. TOTAL DEBT - Long-term obligations
        sub_queries.append(
            f"{company} long-term debt total liabilities as of December 31 {year} balance sheets"
        )

        # 8. PROFIT DRIVERS - MD&A results section
        sub_queries.append(
            f"{company} results of operations factors affecting our performance key business drivers {year}"
        )

        # 9. RISK FACTORS - Dedicated section
        sub_queries.append(
            f"{company} Item 1A risk factors risks and uncertainties that could affect our business {year}"
        )

    print(f"[FIXED QUERIES] Generated {len(sub_queries)} optimized sub-queries for {len(companies)} companies")
    print(f"[FIXED QUERIES] Skipped LLM query generation - using 10-K-optimized templates")

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


def _extract_years_from_question(question: str) -> list:
    """Extract explicitly mentioned 4-digit years (2000-2029) from the user question."""
    years = sorted(set(int(y) for y in re.findall(r'\b(20[0-2][0-9])\b', question)))
    return years if years else [datetime.now().year]


def generate_segment_subqueries(companies: list, question: str = "") -> dict:
    """
    Generate predefined sub-queries for segment reporting queries WITHOUT LLM.
    Optimized for 10-K segment disclosures (ASC 280).
    """
    requested_years = _extract_years_from_question(question)
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

    print(f"[SEGMENT QUERIES] Generated {len(sub_queries)} predefined sub-queries for {len(companies)} companies")
    print(f"[SEGMENT QUERIES] Requested years: {requested_years}")
    print(f"[SEGMENT QUERIES] Skipped LLM query generation - using 10-K segment templates")

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
    requested_years = _extract_years_from_question(question)
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

    print(f"[GEOGRAPHIC QUERIES] Generated {len(sub_queries)} predefined sub-queries for {len(companies)} companies")
    print(f"[GEOGRAPHIC QUERIES] Requested years: {requested_years}")
    print(f"[GEOGRAPHIC QUERIES] Skipped LLM query generation - using 10-K geographic templates")

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
    print("---QUERY ANALYSIS---")
    messages = state["messages"]
    question = messages[-1].content
    question_lower = question.lower()

    # -------------------------------------------------------------
    # COMPARISON MODE: Use fixed templates for known comparison queries
    # -------------------------------------------------------------
    is_comparison_mode = state.get("is_comparison_mode", False)

    if is_comparison_mode:
        print(" COMPARISON MODE DETECTED - Using pre-optimized 10-K queries")

        # Extract companies from state
        comparison_companies = []
        if state.get("comparison_company1"):
            comparison_companies.append(state["comparison_company1"])
        if state.get("comparison_company2"):
            comparison_companies.append(state["comparison_company2"])
        if state.get("comparison_company3"):
            comparison_companies.append(state["comparison_company3"])

        print(f" Companies: {', '.join(comparison_companies)}")

        # Generate fixed sub-queries using the year from state (fallback to current year)
        comparison_year = str(state.get("year_start") or state.get("year_end") or datetime.now().year)
        print(f" Comparison year: {comparison_year}")
        sub_query_analysis = generate_comparison_subqueries(comparison_companies, year=comparison_year)

        return {
            "companies_detected": comparison_companies,
            "sub_query_analysis": sub_query_analysis,
            "requested_years": sub_query_analysis["requested_years"],
            "sub_query_results": {}
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
                print(f" SEGMENT QUERY DETECTED - Using pre-optimized segment templates for {companies}")
                sub_query_analysis = generate_segment_subqueries(companies, question=question)
            else:
                print(f" GEOGRAPHIC QUERY DETECTED - Using pre-optimized geographic templates for {companies}")
                sub_query_analysis = generate_geographic_subqueries(companies, question=question)

            return {
                "companies_detected": companies,
                "sub_query_analysis": sub_query_analysis,
                "requested_years": sub_query_analysis["requested_years"],
                "sub_query_results": {}
            }
        else:
            print(f"  {seg_geo_type.upper()} query detected but no companies identified, falling through to LLM analysis")

    # -------------------------------------------------------------
    # NORMAL MODE: Continue with existing logic
    # -------------------------------------------------------------

    # UNIVERSAL APPROACH: Single LLM call for sub-query analysis
    print("---UNIVERSAL SUB-QUERY ANALYSIS---")
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    
    from rag.prompts.prompts import get_universal_sub_query_analyzer
    sub_query_analyzer = get_universal_sub_query_analyzer(llm)
    
    # Analyze the question
    analysis = sub_query_analyzer.invoke({"question": question})
    
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
    print(f"[ANALYSIS] Query Type: {analysis.query_type}")
    print(f"[ANALYSIS] Companies: {analysis.companies_detected if analysis.companies_detected else 'None'}")
    print(f"[ANALYSIS] Needs Sub-Queries: {analysis.needs_sub_queries}")
    print(f"[ANALYSIS] Requested Years: {analysis.requested_years}")
    
    if analysis.needs_sub_queries:
        print(f"[ANALYSIS] Generated {len(analysis.sub_queries)} sub-queries:")
        for i, sq in enumerate(analysis.sub_queries, 1):
            print(f"   {i}. {sq}")
        print(f"[ANALYSIS] Reasoning: {analysis.reasoning}")
    else:
        print(f"[ANALYSIS] Direct retrieval recommended: {analysis.reasoning}")
    
    # Return state updates
    return {
        "companies_detected": analysis.companies_detected,
        "sub_query_analysis": sub_query_analysis,
        "requested_years": analysis.requested_years,
        "sub_query_results": {}
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
        import re
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


def retrieve(state, config):
    """
    Retrieve documents relevant to the question using ticker-based collections.
    Supports multi-company retrieval by querying separate collections.
    """
    print("="*80)
    print(" TICKER-BASED RETRIEVAL (TEXT + IMAGES)")
    print("="*80)
    
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
    requested_years = state.get("requested_years") or sub_query_analysis.get("requested_years") or [datetime.now().year]

    # SEGMENT / GEOGRAPHIC OPTIMISATION:
    # A 10-K covers the filing year + 2 prior years (3-year comparative).
    #  span == 2  →  e.g. [2022, 2023, 2024]: the 2024 10-K already contains all three
    #                years → query ONLY the last year.
    #  span  > 2  →  e.g. [2020..2024]: no single 10-K covers the full range → query
    #                first + last (their 10-Ks together cover the entire window).
    if query_type in ("segment", "geographic") and len(requested_years) > 1:
        year_span = requested_years[-1] - requested_years[0]
        if year_span == 2:
            requested_years = [requested_years[-1]]
            print(f" Span=2y → querying only [{requested_years[0]}] (single 10-K covers all 3 years)")
        elif year_span > 2:
            requested_years = [requested_years[0], requested_years[-1]]
            print(f" Span={year_span}y → querying [{requested_years[0]}, {requested_years[-1]}] (first+last 10-K covers full range)")

    target_tickers = set()

    # Strategy: Strictly use provided inputs
    
    # 1. From Portfolio/Input (company_filter) - THIS IS THE SOURCE OF TRUTH
    if company_filter:
        for c in company_filter:
            if c and isinstance(c, str) and c.strip():
                # We assume these are already valid tickers from the API layer
                target_tickers.add(c.strip())
    
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

    print(f" Identified Target Tickers: {list(target_tickers) or 'None'}")
    
    # If primary_ticker was empty, set it to the first found ticker for downstream consistency
    if not primary_ticker and target_tickers:
        primary_ticker = list(target_tickers)[0]

    # ============================================================================
    # SUB-QUERY MODE: Targeted retrieval for each sub-query (Multi-Collection)
    # ============================================================================
    all_documents = []
    sub_query_results = {}
    seen_doc_ids = set()
    
    if needs_sub_queries and sub_queries:
        print(f"\n SUB-QUERY MODE: {len(sub_queries)} data points")
        print("-" * 80)
        
        for i, sq in enumerate(sub_queries, 1):
            print(f"\n {i}/{len(sub_queries)}: {sq}")

            # Intelligently detect which tickers are mentioned in THIS sub-query
            sq_tickers_for_step = detect_tickers_in_query(sq, target_tickers)

            # If no specific ticker detected, query ALL allowed tickers
            # (This handles cases where the sub-query doesn't explicitly mention a company)
            if not sq_tickers_for_step:
                print(f"     No specific company detected, querying all: {list(target_tickers)}")
                sq_tickers_for_step = target_tickers
            else:
                print(f"    Detected companies: {list(sq_tickers_for_step)}")
            
            if not sq_tickers_for_step:
                print(f"    No allowed tickers found. Skipping vector search.")
                sub_query_results[sq] = {"found": False, "doc_count": 0, "preview": None, "companies": [], "content_types": {'text': 0, 'image': 0}}
                continue
            
            # Query each relevant ticker collection for this sub-query
            step_docs = []
            for t_ticker in sq_tickers_for_step:
                try:
                    company_name = map_ticker_to_company(t_ticker.lower())
                    print(f"    Querying ticker_{t_ticker.lower()} ({company_name})...")

                    # Get instance for this ticker (DO NOT CREATE if missing)
                    db_instance = vectordb_mgr.get_instance(t_ticker, create_if_missing=False)

                    # Perform search per requested year to ensure representation
                    docs_from_ticker = 0
                    for year_filter in requested_years:
                        search_results = db_instance.hybrid_search(
                            query=sq,
                            content_type=None,
                            years=[year_filter],
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
                        print(f"       Found {docs_from_ticker} chunks")
                    else:
                        print(f"        No chunks found")

                except Exception as e:
                     # Likely collection not found (safe to ignore in retrieval)
                     print(f"       Collection not found or error: {e}")

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
                print(f"    Total: {len(step_docs)} chunks from {len(companies_found)} companies")
            else:
                print(f"    No chunks found for this sub-query")

    else:
        # ============================================================================
        # DIRECT MODE: Retrieval from one or more collections
        # ============================================================================
        print(f"\n DIRECT RETRIEVAL MODE")
        print("-" * 80)
        
        if not target_tickers:
             print(" No target tickers identified. Cannot perform vector search.")
             print(" Returning EMPTY (will trigger web search)")
             all_documents = []
        else:
            print(f" Searching collections for tickers: {', '.join(target_tickers)}")
            
            # Iterate through all identified tickers and merge results
            for target_ticker in target_tickers:
                try:
                    print(f"    Querying collection: ticker_{target_ticker}")
                    # DO NOT CREATE if missing
                    db_instance = vectordb_mgr.get_instance(target_ticker, create_if_missing=False)
                    
                    current_collection_docs = 0
                    for year_filter in requested_years:
                        search_results = db_instance.hybrid_search(
                            query=question,
                            content_type=None,
                            years=[year_filter],
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
                                    
                    print(f"       Found {current_collection_docs} unique chunks across requested years")
                    
                except Exception as e:
                    print(f"      Error searching collection for {target_ticker}: {e}")
            
            # Final stats
            content_types = {'text': 0, 'image': 0}
            companies_found = set()
            for doc in all_documents:
                if hasattr(doc, 'metadata'):
                    ctype = doc.metadata.get('content_type', 'text')
                    content_types[ctype] = content_types.get(ctype, 0) + 1
                    companies_found.add(doc.metadata.get('company', 'Unknown'))

            print(f"\nRetrieved {len(all_documents)} chunks total from {len(target_tickers)} collections")
            print(f"    {content_types['text']} text,  {content_types['image']} images")
            print(f"    {', '.join(sorted(companies_found))}")

    # Final summary
    print(f"\n{'='*80}")
    print(f" FINAL: {len(all_documents)} chunks ready")
    print(f"{'='*80}\n")
    
    return {
        "documents": all_documents,
        "vectorstore_searched": True,
        "sub_query_results": sub_query_results,
        "ticker": primary_ticker  # Store resolved ticker in state
    }


def generate(state):
    print("---GENERATE---")
    messages = state["messages"]
    question = messages[-1].content
    documents = state["documents"]
    
    # Enhanced logging for debugging
    print(f" Question: {question[:100]}...")
    print(f" Number of chunks: {len(documents) if documents else 0}")

    # Log chunk content preview for debugging
    if documents:
        for i, doc in enumerate(documents[:3]):  # Preview first 3 chunks
            if hasattr(doc, 'page_content'):
                content_preview = doc.page_content[:200].replace('\n', ' ')
            else:
                content_preview = str(doc)[:200].replace('\n', ' ')
            print(f" Chunk {i+1} preview: {content_preview}...")
    else:
        print(" WARNING: No chunks available for generation!")
    
    # Context-free mode - no conversation memory
    enriched_question = question
    
    print("---USING STANDARD GENERATION---")
    
    # CRITICAL: Smart truncate documents to prevent context overflow
    # GPT-4o has 128k token limit (~96k chars safe limit)
    total_chars = sum(len(doc.page_content) for doc in documents)
    MAX_TOTAL_CHARS = 150000  # Safe limit for generation
    
    if total_chars > MAX_TOTAL_CHARS:
        print(f"[DOC SIZE] {total_chars:,} chars exceeds limit ({MAX_TOTAL_CHARS:,}). Truncating ONLY web search documents.")
        
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
            print(f"[DOC SIZE] WARNING: Vectorstore docs exceed total budget ({vector_chars:,} chars). Absolute truncation required.")
            budget_per_doc = MAX_TOTAL_CHARS // max(len(vector_docs), 1)
            documents = [Document(page_content=d.page_content[:budget_per_doc], metadata=d.metadata) for d in vector_docs]
        elif web_docs:
            print(f"[DOC SIZE] Vectorstore docs take {vector_chars:,} chars. Truncating {len(web_docs)} web chunks into remaining {remaining_budget:,} chars.")
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
        print(f"[DOC SIZE] After truncation: {total_chars:,} chars")
    else:
        print(f"[DOC SIZE] {total_chars:,} chars (limit: {MAX_TOTAL_CHARS:,})")
    
    llm = ChatOpenAI(
        model="gpt-4o",
        temperature=0.3,
        timeout=30,  # Set timeout to prevent hanging
        request_timeout=30,
        max_retries=2
    )
    rag_chain = get_rag_chain(llm)
    
    generation_input = {
        "documents": documents,
        "question": enriched_question,
        "financial_formulas": "",
        "sub_query_summary": "",
        "extracted_metrics": ""
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
    print("---FINANCIAL ANALYST CHUNK GRADING---")
    messages = state["messages"]
    question = messages[-1].content
    documents = state["documents"]
    web_searched = state.get("web_searched", False)
    
    # Get query context
    sub_query_analysis = state.get("sub_query_analysis", {})
    query_type = sub_query_analysis.get("query_type", "single_company")
    companies_detected = sub_query_analysis.get("companies_detected", [])
    
    print(f"Query Type: {query_type}")
    
    # Context-aware Company Detection
    # If no companies detected in question, use context from portfolio/state
    if not companies_detected:
        ctx_ticker = state.get("ticker")
        ctx_filter = state.get("company_filter", [])
        
        if ctx_ticker:
            companies_detected = [ctx_ticker]
            print(f"Using context ticker: {ctx_ticker}")
        elif ctx_filter:
            companies_detected = ctx_filter
            print(f"Using portfolio context companies: {ctx_filter}")
            
    print(f"Companies Detected: {companies_detected}")
    print(f"Chunks to grade: {len(documents)}")

    # CRITICAL: Handle empty chunks case (e.g., company not in DB)
    if not documents or len(documents) == 0:
        print(" NO CHUNKS TO GRADE")
        print(" Returning INSUFFICIENT grade → Will trigger web search")

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
    print(f"  Sending {len(doc_previews)} documents ({total_chars} chars) to gpt-4o grader...")

    sub_queries = "\n".join([f"- {sq}" for sq in sub_query_analysis.get("sub_queries", [])]) if sub_query_analysis.get("sub_queries") else "None"

    try:
        # Perform single LLM call
        grade = analyst_grader.invoke({
            "question": question,
            "sub_queries": sub_queries,
            "doc_content": doc_preview_text
        })
        
        print(f"\n FINANCIAL ANALYST GRADE:")
        print(f"  Is Sufficient: {grade.is_sufficient}")
        if grade.missing_data_summary:
            print(f"  Missing Data: {grade.missing_data_summary}")
        
        overall_grade = "sufficient" if grade.is_sufficient else "insufficient"
        
        # Store grading result in state for decision-making
        grading_result = {
            "overall_grade": overall_grade,
            "can_answer": grade.is_sufficient,
            "missing_data_summary": grade.missing_data_summary,
            "company_coverage": [], # Removed complex coverage tracking
            "documents_graded_count": len(doc_previews)
        }

        print(f"\n GRADING COMPLETE: {len(documents)} chunks evaluated")
        print(f"   Next: Decision node will use this grading to determine if web search needed")

        return {
            "documents": documents,
            "financial_grading": grading_result
    }

    except Exception as e:
        print(f" Financial analyst grading failed: {e}")
        print("  Falling back to keeping all chunks")

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
    print("---WEB SEARCH (TRUSTED FINANCIAL DOMAINS ONLY)---")
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
        ['10-k', '10k', '10-q', '10q', 'annual report', 'md&a', 'mda', 
         'management discussion', 'sec filing', 'edgar'])
    
    target_company = companies_detected[0] if companies_detected else None
    
    if is_sec_filing_query and target_company:
        print(f"---SEC FILING QUERY DETECTED FOR {target_company.upper()}---")
        import re
        years = re.findall(r'\b(20\d{2})\b', question)
        
        if 'md&a' in question_lower or 'management discussion' in question_lower:
            search_query = f"{target_company} MD&A Management Discussion Analysis {' '.join(years) if years else ''} SEC 10-K site:sec.gov"
        elif '10-k' in question_lower or 'annual report' in question_lower:
            search_query = f"{target_company} 10-K annual report {' '.join(years) if years else ''} site:sec.gov"
        print(f"✓ Optimized search: {search_query}")
    
    # UNIVERSAL SUB-QUERY WEB SEARCH
    web_search_tool = TavilySearch(
        max_results=5, 
        include_raw_content=True,
        include_domains=TRUSTED_FINANCIAL_DOMAINS
    )
    
    documents = []
    total_chars = 0
    
    if sub_queries:
        print(f"---SUB-QUERY MODE: Searching individually for {len(sub_queries)} specific data points---")
        seen_doc_ids = set()
        
        for i, sq in enumerate(sub_queries, 1):
            print(f"   {i}. Web searching for: {sq}")
            
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
            
            print(f"      → Found {len(sources)} sources, {len(documents)} chunks unique total")

        print(f" ✓ Retrieved {len(documents)} unique chunks across all sub-queries")
    else:
        # Standard single search
        if search_query != question:
            print(f"Using optimized query for web search: {search_query[:150]}")
        else:
            print(f"Using original question for web search: {search_query[:150]}")

        print(f" Restricting search to {len(TRUSTED_FINANCIAL_DOMAINS)} trusted financial domains")
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

        print(f"Web search produced {len(documents)} chunks ({total_chars} total chars)")
    
    if not documents or total_chars < 100:
        print("WARNING: Web search returned minimal content, response may be incomplete")

    # Track sub-query results from web search
    sub_query_results = state.get("sub_query_results", {})
    if sub_queries and documents:
        print("---EXTRACTING SUB-QUERY RESULTS FROM WEB SEARCH---")
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
        print(f" Updated sub-query results: {found_count}/{len(sub_queries)} have data")

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
    print(f"Tavily response type: {type(docs)}")
    
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
                    print(f"  Source {i}: {title[:50]}... ({len(content)} chars)")
    
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
                    print(f"  Source {i}: {title[:50]}... ({len(content)} chars)")
            elif isinstance(d, str):
                sources.append({
                    "title": "Web Search Result",
                    "url": "",
                    "content": d
                })
    
    if not sources:
        # Fallback: convert entire response to string
        print("WARNING: Could not parse Tavily response structure, using raw output")
        return [{"title": "Web Search Result", "url": "", "content": str(docs)}]
    
    return sources


def integrate_web_search(state):
    """
    WEB SEARCH INTEGRATION: Builds a single query from missing data + ticker + company name,
    executes one search, and combines results with existing vectorstore documents.
    """
    print("---INTEGRATE WEB SEARCH---")
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
        
    print(f"  [DEBUG] grading missing_summary: {repr(missing_summary)}")
    
    # Is there a valid missing data summary? (Not None, not empty, and not specifically 'no chunks found in vector database')
    has_valid_missing_target = (
        bool(missing_summary_str) and 
        missing_summary_str.lower() != "none" and 
        "no chunks found" not in missing_summary_str.lower()
    )

    if has_valid_missing_target:
        # Missing data summary is the target - use it directly
        print("  [DEBUG] Using missing data summary for web search target.")
        query_parts.append(missing_summary_str)
    else:
        # Fallback to the original question only if there is no explicit missing data summary
        print("  [DEBUG] Using original question for web search fallback.")
        query_parts.append(question)

    search_query = " ".join(query_parts)
    print(f"  Search query: {search_query}")

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
        print(f"  Found {len(sources)} sources")

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
        print(f"  ERROR during web search: {e}")

    combined_documents = existing_documents + web_documents
    print(f"  Existing chunks: {len(existing_documents)} | New web chunks: {len(web_documents)} | Total: {len(combined_documents)}")

    return {
        "documents": combined_documents,
        "web_searched": True
    }


def show_result(state):
    print("---SHOW RESULT---")
    Final_answer = AIMessage(content=state["Intermediate_message"])

    print(f'SHOWING THE RESULTS: {Final_answer}')
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
                    
                    print(f" Detected {num_companies} company/companies in table")
                    print(f"  Header columns: {cells}")
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
                        print(f" Processing 3-company row: {metric_name}")
                        metrics_data[metric_name] = {
                            'company1': cells[1].strip(),
                            'company2': cells[2].strip(),
                            'company3': cells[3].strip()
                        }
    
    if metrics_data:
        print(f"✓ Successfully parsed {len(metrics_data)} metrics from table")
        # Debug: check first metric to see company3 data
        if metrics_data:
            first_metric = list(metrics_data.keys())[0]
            first_data = metrics_data[first_metric]
            print(f"   Sample metric '{first_metric}': company3='{first_data.get('company3')}'")
    else:
        print(" No metrics extracted from table")
    
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


def generate_comparison_chart(state):
    """
    SYNCHRONOUS chart generation node for company comparison.
    Supports both 2 and 3 company comparisons.
    Parses tabular data, creates bar chart, and uploads to Cloudinary.
    
    Note: This is a SYNCHRONOUS function for LangGraph compatibility.
    All async operations are handled internally where needed.
    """
    print("---GENERATING COMPARISON CHART---")
    
    try:
        import plotly.graph_objects as go
        import datetime
        
        # Get the generated answer
        answer = state.get("Intermediate_message", "")
        company1 = state.get("comparison_company1", "")
        company2 = state.get("comparison_company2", "")
        company3 = state.get("comparison_company3", None)
        
        # Debug logging
        print(f" DEBUG: company1='{company1}', company2='{company2}', company3='{company3}'")
        print(f" DEBUG: company3 type={type(company3)}, is None={company3 is None}, is empty={company3 == ''}")
        
        if not answer or not company1 or not company2:
            print(" Missing data for chart generation")
            return {"chart_url": None, "chart_filename": None}
        
        # Treat empty string as None for company3
        if company3 == "":
            company3 = None
        
        if company3:
            print(f"Generating chart for {company1} vs {company2} vs {company3}")
        else:
            print(f"Generating chart for {company1} vs {company2}")
        
        # Step 1: Parse table
        metrics_data = parse_markdown_table(answer)
        if not metrics_data:
            print("No metrics found in answer")
            return {"chart_url": None, "chart_filename": None}
        
        print(f"✓ Parsed {len(metrics_data)} metrics from table")
        
        # Step 2: Prepare chart data
        chart_data = prepare_chart_data(metrics_data, company1, company2, company3, max_metrics=8)
        if not chart_data['metrics']:
            print("No valid numeric metrics for charting")
            return {"chart_url": None, "chart_filename": None}
        
        print(f"✓ Prepared {len(chart_data['metrics'])} metrics for charting")
        
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
        
        print("✓ Chart created successfully")
        
        # Step 4: Save locally first
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        if company3:
            filename = f"comparison_{company1}_{company2}_{company3}_{timestamp}.png"
        else:
            filename = f"comparison_{company1}_{company2}_{timestamp}.png"
        output_dir = "generated_charts"
        
        import os
        os.makedirs(output_dir, exist_ok=True)
        local_path = os.path.join(output_dir, filename)
        
        try:
            fig.write_image(local_path, width=1000 if not company3 else 1200, height=600)
            print(f"✓ Chart saved locally: {local_path}")
        except Exception as e:
            print(f"Failed to save locally: {str(e)}")
        
        # Step 5: Try to upload to Cloudinary (non-blocking)
        chart_url = None
        try:
            import os
            from app.cloudinary import upload_to_cloudinary
            
            if os.getenv("CLOUDINARY_CLOUD_NAME"):
                print("Uploading chart to Cloudinary...")
                result = upload_to_cloudinary(local_path)
                
                if result.get("success"):
                    chart_url = result.get("url")
                    print(f"✓ Chart uploaded: {chart_url}")
                else:
                    print(f"Cloudinary upload failed: {result.get('error')}")
            else:
                print("Cloudinary not configured - chart saved locally only")
        except Exception as e:
            print(f"Cloudinary upload skipped: {str(e)}")
        
        return {
            "chart_url": chart_url,
            "chart_filename": filename
        }
    
    except ImportError as e:
        print(f"Missing required package: {e}")
        print("Install with: pip install plotly kaleido")
        return {"chart_url": None, "chart_filename": None}
    except Exception as e:
        print(f"Chart generation error: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"chart_url": None, "chart_filename": None}


# ============================================================================
# ALPHA FRAMEWORK NODES - Stock Buy Timing Analysis
# ============================================================================

def detect_alpha_query(state):
    """
    Detect if the query is asking about stock buy timing (ALPHA Framework trigger).
    
    Patterns detected:
    - "is it a good time to buy [company/ticker] stock?"
    - "should i buy [company/ticker]?"
    - "is now a good entry point for [ticker]?"
    """
    print("="*80)
    print(" ALPHA QUERY DETECTION")
    print("="*80)
    
    messages = state["messages"]
    question = messages[-1].content.lower()
    
    # Buy timing patterns
    alpha_patterns = [
        "good time to buy",
        "should i buy",
        "should i invest in",
        "entry point",
        "right time to buy",
        "buy now",
        "time to invest",
        "good buy",
        "worth buying",
        "alpha analysis of"
    ]
    
    # Check if query matches ALPHA pattern
    is_alpha_query =any(pattern in question for pattern in alpha_patterns)
    
    if is_alpha_query:
        print(" ALPHA MODE ACTIVATED")
        print(f"   Query: {question}")
        
        # Extract company/ticker from query
        from app.utils.company_mapping import get_ticker
        
        # Try to extract ticker from state first
        ticker = state.get("ticker")
        company_filter = state.get("company_filter", [])
        
        # If we have a ticker or company_filter, use it
        if ticker:
            target_ticker = ticker
        elif company_filter and len(company_filter) > 0:
            target_ticker = company_filter[0]
        else:
            # Fallback: Try to extract from question
            # Look for common ticker patterns
            words = question.split()
            target_ticker = None
            for word in words:
                cleaned = word.strip(',.?!').upper()
                if len(cleaned) <= 5 and cleaned.isalpha():
                    # Looks like a ticker
                    target_ticker = cleaned
                    break
        
        if not target_ticker:
            print(" WARNING: Could not extract ticker/company")
            target_ticker = "unknown"
        
        print(f"   Target: {target_ticker}")
        print("="*80 + "\n")
        
        return {
            "alpha_mode": True,
            "ticker": target_ticker,
            "alpha_dimensions": {},
            "alpha_report": ""
        }
    else:
        print(" Normal RAG query (not ALPHA)")
        print("="*80 + "\n")
        return {
            "alpha_mode": False
        }


def alpha_dimension_retrieve(state):
    """
    Retrieve dimension-specific data for ALPHA Framework.
    
    Fixed retrieval strategies per dimension:
    - Alignment: VectorDB only
    - Liquidity: VectorDB (60%) + Web (40%)
    - Performance: VectorDB only
    - Horizon: Web only
    - Action: Web only
    """
    print("="*80)
    print(" ALPHA DIMENSIONAL RETRIEVAL")
    print("="*80)
    
    ticker = state.get("ticker", "").upper()
    company_filter = state.get("company_filter", [])
    
    if not ticker and company_filter:
        ticker = company_filter[0].upper()
    
    print(f" Target: {ticker}\n")
    
    from app.services.vectordb_manager import get_vectordb_manager
    from langchain_tavily import TavilySearch
    
    vectordb_mgr = get_vectordb_manager()
    # All web searches restricted to trusted financial domains
    web_search = TavilySearch(max_results=3, include_domains=TRUSTED_FINANCIAL_DOMAINS)
    # Trends / notable trends (Horizon) fetched exclusively from SeekingAlpha
    web_search_seekingalpha = TavilySearch(max_results=3, include_domains=["seekingalpha.com"])

    alpha_dimensions = {}
    
    # -------------------------------------------------------------------------
    # ALIGNMENT: VectorDB (MD&A, Governance) + Form4 Insider Trading
    # -------------------------------------------------------------------------
    print(" [1/5] Alignment (Stakeholder Interests) - VectorDB + Form4 Insider Data")
    try:
        db_instance = vectordb_mgr.get_instance(ticker, create_if_missing=False)

        # Query for MD&A and governance documents
        alignment_queries = [
            f"{ticker} management discussion analysis MD&A",
            f"{ticker} governance board independence proxy statement",
            f"{ticker} related party transactions"
        ]

        alignment_docs = []
        for query in alignment_queries:
            results = db_instance.hybrid_search(query=query, content_type="text", limit=3)
            for point in results:
                if hasattr(point, 'payload'):
                    from langchain_core.documents import Document
                    doc = Document(
                        page_content=point.payload.get('page_content', ''),
                        metadata=point.payload.get('metadata', {})
                    )
                    alignment_docs.append(doc)

        # ── Form 4 insider trading advisory data ──────────────────────────────
        print("    Fetching Form 4 insider trading data…")
        try:
            from rag.utils.Insights_Form4.advisory_hub import get_advisory_report

            form4_report = get_advisory_report(ticker)

            if form4_report and "status" not in form4_report and "error" not in form4_report:
                lines = [f"INSIDER TRADING ANALYSIS (SEC Form 4) — {ticker}\n"]
                for issuer_name, detail in form4_report.items():
                    if issuer_name in ("error", "status", "message", "ticker"):
                        continue
                    #lines.append(f"Issuer: {issuer_name}")
                    #lines.append(f"Recommendation: {detail.get('Recommendation', 'N/A')}")
                    #lines.append(f"Net Insider Flow: ${detail.get('Net_Inside_Flow', 0):,.2f}")
                    #lines.append(f"Total Bought: ${detail.get('Total_Bought', 0):,.2f} ({int(detail.get('Total_Bought_Shares', 0)):,} shares)")
                    #lines.append(f"Total Sold:   ${detail.get('Total_Sold', 0):,.2f} ({int(detail.get('Total_Sold_Shares', 0)):,} shares)")
                    #lines.append(f"Transaction Count: {detail.get('Transaction_Count', 0)}")
                    lines.append(f"\nAnalyst Insight:\n{detail.get('Reason', 'No analysis available')}\n")

                from langchain_core.documents import Document
                insider_doc = Document(
                    page_content="\n".join(lines),
                    metadata={"source": "form4_insider_trading", "company": ticker, "content_type": "insider_trading"}
                )
                # Insert first so it's never cut off by the docs[:5] slice in format_docs
                alignment_docs.insert(0, insider_doc)
                print(f"    Form4 insider doc added ({len(form4_report)} issuer(s))")
            else:
                print(f"    No Form4 data in DB for {ticker} — skipping insider doc")
        except Exception as form4_err:
            print(f"    Form4 fetch error (non-fatal): {form4_err}")
        # ─────────────────────────────────────────────────────────────────────

        alpha_dimensions['alignment'] = {
            'source': 'vectordb+form4',
            'documents': alignment_docs[:5],  # Form4 doc at [0], then vectordb docs
            'query_count': len(alignment_queries)
        }
        print(f"    Total alignment docs: {len(alignment_docs[:5])}")

    except Exception as e:
        print(f"    Error: {e}")
        alpha_dimensions['alignment'] = {'source': 'vectordb+form4', 'documents': [], 'query_count': 0}
    
    # -------------------------------------------------------------------------
    # LIQUIDITY: VectorDB (risk factors) + Web (sector trends)
    # -------------------------------------------------------------------------
    print(" [2/5] Liquidity (Macro/Micro Environment) - VectorDB + Web")
    try:
        # VectorDB: Risk factors, commodity exposure
        liquidity_docs = []
        vdb_queries = [
            f"{ticker} risk factors competitive pressures",
            f"{ticker} commodity input cost exposure raw materials"
        ]
        
        for query in vdb_queries:
            results = db_instance.hybrid_search(query=query, content_type="text", limit=2)
            for point in results:
                if hasattr(point, 'payload'):
                    from langchain_core.documents import Document
                    doc = Document(
                        page_content=point.payload.get('page_content', ''),
                        metadata=point.payload.get('metadata', {})
                    )
                    liquidity_docs.append(doc)
        
        # Web: Sector headwinds, interest rate sensitivity
        web_queries = [
            f"{ticker} sector headwinds tailwinds industry trends",
            f"{ticker} interest rate sensitivity debt structure"
        ]
        
        for query in web_queries:
            web_results = web_search.invoke({"query": query})
            # Parse Tavily response using helper
            sources = _parse_tavily_response(web_results, query)
            for source in sources:
                from langchain_core.documents import Document
                doc = Document(
                    page_content=source['content'],
                    metadata={'source': 'web_search', 'url': source['url'], 'title': source['title']}
                )
                liquidity_docs.append(doc)
        
        alpha_dimensions['liquidity'] = {
            'source': 'vectordb+web',
            'documents': liquidity_docs,
            'query_count': len(vdb_queries) + len(web_queries)
        }
        print(f"    Retrieved {len(liquidity_docs)} chunks (mixed sources)")
        
    except Exception as e:
        print(f"    Error: {e}")
        alpha_dimensions['liquidity'] = {'source': 'vectordb+web', 'documents': [], 'query_count': 0}
    
    # -------------------------------------------------------------------------
    # PERFORMANCE: VectorDB only (10-year financials)
    # -------------------------------------------------------------------------
    print(" [3/5] Performance (Earnings & Fundamentals) - VectorDB")
    try:
        _cur_yr = datetime.now().year
        performance_queries = [
            f"{ticker} revenue net income latest annual fiscal year {_cur_yr} {_cur_yr + 1} financial results",
            f"{ticker} operating cash flow free cash flow income statement most recent annual {_cur_yr} {_cur_yr + 1}",
            f"{ticker} EBITDA margins ROE profitability metrics latest fiscal year {_cur_yr} {_cur_yr + 1}"
        ]
        
        performance_docs = []
        for query in performance_queries:
            results = db_instance.hybrid_search(query=query, content_type="text", limit=3)
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
            'documents': performance_docs[:6],
            'query_count': len(performance_queries)
        }
        print(f"    Retrieved {len(performance_docs[:6])} chunks")
        
    except Exception as e:
        print(f"    Error: {e}")
        alpha_dimensions['performance'] = {'source': 'vectordb', 'documents': [], 'query_count': 0}
    
    # -------------------------------------------------------------------------
    # HORIZON: SeekingAlpha only (trends, competitive positioning, moat)
    # -------------------------------------------------------------------------
    print(" [4/5] Horizon (Structural Opportunity & Moat) - SeekingAlpha")
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
        print(f"    Retrieved {len(horizon_docs)} chunks (SeekingAlpha)")

    except Exception as e:
        print(f"    Error: {e}")
        alpha_dimensions['horizon'] = {'source': 'seekingalpha', 'documents': [], 'query_count': 0}
    
    # -------------------------------------------------------------------------
    # ACTION: Web (RSI, SMA200, price, P/E, EBITDA) — all from trusted sources
    # -------------------------------------------------------------------------
    print(" [5/5] Action (RSI / SMA200 / Price / P/E / EBITDA) - Web")
    try:
        action_docs = []

        # Domains that reliably display live technical indicators
        web_search_technical = TavilySearch(
            max_results=3,
            include_domains=TRUSTED_FINANCIAL_DOMAINS
        )

        web_search_technical_stock_price = TavilySearch(
            max_results=5,
            include_domains=TRUSTED_FINANCIAL_DOMAINS
        )

        # -- RSI(14) and current price from web --------------------------------
        print("    Fetching RSI(14) and current price from web...")
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
            print(f"    Retrieved {len(rsi_sources)} RSI/price docs from web")
        except Exception as rsi_err:
            print(f"    RSI web search error (non-fatal): {rsi_err}")

        # -- SMA200 from web ---------------------------------------------------
        print("    Fetching SMA200 from web...")
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
            print(f"    Retrieved {len(sma_sources)} SMA200 docs from web")
        except Exception as sma_err:
            print(f"    SMA200 web search error (non-fatal): {sma_err}")
        
        # -- Current Stock Price from web ---------------------------------------------------
        print("    Fetching Current Stock Price from web...")
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
            print(f"    Retrieved {len(sma_sources)} Current Stock Price docs from web")
        except Exception as sma_err:
            print(f"    Current Stock Price web search error (non-fatal): {sma_err}")

        # -- EBITDA from web search (trusted financial domains) ---------------
        print("    Fetching EBITDA from web (trusted domains)...")
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
            print(f"    Retrieved {len(ebitda_sources)} EBITDA docs from web")
        except Exception as ebitda_err:
            print(f"    EBITDA web search error (non-fatal): {ebitda_err}")

        # -- P/E ratio from web search (trusted financial domains) ------------
        print("    Fetching P/E ratio from web (trusted domains)...")
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
            print(f"    Retrieved {len(pe_sources)} P/E docs from web")
        except Exception as pe_err:
            print(f"    P/E web search error (non-fatal): {pe_err}")

        alpha_dimensions['action'] = {
            'source': 'web',
            'documents': action_docs,
            'query_count': 5
        }
        print(f"    Total action docs: {len(action_docs)}")

    except Exception as e:
        print(f"    Error: {e}")
        alpha_dimensions['action'] = {
            'source': 'web',
            'documents': [],
            'query_count': 0
        }
    
    print("\n" + "="*80)
    print(f" RETRIEVAL COMPLETE: {sum(len(d.get('documents', [])) for d in alpha_dimensions.values())} total chunks")
    print("="*80 + "\n")
    
    return {
        "alpha_dimensions": alpha_dimensions
    }


def alpha_generate_report(state):
    """
    Generate ALPHA Framework report from dimensional analysis.
    Creates <100 word summaries for each dimension and combines into final report.
    """
    print("="*80)
    print(" ALPHA REPORT GENERATION")
    print("="*80 + "\n")
    
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
        print(f" Generating {dim_name}...")

        dim_data = alpha_dimensions.get(dim_key, {})
        docs = dim_data.get('documents', [])

        if dim_key == 'action':
            technical_docs = [d for d in docs if d.metadata.get('data_type') == 'technical']
            pe_docs        = [d for d in docs if d.metadata.get('data_type') == 'pe_ratio']
            ebitda_docs    = [d for d in docs if d.metadata.get('data_type') == 'ebitda']
            print(f"    ACTION docs → {len(technical_docs)} technical, {len(pe_docs)} P/E, {len(ebitda_docs)} EBITDA")

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
            chain = chain_func(llm)
            result = chain.invoke(invoke_kwargs)

            analysis = result.analysis

            dimension_outputs[dim_key] = {
                'analysis': analysis,
                'key_points': result.key_points,
                'recommendation': getattr(result, 'recommendation', '')
            }
            print(f"    ✓ {dim_name}: {len(analysis)} chars, {len(result.key_points)} points")

        except Exception as e:
            print(f"    ✗ Error: {e}")
            dimension_outputs[dim_key] = {
                'analysis': f"Analysis unavailable due to insufficient data.",
                'key_points': [],
                'recommendation': ''
            }
    
    # Combine into final report
    print("\n Combining dimensions into final report...")

    def _dim_with_recommendation(dim_key):
        """Return analysis text with Recommendation appended, ready for the combiner."""
        dim = dimension_outputs.get(dim_key, {})
        analysis = dim.get('analysis', 'N/A')
        rec = dim.get('recommendation', '')
        if rec:
            return f"{analysis}\n\n**Recommendation:** {rec}"
        return analysis

    try:
        combiner_chain = get_alpha_report_combiner_chain(llm)
        final_report = combiner_chain.invoke({
            "company": ticker,
            "ticker": ticker,
            "alignment": _dim_with_recommendation('alignment'),
            "liquidity": _dim_with_recommendation('liquidity'),
            "performance": _dim_with_recommendation('performance'),
            "horizon": _dim_with_recommendation('horizon'),
            "action": _dim_with_recommendation('action')
        })
        print(f"    ✓ Final report: {len(final_report)} chars")

    except Exception as e:
        print(f"    ✗ Error: {e}")
        final_report = f"# ALPHA Framework Analysis: {ticker}\n\nError generating report: {str(e)}"
    
    print("\n" + "="*80)
    print(" ALPHA REPORT COMPLETE")
    print("="*80 + "\n")
    
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
    print("=" * 80)
    print(" SCENARIO QUERY DETECTION")
    print("=" * 80)

    # If alpha mode already active, skip scenario detection
    if state.get("alpha_mode", False):
        print(" Alpha mode active – skipping scenario detection")
        print("=" * 80 + "\n")
        return {"scenario_mode": False}

    messages = state["messages"]
    question = messages[-1].content.lower()

    is_scenario_query = any(pattern in question for pattern in SCENARIO_PATTERNS)

    if is_scenario_query:
        print(" SCENARIO MODE ACTIVATED")
        print(f"   Query: {question}")

        # Resolve ticker from state (set by portfolio/session context)
        ticker = state.get("ticker")
        company_filter = state.get("company_filter", [])

        if not ticker and company_filter:
            ticker = company_filter[0].upper()

        if not ticker:
            # Fallback: look for an all-caps word ≤5 chars in the question
            words = question.upper().split()
            for word in words:
                cleaned = word.strip(",.?!\"'")
                if 2 <= len(cleaned) <= 5 and cleaned.isalpha():
                    ticker = cleaned
                    break

        if not ticker:
            ticker = "UNKNOWN"

        print(f"   Target ticker: {ticker}")
        print("=" * 80 + "\n")
        return {
            "scenario_mode": True,
            "ticker": ticker,
            "scenario_data": {},
            "scenario_report": ""
    }
    else:
        print(" Normal query (not a Scenario request)")
        print("=" * 80 + "\n")
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
    print("=" * 80)
    print(" SCENARIO DATA RETRIEVAL (Web-Search Only)")
    print("=" * 80)

    ticker = state.get("ticker", "UNKNOWN").upper()
    print(f" Target: {ticker}\n")

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
    print(" [1/6] Analyst ratings & brokerage price targets")
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
            print(f"    Warning: {e}")
    print(f"    {len(scenario_data['analyst_data'])} analyst sources collected")

    # -------------------------------------------------------------------------
    # 2. Valuation Metrics
    # -------------------------------------------------------------------------
    print(" [2/6] Valuation metrics")
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
            print(f"    Warning: {e}")
    print(f"    {len(scenario_data['valuation_data'])} valuation sources collected")

    # -------------------------------------------------------------------------
    # 3. Growth Catalysts (Bull drivers)
    # -------------------------------------------------------------------------
    print(" [3/6] Growth catalysts & bull drivers")
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
            print(f"    Warning: {e}")
    print(f"    {len(scenario_data['catalyst_data'])} catalyst sources collected")

    # -------------------------------------------------------------------------
    # 4. Downside Risks (Bear drivers)
    # -------------------------------------------------------------------------
    print(" [4/6] Downside risks & bear headwinds")
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
            print(f"    Warning: {e}")
    print(f"    {len(scenario_data['risk_data'])} risk sources collected")

    # -------------------------------------------------------------------------
    # 5. Credit Ratings
    # -------------------------------------------------------------------------
    print(" [5/6] Credit rating agency reports")
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
            print(f"    Warning: {e}")
    print(f"    {len(scenario_data['credit_data'])} credit sources collected")

    # -------------------------------------------------------------------------
    # 6. Macro & Sector Environment
    # -------------------------------------------------------------------------
    print(" [6/6] Macro & sector environment")
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
            print(f"    Warning: {e}")
    print(f"    {len(scenario_data['macro_data'])} macro sources collected")

    total = sum(len(v) for v in scenario_data.values())
    print(f"\n Retrieval complete: {total} total sources across 6 buckets")
    print("=" * 80 + "\n")

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
    print("=" * 80)
    print(" SCENARIO REPORT GENERATION")
    print("=" * 80 + "\n")

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
    print(" Generating Bull Case...")
    bull_result = None
    try:
        bull_chain = get_scenario_bull_chain(llm)
        bull_result = bull_chain.invoke({
            "ticker": ticker,
            "analyst_data": analyst_text,
            "valuation_data": valuation_text,
            "catalyst_data": catalyst_text
    })
        print(f"    Bull target: {bull_result.price_target}  upside: {bull_result.upside_downside}")
    except Exception as e:
        print(f"    Error: {e}")

    # ── Bear Case ─────────────────────────────────────────────────────────────
    print(" Generating Bear Case...")
    bear_result = None
    try:
        bear_chain = get_scenario_bear_chain(llm)
        bear_result = bear_chain.invoke({
            "ticker": ticker,
            "analyst_data": analyst_text,
            "risk_data": risk_text,
            "credit_data": credit_text
    })
        print(f"    Bear target: {bear_result.price_target}  downside: {bear_result.upside_downside}")
    except Exception as e:
        print(f"    Error: {e}")

    # ── Base Case ─────────────────────────────────────────────────────────────
    print(" Generating Base Case...")
    base_result = None
    try:
        base_chain = get_scenario_base_chain(llm)
        base_result = base_chain.invoke({
            "ticker": ticker,
            "analyst_data": analyst_text,
            "valuation_data": valuation_text,
            "macro_data": macro_text
    })
        print(f"    Base target: {base_result.price_target}  return: {base_result.upside_downside}")
    except Exception as e:
        print(f"    Error: {e}")

    def _fmt_list(lst):
        if not lst:
            return "N/A"
        return "\n".join(f"• {item}" for item in lst)

    # Fallback defaults if any case failed
    def _safe(result, field, default="N/A"):
        if result is None:
            return default
        return getattr(result, field, default) or default

    # ── Combine into final report ─────────────────────────────────────────────
    print("\n Combining into final scenario report...")
    final_report = ""
    try:
        combiner_chain = get_scenario_report_combiner_chain(llm)
        final_report = combiner_chain.invoke({
            "ticker": ticker,
            # Bull
            "bull_target": _safe(bull_result, "price_target"),
            "bull_upside": _safe(bull_result, "upside_downside"),
            "bull_probability": _safe(bull_result, "probability"),
            "bull_drivers": _fmt_list(_safe(bull_result, "key_drivers", [])),
            "bull_assumptions": _fmt_list(_safe(bull_result, "assumptions", [])),
            "bull_analysis": _safe(bull_result, "analysis"),
            # Base
            "base_target": _safe(base_result, "price_target"),
            "base_upside": _safe(base_result, "upside_downside"),
            "base_probability": _safe(base_result, "probability"),
            "base_drivers": _fmt_list(_safe(base_result, "key_drivers", [])),
            "base_assumptions": _fmt_list(_safe(base_result, "assumptions", [])),
            "base_analysis": _safe(base_result, "analysis"),
            # Bear
            "bear_target": _safe(bear_result, "price_target"),
            "bear_upside": _safe(bear_result, "upside_downside"),
            "bear_probability": _safe(bear_result, "probability"),
            "bear_drivers": _fmt_list(_safe(bear_result, "key_drivers", [])),
            "bear_assumptions": _fmt_list(_safe(bear_result, "assumptions", [])),
            "bear_analysis": _safe(bear_result, "analysis"),
            # Summaries
            "analyst_summary": analyst_text[:2000] if analyst_text else "N/A",
            "credit_summary": credit_text[:1000] if credit_text else "N/A"
    })
        print(f"    Final report: {len(final_report)} chars")
    except Exception as e:
        print(f"    Error generating combined report: {e}")
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

    print("\n" + "=" * 80)
    print(" SCENARIO REPORT COMPLETE")
    print("=" * 80 + "\n")

    return {
        "messages": [AIMessage(content=final_report)],
        "scenario_report": final_report,
        "Intermediate_message": final_report,
        "web_searched": True
    }
