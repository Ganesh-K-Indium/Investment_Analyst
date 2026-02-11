"This module contains all info about about the nodes in the graph"
import re
from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq
from langchain_core.messages import AIMessage
from langchain_tavily import TavilySearch
from rag.vectordb.chains import (get_retrival_grader_chain, get_rag_chain,
                                                          get_company_name, get_question_rewriter_chain,
                                                          get_financial_analyst_grader_chain,
                                                          get_financial_data_extractor_chain)
from rag.vectordb.client import load_vector_database
from app.utils.company_mapping import get_ticker
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
        'revenue': [r'total revenue[:\s]+\$?([\d,]+(?:\.\d+)?)', r'revenue[:\s]+\$?([\d,]+(?:\.\d+)?)'],
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


# CACHE for extracted documents to avoid re-processing
_extraction_cache = {}

def smart_extract_financial_data(documents, max_chars=80000):
    """
    SMART FINANCIAL DATA EXTRACTION with LLM + Better Fallback + Caching.
    
    STRATEGY:
    1. Use LLM to extract structured financial data from web docs
    2. Better fallback: Keep MORE content (not just 1K) when extraction fails
    3. Vectorstore docs: Keep as-is (already clean)
    4. Distribute budget wisely to use full character limit
    5. Cache results to avoid re-processing
    
    OPTIMIZATIONS:
    - Caching: Avoids re-processing same documents
    - LLM extraction: Structures financial data properly
    - Smart fallback: Keeps 3-5K chars (not 1K) for better context
    - Budget distribution: Uses full 80K budget efficiently
    
    Returns:
        documents with extracted financial data + rich content
    """
    if not documents:
        return []
    
    # Generate cache key based on document content hashes and max_chars
    try:
        cache_key = f"{hash(tuple(hash(d.page_content[:100]) for d in documents))}_{max_chars}_{len(documents)}"
    except:
        cache_key = f"{id(documents[0])}_{max_chars}_{len(documents)}"
    
    # Check cache first (MAJOR optimization - avoids re-processing)
    if cache_key in _extraction_cache:
        print(f"[CACHE HIT]  Reusing previously processed {len(documents)} documents")
        return _extraction_cache[cache_key]
    
    total_chars = sum(len(doc.page_content) for doc in documents)
    
    # If small enough, return as-is
    if total_chars <= max_chars:
        print(f"[DOC SIZE] {total_chars:,} chars (within {max_chars:,} limit) - keeping all content")
        _extraction_cache[cache_key] = documents
        return documents
    
    print(f"[EXTRACT] {total_chars:,} chars → {max_chars:,} chars target")
    
    # Separate web docs from vectorstore docs
    web_docs = []
    vectorstore_docs = []
    
    for doc in documents:
        source = doc.metadata.get("source", "") if hasattr(doc, 'metadata') else ""
        is_web_doc = source in ["web_search", "integrate_web_search", "financial_web_search"]
        
        if is_web_doc:
            web_docs.append(doc)
        else:
            vectorstore_docs.append(doc)
    
    print(f"[EXTRACT] Web: {len(web_docs)} docs, Vectorstore: {len(vectorstore_docs)} docs")
    
    # Extract web docs with LLM + smart fallback
    extracted_docs = []
    
    if web_docs:
        # Separate small docs (< 10K) from large docs (>= 10K)
        small_docs = [d for d in web_docs if len(d.page_content) < 15000]
        large_docs = [d for d in web_docs if len(d.page_content) >= 15000]
        
        print(f"[EXTRACT] Web docs: {len(small_docs)} small (< 15K chars), {len(large_docs)} large (>= 15K chars)")
        
        # Small docs: Add directly (no LLM processing needed)
        if small_docs:
            print(f"[EXTRACT]  Adding {len(small_docs)} small docs directly (no LLM needed)")
            extracted_docs.extend(small_docs)
        
        # Large docs: Process with LLM
        if large_docs:
            print(f"[EXTRACT] Processing {len(large_docs)} large documents with LLM...")
            
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
            extractor_chain = get_financial_data_extractor_chain(llm)
            
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            # Calculate budget per large doc
            web_budget = int(max_chars * 0.7)
            budget_per_web_doc = max(3000, web_budget // len(large_docs)) if large_docs else 5000
            
            def extract_from_doc(doc):
                """Extract financial data with LLM or use smart fallback."""
                try:
                    # Try LLM extraction first
                    content = doc.page_content[:8000]  # Use more content for extraction
                    structured_data = extractor_chain.invoke({"document_content": content})
                    
                    # Build structured summary
                    summary_parts = []
                    summary_parts.append(f"Company: {structured_data.company} | Year: {structured_data.year}")
                    
                    # Add all extracted metrics
                    if structured_data.revenue:
                        summary_parts.append(f"Revenue: {structured_data.revenue}")
                    if structured_data.net_income:
                        summary_parts.append(f"Net Income: {structured_data.net_income}")
                    if structured_data.operating_income:
                        summary_parts.append(f"Operating Income: {structured_data.operating_income}")
                    if structured_data.gross_profit:
                        summary_parts.append(f"Gross Profit: {structured_data.gross_profit}")
                    if structured_data.earnings_per_share:
                        summary_parts.append(f"EPS: {structured_data.earnings_per_share}")
                    
                    # If extraction worked, add original content for context
                    if len(summary_parts) > 1:
                        structured_summary = "\n".join(summary_parts)
                        # Add more original content for better context
                        additional_content = doc.page_content[:budget_per_web_doc - len(structured_summary)]
                        final_content = f"{structured_summary}\n\n---FULL CONTENT---\n{additional_content}"
                        print(f"    {structured_data.company} {structured_data.year}: Extracted + {len(final_content):,} chars content")
                    else:
                        # Extraction didn't find much, use more original content
                        final_content = doc.page_content[:budget_per_web_doc]
                        print(f"     Limited extraction, using {len(final_content):,} chars original content")
                    
                    return Document(
                        page_content=final_content,
                        metadata=doc.metadata
                    )
                    
                except Exception as e:
                    # SMART FALLBACK: Keep MORE content (3-5K, not 1K!)
                    fallback_content = doc.page_content[:budget_per_web_doc]
                    print(f"     Extraction failed, keeping {len(fallback_content):,} chars original content")
                    return Document(
                        page_content=fallback_content,
                        metadata=doc.metadata
                    )
        
            # Process large docs in parallel
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(extract_from_doc, doc): doc for doc in large_docs}
                for future in as_completed(futures):
                    extracted_docs.append(future.result())
    
    # Add vectorstore docs (keep as-is, truncate if needed)
    web_chars = sum(len(d.page_content) for d in extracted_docs)
    remaining_budget = max_chars - web_chars
    
    if vectorstore_docs and remaining_budget > 0:
        vs_total = sum(len(d.page_content) for d in vectorstore_docs)
        if vs_total <= remaining_budget:
            extracted_docs.extend(vectorstore_docs)
            print(f"[EXTRACT] All vectorstore docs fit ({vs_total:,} chars)")
        else:
            # Truncate vectorstore docs proportionally
            budget_per_vs = remaining_budget // len(vectorstore_docs)
            for doc in vectorstore_docs:
                if len(doc.page_content) <= budget_per_vs:
                    extracted_docs.append(doc)
                else:
                    extracted_docs.append(Document(
                        page_content=doc.page_content[:budget_per_vs],
                        metadata=doc.metadata
                    ))
            print(f"[EXTRACT] Vectorstore docs truncated to fit budget")
    
    # Calculate final stats
    final_chars = sum(len(d.page_content) for d in extracted_docs)
    reduction_pct = ((total_chars - final_chars) / total_chars * 100) if total_chars > 0 else 0
    
    print(f"[EXTRACT COMPLETE]")
    print(f"  Original: {total_chars:,} → Final: {final_chars:,} chars ({reduction_pct:.1f}% reduction)")
    print(f"  {len(extracted_docs)} documents with rich financial + contextual data")
    
    # Cache result
    _extraction_cache[cache_key] = extracted_docs
    
    return extracted_docs


def preprocess_and_analyze_query(state):
    """
    PREPROCESSING NODE: Analyze query and generate sub-queries if needed.
    Context-free - no memory or conversation history.
    
    UNIVERSAL SUB-QUERY ANALYZER:
    - Single LLM call extracts companies AND generates optimal sub-queries
    - Works for ALL query types: single-company, multi-company, financial calculations, temporal comparisons
    
    SMART CONTEXT REUSE (NEW):
    - If documents exist from previous turn AND query appears to be a follow-up, skip analysis
    """
    print("---QUERY ANALYSIS---")
    messages = state["messages"]
    question = messages[-1].content
    question_lower = question.lower()
    
    # -------------------------------------------------------------
    # SMART CONTEXT REUSE (moved from route_question for efficiency)
    # Intelligently detect intent and select optimal context source
    # -------------------------------------------------------------
    persisted_documents = state.get("documents", [])
    if persisted_documents and len(persisted_documents) > 0:
        print(f" Memory: Found {len(persisted_documents)} persisted documents from previous turn")
        
        # Intent Detection Keywords
        SUMMARIZE_KEYWORDS = ["summarize", "sum up", "recap", "give me a summary", "in short", "briefly", "tldr"]
        MORE_INFO_KEYWORDS = ["more details", "tell me more", "additional info", "what else", "expand on", "elaborate", "dig deeper"]
        FOLLOW_UP_KEYWORDS = ["explain", "what about", "and", "it", "they", "that", "those"]
        
        # Priority 1: Detect SUMMARIZE intent (highest efficiency - use conversation messages)
        is_summarize = any(keyword in question_lower for keyword in SUMMARIZE_KEYWORDS)
        if is_summarize:
            print(" Smart Reuse: SUMMARIZE query detected → Will use conversation messages")
            
            # Extract AI messages from conversation history
            ai_messages = []
            for msg in messages:
                if hasattr(msg, 'type') and msg.type == 'ai':
                    ai_messages.append(msg.content)
            
            return {
                "companies_detected": [],
                "context_strategy": "messages",
                "conversation_messages": ai_messages,
                "sub_query_analysis": {
                    "needs_sub_queries": False,
                    "query_type": "summarize",
                    "companies_detected": [],
                    "sub_queries": [],
                    "reasoning": "Summarize query detected - using conversation messages"
                },
                "sub_query_results": {}
            }
        
        # Priority 2: Detect MORE_INFO intent (incremental retrieval)
        is_more_info = any(keyword in question_lower for keyword in MORE_INFO_KEYWORDS)
        if is_more_info:
            print(" Smart Reuse: MORE_INFO query detected → Will perform incremental retrieval")
            return {
                "companies_detected": [],
                "context_strategy": "incremental",
                "sub_query_analysis": {
                    "needs_sub_queries": False,
                    "query_type": "more_info",
                    "companies_detected": [],
                    "sub_queries": [],
                    "reasoning": "More info query detected - will merge new retrieval with existing docs"
                },
                "sub_query_results": {}
            }
        
        # Priority 3: Detect generic FOLLOW_UP (use persisted documents)
        is_follow_up = any(keyword in question_lower for keyword in FOLLOW_UP_KEYWORDS)
        if is_follow_up:
            print(" Smart Reuse: FOLLOW_UP detected → Will use existing context")
            return {
                "companies_detected": [],
                "context_strategy": "documents",
                "sub_query_analysis": {
                    "needs_sub_queries": False,
                    "query_type": "follow_up",
                    "companies_detected": [],
                    "sub_queries": [],
                    "reasoning": "Follow-up query detected with existing context"
                },
                "sub_query_results": {}
            }

    
    # UNIVERSAL APPROACH: Single LLM call for sub-query analysis
    print("---UNIVERSAL SUB-QUERY ANALYSIS---")
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    
    from rag.vectordb.chains import get_universal_sub_query_analyzer
    sub_query_analyzer = get_universal_sub_query_analyzer(llm)
    
    # Analyze the question
    analysis = sub_query_analyzer.invoke({"question": question})
    
    # Convert to dict for state storage
    sub_query_analysis = {
        "needs_sub_queries": analysis.needs_sub_queries,
        "query_type": analysis.query_type,
        "companies_detected": analysis.companies_detected,
        "sub_queries": analysis.sub_queries,
        "reasoning": analysis.reasoning
    }
    
    # Log analysis results
    print(f"[ANALYSIS] Query Type: {analysis.query_type}")
    print(f"[ANALYSIS] Companies: {analysis.companies_detected if analysis.companies_detected else 'None'}")
    print(f"[ANALYSIS] Needs Sub-Queries: {analysis.needs_sub_queries}")
    
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
        "sub_query_results": {}
    }

def extract_multiple_companies_from_question(question, llm=None):
    """
    Extract multiple companies from the question for cross-referencing scenarios.
    Uses LLM-based extraction for more accurate company identification.
    """
    try:
        if llm:
            from rag.vectordb.chains import get_multi_company_extractor_chain
            
            # Use structured LLM extraction
            extractor_chain = get_multi_company_extractor_chain(llm)
            extraction_result = extractor_chain.invoke({"question": question})
            
            print(f"Extracted companies: {extraction_result.companies}, Primary: {extraction_result.primary_company}, Is comparison: {extraction_result.is_comparison}")
            
            return extraction_result.companies
        
    except Exception as e:
        print(f"Error in LLM-based company extraction: {e}")
    
    # Fallback to keyword matching
    company_mappings = {
        "amazon": ["amazon", "amzn", "amazon.com"],
        "berkshire": ["berkshire", "berkshire hathaway", "brk"],
        "google": ["google", "alphabet", "googl", "goog"],
        "Jhonson and Jhonosn": ["johnson", "jnj", "johnson & johnson", "johnson and johnson"],
        "jp morgan": ["jp morgan", "jpmorgan", "jpmc", "chase", "jpm"],
        "meta": ["meta", "facebook", "fb", "meta platforms"],
        "microsoft": ["microsoft", "msft", "ms"],
        "nvidia": ["nvidia", "nvda"],
        "tesla": ["tesla", "tsla"],
        "visa": ["visa", "v"],
        "walmart": ["walmart", "wmt"],
        "pfizer": ["pfizer", "pfe"]
    }
    
    question_lower = question.lower()
    detected_companies = []
    
    for standard_name, variations in company_mappings.items():
        for variation in variations:
            if variation in question_lower:
                if standard_name not in detected_companies:
                    detected_companies.append(standard_name)
                break
    
    # Also look for comparison keywords to determine if cross-referencing is likely
    comparison_keywords = ["compare", "versus", "vs", "against", "with", "between", "and"]
    has_comparison = any(keyword in question_lower for keyword in comparison_keywords)
    
    # If we found multiple companies or comparison keywords, return detected companies
    if len(detected_companies) > 1 or (len(detected_companies) >= 1 and has_comparison):
        return detected_companies
    
    # If only one company detected but no comparison context, still return it
    return detected_companies

def request_clarification(state):
    """
    HITL NODE: Generates a clarifying question using GPT-4o based on detected intent.
    This node creates an interrupt point where the user can specify exact requirements.
    """
    print("---REQUESTING CLARIFICATION---")
    messages = state["messages"]
    question = messages[-1].content
    context_strategy = state.get("context_strategy", "")
    query_type = state.get("sub_query_analysis", {}).get("query_type")
    
    # Get existing context preview
    existing_docs = state.get("documents", [])
    conversation_messages = state.get("conversation_messages", [])
    
    # Build context summary
    if context_strategy == "messages" and conversation_messages:
        context_preview = f"Previous conversation covered: {len(conversation_messages)} AI responses"
    elif existing_docs:
        topics = set()
        for doc in existing_docs[:5]:
            if hasattr(doc, 'metadata'):
                source = doc.metadata.get('source_file', 'Unknown')
                # Extract just the filename
                if '/' in source:
                    source = source.split('/')[-1]
                topics.add(source)
        context_preview = f"Existing documents about: {', '.join(list(topics)[:3])}"
    else:
        context_preview = "No prior context available"
    
    print(f" Query type: {query_type}")
    print(f" Context: {context_preview}")
    
    # Generate clarification question using GPT-4o
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    
    prompt = f"""You are helping clarify a user's follow-up query intent.

User's query: "{question}"
Detected intent: {query_type}
Context available: {context_preview}

Generate a concise clarifying question (max 2 sentences) that helps the user specify:
- For SUMMARIZE: Which specific parts or aspects to summarize
- For MORE_INFO: What specific additional information they want
- For FOLLOW_UP: How they want to refine or use the existing information

The question should:
1. Be actionable and specific
2. Offer 2-3 concrete examples
3. Include a "skip" option

Format:
[Your clarifying question with examples]
(Or type 'proceed' to continue as planned)

Clarifying question:"""
    
    response = llm.invoke(prompt)
    clarification_question = response.content.strip()
    
    print(f"Generated question: {clarification_question[:100]}...")
    print(f"{'='*80}\n")
    
    return {
        "clarification_needed": True,
        "clarification_request": clarification_question
    }

def process_clarification(state):
    """
    HITL NODE: Processes user's clarification using GPT-4o to extract actionable parameters.
    Parses the user's response to refine retrieval and generation strategies.
    """
    print("---PROCESSING CLARIFICATION---")
    user_response = state.get("user_clarification", "")
    original_question = state["messages"][-1].content
    original_intent = state.get("sub_query_analysis", {}).get("query_type")
    
    if not user_response:
        print(" No user clarification provided - proceeding with original intent")
        return {
            "clarification_needed": False,
            "clarified_intent": {"action": "proceed_original"}
        }
    
    print(f" User response: {user_response[:100]}...")
    
    # Check if user wants to skip
    skip_keywords = ["skip", "proceed", "continue", "no", "as planned", "go ahead"]
    if any(kw in user_response.lower() for kw in skip_keywords):
        print(" User skipped clarification - proceeding with original intent")
        return {
            "clarification_needed": False,
            "clarified_intent": {"action": "proceed_original"}
        }
    
    # Use GPT-4o to parse clarification
    from langchain_core.output_parsers import JsonOutputParser
    from pydantic import BaseModel, Field
    
    class ClarifiedIntent(BaseModel):
        action: str = Field(description="Action: 'summarize', 'retrieve_new', 'refine_followup', or 'proceed_original'")
        scope: str = Field(description="Specific scope or focus area mentioned by user")
        keywords: list = Field(description="Key terms or topics to focus on")
        exclude: list = Field(description="Topics or areas to exclude")
        additional_filters: dict = Field(description="Any specific constraints like date ranges, metrics, companies, etc.")
    
    parser = JsonOutputParser(pydantic_object=ClarifiedIntent)
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    
    prompt = f"""Parse the user's clarification and extract actionable parameters.

Original query: "{original_question}"
Detected intent: {original_intent}
User clarification: "{user_response}"

Extract:
1. Final action (summarize/retrieve_new/refine_followup/proceed_original)
2. Specific scope or focus area
3. Key topics to include
4. Topics to exclude (if any)
5. Any filters (dates, companies, metrics, document types, etc.)

{parser.get_format_instructions()}
"""
    
    try:
        response = llm.invoke(prompt)
        clarified_intent = parser.parse(response.content)
        
        print(f" Clarified Intent:")
        print(f"   Action: {clarified_intent.get('action')}")
        print(f"   Scope: {clarified_intent.get('scope')}")
        print(f"   Keywords: {clarified_intent.get('keywords')}")
        if clarified_intent.get('exclude'):
            print(f"   Exclude: {clarified_intent.get('exclude')}")
        print(f"{'='*80}\n")
        
        return {
            "clarification_needed": False,
            "clarified_intent": clarified_intent,
            "retrieval_constraints": clarified_intent.get("additional_filters", {})
        }
    
    except Exception as e:
        print(f" Error parsing clarification: {e}")
        print("Proceeding with original intent")
        return {
            "clarification_needed": False,
            "clarified_intent": {"action": "proceed_original"}
        }

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
        print(f"\n🎯 SUB-QUERY MODE: {len(sub_queries)} data points")
        print("-" * 80)
        
        for i, sq in enumerate(sub_queries, 1):
            print(f"\n {i}/{len(sub_queries)}: {sq}")
            
            # Detect company/ticker for THIS sub-query from the ALLOWED set
            sq_tickers_for_step = set()
            
            # Simple check: which of our valid target_tickers is mentioned in the sub-query?
            for t_ticker in target_tickers:
                # Check if ticker symbol is explicitly mentioned
                if t_ticker.lower() in sq.lower().split():
                     sq_tickers_for_step.add(t_ticker)
                # We could check company name map here if we had it available easily, 
                # but for simplicity we rely on the ticker symbol or just query all if unsure.
            
            # If no specific ticker mentioned in sub-query, query ALL allowed tickers
            if not sq_tickers_for_step:
                sq_tickers_for_step = target_tickers
            
            if not sq_tickers_for_step:
                print(f"  No allowed tickers found. Skipping vector search.")
                sub_query_results[sq] = {"found": False, "doc_count": 0, "preview": None, "companies": [], "content_types": {'text': 0, 'image': 0}}
                continue
                
            print(f"   Target Tickers for step: {list(sq_tickers_for_step)}")
            
            # Query each relevant ticker collection for this sub-query
            step_docs = []
            for t_ticker in sq_tickers_for_step:
                try:
                    # Get instance for this ticker (DO NOT CREATE if missing)
                    db_instance = vectordb_mgr.get_instance(t_ticker, create_if_missing=False)
                    
                    # Perform search
                    search_results = db_instance.hybrid_search(
                        query=sq,
                        content_type=None,
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
                            
                except Exception as e:
                     # Likely collection not found (safe to ignore in retrieval)
                     print(f"   (Collection not found or error for {t_ticker}: {e})")

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
            
            status = "Yes" if len(step_docs) > 0 else "No"
            print(f"   {status} {len(step_docs)} docs found")

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
                    
                    search_results = db_instance.hybrid_search(
                        query=question,
                        content_type=None,
                        limit=10, 
                        dense_limit=100,
                        sparse_limit=100
                    )
                    
                    # Convert to Documents and Deduplicate
                    current_collection_docs = 0
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
                                
                    print(f"       Found {current_collection_docs} unique docs")
                    
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

            print(f"\nRetrieved {len(all_documents)} total documents from {len(target_tickers)} collections")
            print(f"    {content_types['text']} text,  {content_types['image']} images")
            print(f"    {', '.join(sorted(companies_found))}")

    # Final summary
    print(f"\n{'='*80}")
    print(f" FINAL: {len(all_documents)} documents ready")
    print(f"{'='*80}\n")
    
    tool_call_entry = {
        "tool": "ticker_hybrid_retriever",
        "sub_queries_used": len(sub_queries) > 0,
        "hybrid_search": True,
        "primary_ticker": primary_ticker
    }
    
    return {
        "documents": all_documents,
        "vectorstore_searched": True,
        "tool_calls": state.get("tool_calls", []) + [tool_call_entry],
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
    print(f" Number of documents: {len(documents) if documents else 0}")
    
    # Log document content preview for debugging
    if documents:
        for i, doc in enumerate(documents[:3]):  # Preview first 3 docs
            if hasattr(doc, 'page_content'):
                content_preview = doc.page_content[:200].replace('\n', ' ')
            else:
                content_preview = str(doc)[:200].replace('\n', ' ')
            print(f" Doc {i+1} preview: {content_preview}...")
    else:
        print(" WARNING: No documents available for generation!")
    
    # ============================================================================
    # MESSAGE-BASED GENERATION: For "summarize" queries, use conversation messages
    # ============================================================================
    context_strategy = state.get("context_strategy", "documents")
    if context_strategy == "messages":
        print("\\n🧠 MESSAGE-BASED GENERATION MODE")
        print("-" * 80)
        
        conversation_messages = state.get("conversation_messages", [])
        if not conversation_messages:
            print("⚠️ No conversation messages found, falling back to document-based generation")
        else:
            print(f"📜 Using {len(conversation_messages)} previous AI responses")
            
            # Combine conversation messages
            context = "\\n\\n---\\n\\n".join(conversation_messages)
            
            # Create summarization prompt
            prompt = f"""Based on our previous conversation, please provide a concise summary.

Previous AI responses:
{context}

User's request: {question}

Please provide a clear, well-structured summary."""
            
            # Generate using Groq (fast and efficient for summarization)
            from langchain_groq import ChatGroq
            llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
            
            print(f" Generating summary with Groq...")
            response = llm.invoke(prompt)
            generation = response.content
            
            print(f" Summary generated ({len(generation)} chars)")
            print(f"{'='*80}\\n")
            
            return {"messages": [generation]}
    
    # Context-free mode - no conversation memory
    enriched_question = question
    conversation_history = ""
    
    # NEW: Check if this is a financial calculation query
    financial_calculation = state.get("financial_calculation", {})
    needs_calculation = financial_calculation.get("needs_calculation", False)
    
    if needs_calculation:
        print("---FINANCIAL CALCULATION MODE ENABLED---")
        metrics_needed = financial_calculation.get("metrics_needed", [])
        sub_queries = financial_calculation.get("sub_queries", [])
        sub_query_results = state.get("sub_query_results", {})
        
        print(f" Metrics to calculate: {metrics_needed}")
        print(f" Sub-queries: {len(sub_queries)} total, {len(sub_query_results)} with data")
        
        # Add financial formulas to the generation context
        FINANCIAL_FORMULAS = """
**FINANCIAL METRIC FORMULAS FOR CALCULATIONS:**

1. **ROE (Return on Equity)** = Net Income (annual) / Shareholders' Equity
2. **Revenue Growth (3 Years)** = ((Revenue at end of Year 3 - Revenue at beginning of Year 1) / Revenue at beginning of Year 1) × 100%
3. **Debt-to-Equity Ratio** = Total Debt / Total Equity
4. **Dividend Yield** = (Dividends per Share / Price per Share) × 100%
5. **P/E Ratio** = Price per Share / Earnings per Share
6. **Current Ratio** = Current Assets / Current Liabilities
7. **Quick Ratio** = (Current Assets - Inventory) / Current Liabilities
8. **Gross Margin** = (Revenue - Cost of Goods Sold) / Revenue
9. **Operating Margin** = Operating Income / Revenue
10. **Cash Ratio** = Cash and Cash Equivalents / Current Liabilities
11. **Interest Coverage Ratio** = Operating Income / Interest Expense
12. **Inventory Turnover** = Cost of Goods Sold / Average Inventory
13. **Payables Turnover** = Cost of Goods Sold / Average Accounts Payable
14. **Revenue Growth (YoY)** = [(Current Year Revenue - Prior Year Revenue) / Prior Year Revenue] × 100%
15. **Net CapEx** = Ending PP&E - Beginning PP&E + Depreciation Expense
16. **Cash Burn Rate** = (Net Cash Used in Operating Activities) / Cash & Cash Equivalents at beginning of period
17. **Return on Assets (ROA)** = Net Income / Total Assets

**DEFINITIONS:**
- **Current Assets**: Cash & Cash Equivalents, Short-term Investments, Accounts Receivable, Inventory (from Balance Sheet)
- **Current Liabilities**: Accounts Payable, Short-term Debt, Accrued Liabilities (from Balance Sheet)
- **PP&E**: Property, Plant & Equipment
- **Depreciation Expense**: From Income Statement

**INSTRUCTIONS:**
1. Extract the required data points from the provided documents
2. Apply the appropriate formula
3. Show your calculation step-by-step
4. If any required data is missing, clearly state what's missing and explain you cannot complete the calculation
5. Always cite the source documents for the numbers used
"""
        
        # Build sub-query results summary
        sub_query_summary = ""
        if sub_query_results:
            sub_query_summary = "\n\n**DATA GATHERED FOR SUB-QUERIES:**\n"
            for sq, results in sub_query_results.items():
                sub_query_summary += f"\n- {sq}:\n"
                for r in results[:2]:  # Show first 2 results per sub-query
                    sub_query_summary += f"  • {r[:200]}...\n"
        
        # NEW: Add extracted financial metrics to context
        extracted_metrics = state.get("extracted_financial_metrics", {})
        metrics_summary = ""
        if extracted_metrics:
            metrics_summary = "\n\n**EXTRACTED FINANCIAL DATA (Use these values for calculations):**\n"
            for metric, data in extracted_metrics.items():
                metrics_summary += f"- {metric.title()}: ${data['raw']} (Source: {data['source']})\n"
            print(f" Adding {len(extracted_metrics)} extracted metrics to generation context")
        
        print(" Adding financial formulas and calculation instructions to generation context")
    else:
        FINANCIAL_FORMULAS = ""
        sub_query_summary = ""
    
    print("---USING STANDARD GENERATION---")
    
    # CRITICAL: Smart truncate documents to prevent context overflow
    # GPT-4o has 128k token limit (~96k chars safe limit)
    total_chars = sum(len(doc.page_content) for doc in documents)
    MAX_TOTAL_CHARS = 80000  # Safe limit for generation
    
    # Check if financial query to prioritize financial data
    sub_query_analysis = state.get("sub_query_analysis", {})
    is_financial_query = sub_query_analysis.get("query_type") in ["financial_calculation", "multi_company"]
    
    # NEW: Structured financial data extraction (replaces lossy truncation)
    if total_chars > MAX_TOTAL_CHARS:
        documents = smart_extract_financial_data(documents, MAX_TOTAL_CHARS)
    else:
        print(f"[DOC SIZE] {total_chars:,} chars (limit: {MAX_TOTAL_CHARS:,})")
    
    
    llm = ChatOpenAI(
        model="gpt-4o",
        temperature=0.3,
        timeout=30,  # Set timeout to prevent hanging
        request_timeout=30,
        max_retries=2
    )
    #llm=ChatGroq(model="llama-3.3-70b-versatile")
    rag_chain = get_rag_chain(llm)
    
    # Pass documents (truncated if necessary)
    generation_input = {
        "documents": documents,
        "question": enriched_question,
        "financial_formulas": FINANCIAL_FORMULAS if needs_calculation else "",
        "sub_query_summary": sub_query_summary if needs_calculation else "",
        "extracted_metrics": metrics_summary if needs_calculation else ""
    }
    
    Intermediate_message = rag_chain.invoke(generation_input)

    retry_count = state.get("retry_count", 0)

    tool_call_entry = {
        "tool": "rag_chain",
        "financial_calculation_mode": needs_calculation
    }

    return {
        "Intermediate_message": Intermediate_message,
        "retry_count": retry_count + 1,
        "tool_calls": state.get("tool_calls", []) + [tool_call_entry]
    }


def grade_documents(state):
    """
    FINANCIAL ANALYST DOCUMENT GRADING: Evaluates documents like a financial analyst.
    
    NEW APPROACH:
    1. Identifies what financial metrics the question needs
    2. Scans documents to find which metrics ARE present
    3. Identifies which metrics are MISSING
    4. Returns grading with specific gap analysis
    
    This replaces binary yes/no grading with intelligent financial analysis.
    """
    print("---FINANCIAL ANALYST DOCUMENT GRADING---")
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
    print(f"Documents to grade: {len(documents)}")
    
    # CRITICAL: Handle empty documents case (e.g., company not in DB)
    if not documents or len(documents) == 0:
        print(" NO DOCUMENTS TO GRADE")
        print(" Returning INSUFFICIENT grade → Will trigger web search")
        
        return {
            "documents": [],
            "financial_grading": {
                "overall_grade": "insufficient",
                "can_answer": False,
                "missing_data_summary": "No documents found in vector database",
                "company_coverage": []
            },
            "tool_calls": state.get("tool_calls", []) + [{
                "tool": "financial_analyst_grader",
                "result": "no_documents"
            }]
        }
    
    # OPTIMIZATION: Smart sampling - don't send ALL docs to LLM
    # Vectorstore docs are already relevant (retrieved by semantic search)
    # Only need to carefully grade a representative sample
    
    web_docs = [d for d in documents if d.metadata.get("source", "") in ["web_search", "integrate_web_search", "financial_web_search"]]
    vectorstore_docs = [d for d in documents if d not in web_docs]
    
    print(f"  Vectorstore docs: {len(vectorstore_docs)} (high quality)")
    print(f"  Web docs: {len(web_docs)} (needs careful grading)")
    
    # Sample intelligently: All web docs + sample of vectorstore docs
    max_docs_to_grade = 15  # Reasonable limit for LLM context
    
    if len(documents) <= max_docs_to_grade:
        docs_to_grade = documents
        print(f"  Grading all {len(documents)} documents")
    else:
        # All web docs (they need careful analysis) + sample of vectorstore docs
        vectorstore_sample_size = max(5, max_docs_to_grade - len(web_docs))
        vectorstore_sample = vectorstore_docs[:vectorstore_sample_size]
        docs_to_grade = web_docs + vectorstore_sample
        print(f"  Smart sample: {len(web_docs)} web + {len(vectorstore_sample)}/{len(vectorstore_docs)} vectorstore = {len(docs_to_grade)} total")
    
    # Initialize financial analyst grader
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    analyst_grader = get_financial_analyst_grader_chain(llm)
    
    # Prepare document previews for grading
    doc_previews = []
    preview_chars_per_doc = min(900, 45000 // len(docs_to_grade))  # Distribute budget
    
    for i, doc in enumerate(docs_to_grade, 1):
        if hasattr(doc, 'page_content'):
            content = doc.page_content[:preview_chars_per_doc]
        elif isinstance(doc, dict) and 'page_content' in doc:
            content = doc['page_content'][:preview_chars_per_doc]
        else:
            content = str(doc)[:preview_chars_per_doc]
        
        # Include metadata for context
        metadata_str = ""
        if hasattr(doc, 'metadata'):
            company = doc.metadata.get("company", "Unknown")
            source = doc.metadata.get("source", "Unknown")
            metadata_str = f" [Company: {company}, Source: {source}]"
        
        doc_previews.append(f"--- Document {i} ---{metadata_str}\n{content}\n")
    
    doc_preview_text = "\n".join(doc_previews)
    
    print(f"  Invoking financial analyst grader on {len(docs_to_grade)} documents...")
    
    try:
        # Invoke financial analyst grader
        analyst_grade = analyst_grader.invoke({
            "question": question,
            "doc_count": len(documents),
            "doc_previews": doc_preview_text,
            "companies_detected": ", ".join(companies_detected) if companies_detected else "None",
            "query_type": query_type
        })
        
        print(f"\n FINANCIAL ANALYST GRADE: {analyst_grade.overall_grade.upper()}")
        print(f"Can Answer Question: {analyst_grade.can_answer_question}")
        print(f"Reasoning: {analyst_grade.reasoning}")
        
        # Log per-company coverage
        for company_coverage in analyst_grade.company_coverage:
            print(f"\n  Company: {company_coverage.company}")
            print(f"    Confidence: {company_coverage.confidence}")
            print(f"    Years: {', '.join(company_coverage.year_coverage) if company_coverage.year_coverage else 'Unknown'}")
            print(f"    Metrics Found: {', '.join(company_coverage.metrics_found[:5])}{'...' if len(company_coverage.metrics_found) > 5 else ''}")
            if company_coverage.metrics_missing:
                print(f"      Metrics Missing: {', '.join(company_coverage.metrics_missing[:3])}{'...' if len(company_coverage.metrics_missing) > 3 else ''}")
        
        if analyst_grade.missing_data_summary:
            print(f"\n   MISSING DATA: {analyst_grade.missing_data_summary}")
        
        # Store grading result in state for decision-making
        grading_result = {
            "analyst_grade": analyst_grade.dict(),
            "overall_grade": analyst_grade.overall_grade,
            "can_answer": analyst_grade.can_answer_question,
            "missing_data_summary": analyst_grade.missing_data_summary,
            "company_coverage": [cc.dict() for cc in analyst_grade.company_coverage]
        }
        
        # For now, keep all documents (decision node will use grading to determine if web search needed)
        # This is different from old approach which filtered here
        filtered_docs = documents  # Keep all docs, let decision node use grading
        
        tool_call_entry = {
            "tool": "financial_analyst_grader",
            "grade": analyst_grade.overall_grade,
            "can_answer": analyst_grade.can_answer_question
        }
        
        print(f"\n GRADING COMPLETE: {len(filtered_docs)} documents retained")
        print(f"   Next: Decision node will use this grading to determine if web search needed")
        
        return {
            "documents": filtered_docs,
            "financial_grading": grading_result,
            "tool_calls": state.get("tool_calls", []) + [tool_call_entry]
        }
        
    except Exception as e:
        print(f" Financial analyst grading failed: {e}")
        print("  Falling back to keeping all documents")
        
        # Fallback: keep all documents
        return {
            "documents": documents,
            "financial_grading": {"overall_grade": "partial", "can_answer": False, "error": str(e)},
            "tool_calls": state.get("tool_calls", []) + [{"tool": "financial_analyst_grader", "error": str(e)}]
        }


def transform_query(state):
    print("---TRANSFORM QUERY---")
    messages = state["messages"]
    question = messages[-1].content

    #llm = ChatGroq(model="llama-3.1-8b-instant")
    llm= ChatOpenAI(model="gpt-4o-mini")
    question_rewriter = get_question_rewriter_chain(llm)
    better_question = question_rewriter.invoke({"question": question})

    tool_call_entry = {
        "tool": "question_rewriter"
    }

    return {
        "messages": [better_question],
        "tool_calls": state.get("tool_calls", []) + [tool_call_entry]
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
            
            print(f"      → Found {len(sources)} sources, {len(documents)} unique total")
        
        print(f" ✓ Retrieved {len(documents)} unique documents across all sub-queries")
    else:
        # Standard single search
        if search_query != question:
            print(f"Using optimized query for web search: {search_query[:150]}")
        else:
            print(f"Using original question for web search: {search_query[:150]}")
        
        print(f" Restricting search to {len(TRUSTED_FINANCIAL_DOMAINS)} trusted financial domains")
        docs = web_search_tool.invoke({"query": search_query})
        
        # Parse Tavily response into individual sources
        sources = _parse_tavily_response(docs, search_query)
        
        # Create separate documents for each source
        for source in sources:
            # Include title and URL in document metadata for better traceability
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
        
        print(f"Web search created {len(documents)} separate documents with {total_chars} total characters")
    
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

    tool_call_entry = {
        "tool": "web_search",
        "sub_queries_used": len(sub_queries) > 0
    }

    return {
        "documents": documents,
        "web_searched": True,
        "tool_calls": state.get("tool_calls", []) + [tool_call_entry],
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


def financial_web_search(state):
    """
    Fallback web search when vectorstore has no relevant documents.
    Restricted to trusted financial domains, optimized for SEC filings.
    Creates separate documents per source for better context utilization.
    
    NOW WITH UNIVERSAL SUB-QUERY SUPPORT:
    - Uses sub_query_analysis from preprocessing
    - Optimizes searches for SEC EDGAR filings
    - Individual targeted searches for each data point
    """
    print("---FINANCIAL WEB SEARCH (SEC EDGAR FOCUSED)---")
    messages = state["messages"]
    question = messages[-1].content
    enriched_query = state.get("enriched_query", question)
    
    # Use universal sub-query analysis
    sub_query_analysis = state.get("sub_query_analysis", {})
    needs_sub_queries = sub_query_analysis.get("needs_sub_queries", False)
    sub_queries = sub_query_analysis.get("sub_queries", [])
    companies_detected = sub_query_analysis.get("companies_detected", [])
    
    # Optimize for SEC filings
    search_query = enriched_query if enriched_query != question else question
    question_lower = question.lower()
    is_sec_filing_query = any(kw in question_lower for kw in 
        ['10-k', '10k', '10-q', '10q', 'annual report', 'md&a', 'mda', 
         'management discussion', 'sec filing', 'edgar'])
    
    target_company = companies_detected[0] if companies_detected else None
    
    if is_sec_filing_query and target_company:
        print(f"✓ SEC FILING QUERY FOR {target_company.upper()}")
        import re
        years = re.findall(r'\b(20\d{2})\b', question)
        
        if 'md&a' in question_lower or 'management discussion' in question_lower:
            financial_search_query = f"{target_company} MD&A {' '.join(years) if years else ''} SEC 10-K financial data site:sec.gov"
        elif '10-k' in question_lower or 'annual report' in question_lower:
            financial_search_query = f"{target_company} 10-K {' '.join(years) if years else ''} financial statements site:sec.gov"
        else:
            financial_search_query = f"{search_query} financial data site:sec.gov/Archives/edgar"
        print(f"✓ Optimized query: {financial_search_query[:100]}")
    else:
        financial_search_query = f"{search_query} financial data detailed numbers"
    
    # UNIVERSAL SUB-QUERY FINANCIAL WEB SEARCH
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
        
        # Use the optimized SEC EDGAR query as base if we have it
        base_query = financial_search_query if target_company else None
        
        for i, sq in enumerate(sub_queries, 1):
            # For each sub-query, create a targeted search combining company + specific metric
            if target_company:
                # Example: "Meta 2023 current assets 10-K site:sec.gov/Archives/edgar"
                sq_query = f"{target_company} {sq} 10-K site:sec.gov/Archives/edgar"
            else:
                sq_query = sq
            
            print(f"   {i}. Financial web searching for: {sq_query[:80]}...")
            
            # Search specifically for this data point
            docs = web_search_tool.invoke({"query": sq_query})
            sources = _parse_tavily_response(docs, sq_query)
            
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
                            "source": "financial_web_search",
                            "title": source['title'],
                            "url": source['url']
                        }
                    )
                    documents.append(doc)
                    total_chars += len(source['content'])
            
            print(f"      → Found {len(sources)} sources, {len(documents)} unique total")
        
        print(f" ✓ Retrieved {len(documents)} unique documents across all sub-queries")
    else:
        # Standard single search with optimized query
        if financial_search_query != question:
            print(f"Using optimized query for financial web search: {financial_search_query[:150]}")
        else:
            print(f"Using original question for financial web search: {financial_search_query[:150]}")

        print(f" Restricting search to {len(TRUSTED_FINANCIAL_DOMAINS)} trusted financial domains")
        docs = web_search_tool.invoke({"query": financial_search_query})

        # Parse Tavily response into individual sources
        sources = _parse_tavily_response(docs, financial_search_query)
        
        # Create separate documents for each source
        for source in sources:
            # Include title and URL in document metadata for better traceability
            doc_content = f"**Source: {source['title']}**\n"
            if source['url']:
                doc_content += f"URL: {source['url']}\n\n"
            doc_content += source['content']
            
            doc = Document(
                page_content=doc_content,
                metadata={
                    "source": "financial_web_search",
                    "title": source['title'],
                    "url": source['url']
                }
            )
            documents.append(doc)
            total_chars += len(source['content'])
        
        print(f"Financial web search created {len(documents)} separate documents with {total_chars} total characters")
    
    if not documents or total_chars < 100:
        print("WARNING: Financial web search returned minimal content")

    # Track sub-query results from web search
    sub_query_results = state.get("sub_query_results", {})
    if sub_queries and documents:
        print("---EXTRACTING SUB-QUERY RESULTS FROM FINANCIAL WEB SEARCH---")
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
    
    # NEW: Extract actual financial metric values from web search documents
    extracted_metrics = {}
    sub_query_analysis = state.get("sub_query_analysis", {})
    is_financial_calc = sub_query_analysis.get("query_type") == "financial_calculation"
    
    if is_financial_calc and documents:
        print("---EXTRACTING FINANCIAL METRICS FROM WEB DOCUMENTS---")
        extracted_metrics = extract_financial_metrics_from_documents(documents)
        if extracted_metrics:
            print(f"✓ Successfully extracted {len(extracted_metrics)} financial metrics from web documents")
        else:
            print("  Could not extract specific numeric values from web documents")

    tool_call_entry = {
        "tool": "financial_web_search",
        "sub_queries_used": len(sub_queries) > 0,
        "metrics_extracted": len(extracted_metrics) > 0
    }

    return {
        "documents": documents,
        "web_searched": True,
        "tool_calls": state.get("tool_calls", []) + [tool_call_entry],
        "sub_query_results": sub_query_results,
        "extracted_financial_metrics": extracted_metrics
    }


def integrate_web_search(state):
    """
    SMART WEB SEARCH INTEGRATION: Uses targeted gap queries OR missing sub-queries.
    
    NEW ENHANCED APPROACH:
    1. PRIORITY: Use targeted queries from gap analysis (specific missing data points)
    2. FALLBACK: Use missing sub-queries if no gap analysis
    3. Combine web results with existing vectorstore documents
    
    This implements the gap analysis strategy: search ONLY for specifically identified missing data.
    """
    print("---INTEGRATE WEB SEARCH (GAP-AWARE TARGETED SEARCH)---")
    messages = state["messages"]
    question = messages[-1].content
    existing_documents = state.get("documents", [])
    
    # NEW: Check if we have targeted gap queries from gap analysis
    targeted_gap_queries = state.get("targeted_gap_queries", [])
    gap_analysis = state.get("gap_analysis", {})
    
    # Get sub-query analysis and results (fallback if no gap analysis)
    sub_query_analysis = state.get("sub_query_analysis", {})
    sub_query_results = state.get("sub_query_results", {})
    companies_detected = sub_query_analysis.get("companies_detected", [])
    
    #  NEW: Use portfolio company filter if available (takes priority over detected companies)
    company_filter = state.get("company_filter", [])
    if company_filter:
        # Portfolio company filter (already scoped to specific companies)
        target_company = company_filter[0] if isinstance(company_filter, list) else company_filter
        print(f" Using portfolio company for web search: {target_company}")
    elif companies_detected:
        target_company = companies_detected[0]
        print(f" Using detected company for web search: {target_company}")
    else:
        target_company = None
        print(f" No company specified for web search (will search generically)")
    
    # PRIORITY 1: Use targeted gap queries (most specific)
    if targeted_gap_queries:
        print(f" USING TARGETED GAP QUERIES FROM GAP ANALYSIS")
        print(f"   Gap Type: {gap_analysis.get('gap_type', 'unknown')}")
        print(f"   Missing Items: {', '.join(gap_analysis.get('missing_items', [])[:3])}")
        print(f"   Targeted Queries: {len(targeted_gap_queries)}")
        
        search_queries_to_execute = targeted_gap_queries
        mode = "gap_analysis"
        
        for i, query in enumerate(targeted_gap_queries, 1):
            print(f"     {i}. {query}")
    
    # PRIORITY 2: Use missing sub-queries (fallback)
    else:
        print(f" USING MISSING SUB-QUERIES (no gap analysis available)")
        
        sub_queries = sub_query_analysis.get("sub_queries", [])
        needs_sub_queries = sub_query_analysis.get("needs_sub_queries", False)
        
        missing_sub_queries = []
        if needs_sub_queries and sub_queries:
            # Find sub-queries with no data or incomplete data
            for sq in sub_queries:
                sq_data = sub_query_results.get(sq, {})
                has_data = sq_data.get("found", False) if isinstance(sq_data, dict) else bool(sq_data)
                
                if not has_data:
                    missing_sub_queries.append(sq)
            
            print(f"   Total sub-queries: {len(sub_queries)}")
            print(f"   Sub-queries with data: {len(sub_queries) - len(missing_sub_queries)}")
            print(f"   MISSING sub-queries: {len(missing_sub_queries)}")
            
            if missing_sub_queries:
                for i, msq in enumerate(missing_sub_queries, 1):
                    print(f"     {i}. {msq}")
                
                # Convert missing sub-queries to search queries (include company name!)
                search_queries_to_execute = []
                for msq in missing_sub_queries:
                    if target_company:
                        # Include company name in search for better targeting
                        search_query = f"{target_company} {msq} financial data"
                        print(f"    Query: {search_query}")
                    else:
                        search_query = f"{msq} financial data"
                    search_queries_to_execute.append(search_query)
                
                mode = "sub_queries"
            else:
                # No missing sub-queries
                print("     No missing sub-queries, using general search")
                search_queries_to_execute = [question]
                mode = "general"
        else:
            # No sub-query mode
            print("   No sub-queries defined, using general search")
            search_queries_to_execute = [question]
            mode = "general"
    
    # Setup web search tool
    web_search_tool = TavilySearch(
        max_results=5, 
        include_raw_content=True,
        include_domains=TRUSTED_FINANCIAL_DOMAINS
    )
    
    web_documents = []
    total_chars = 0
    seen_doc_ids = set()
    updated_sub_query_results = dict(sub_query_results)
    
    # EXECUTE TARGETED SEARCHES
    print(f"\n---EXECUTING {len(search_queries_to_execute)} TARGETED WEB SEARCHES ({mode} mode)---")
    
    for i, search_query in enumerate(search_queries_to_execute, 1):
        print(f"\n   {i}/{len(search_queries_to_execute)}: {search_query[:100]}")
        
        try:
            docs = web_search_tool.invoke({"query": search_query})
            sources = _parse_tavily_response(docs, search_query)
            
            print(f"      Found {len(sources)} sources")
            
            # Create documents from sources
            query_doc_count = 0
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
                            "source": "integrate_web_search",
                            "title": source['title'],
                            "url": source['url'],
                            "search_query": search_query,
                            "search_mode": mode
                        }
                    )
                    web_documents.append(doc)
                    total_chars += len(source['content'])
                    query_doc_count += 1
            
            if query_doc_count > 0:
                print(f"      ✓ Retrieved {query_doc_count} unique documents")
            else:
                print(f"        No unique documents (may be duplicates)")
                
        except Exception as e:
            print(f"        ERROR: {e}")
    
    print(f"\n---WEB SEARCH COMPLETE---")
    print(f"Total unique documents: {len(web_documents)}")
    print(f"Total characters: {total_chars:,}")
    print(f"Mode: {mode}")
    
    # Combine existing and web documents
    combined_documents = existing_documents + web_documents
    
    tool_call_entry = {
        "tool": "integrate_web_search",
        "search_mode": mode,
        "queries_executed": len(search_queries_to_execute),
        "web_docs_retrieved": len(web_documents)
    }
    
    print(f"\n✓ INTEGRATED WEB SEARCH RESULT:")
    print(f"  Existing docs: {len(existing_documents)}")
    print(f"  New web docs: {len(web_documents)} ({total_chars:,} chars)")
    print(f"  Total combined: {len(combined_documents)}")
    
    return {
        "documents": combined_documents,
        "web_searched": True,
        "tool_calls": state.get("tool_calls", []) + [tool_call_entry],
        "sub_query_results": updated_sub_query_results  # Update with web search results
    }


def evaluate_vectorstore_quality(state):
    """
    Evaluate the quality of vectorstore results to determine if web search is needed.
    """
    print("---EVALUATE VECTORSTORE QUALITY---")
    messages = state["messages"]
    question = messages[-1].content
    documents = state.get("documents", [])

    # Simple heuristics to evaluate vectorstore quality
    quality = "none"
    needs_web_fallback = True

    if documents:
        # Check if we have sufficient relevant documents
        if len(documents) >= 2:
            quality = "good"
            needs_web_fallback = False
        elif len(documents) == 1:
            quality = "poor"
            needs_web_fallback = True
        else:
            quality = "none"
            needs_web_fallback = True
    
    tool_call_entry = {
        "tool": "evaluate_vectorstore_quality"
    }

    print(f"VECTORSTORE QUALITY: {quality}, NEEDS WEB FALLBACK: {needs_web_fallback}")
    return {
        "vectorstore_quality": quality,
        "needs_web_fallback": needs_web_fallback,
        "tool_calls": state.get("tool_calls", []) + [tool_call_entry]
    }


def show_result(state):
    print("---SHOW RESULT---")
    Final_answer = AIMessage(content=state["Intermediate_message"])

    tool_call_entry = {
        "tool": "final_output"
    }

    print(f'SHOWING THE RESULTS: {Final_answer}')
    return {
        "messages": Final_answer,
        "tool_calls": state.get("tool_calls", []) + [tool_call_entry]
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
        title = f'Financial Comparison: {company1} vs {company2}'
        if company3:
            title += f" vs {company3}"
        title += " (2024)"
        
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
