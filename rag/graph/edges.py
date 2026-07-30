"his modules has all info about the graph edges"
import logging
from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq
from rag.vectordb.client import load_vector_database

logger = logging.getLogger("rag.graph.edges")


def route_alpha_workflow(state):
    """
    Route to ALPHA workflow, Scenario workflow, Macro workflow, or normal RAG.

    Priority:
        1. alpha_mode  → "alpha"   (buy-timing ALPHA Framework)
        2. scenario_mode → "scenario" (Bull/Bear/Base scenario analysis)
        3. macro_mode → "macro" (Macroeconomic Data)
        4. else        → "normal"  (standard RAG pipeline)

    Returns:
        str: "alpha" | "scenario" | "macro" | "normal"
    """
    alpha_mode = state.get("alpha_mode", False)
    scenario_mode = state.get("scenario_mode", False)
    macro_mode = state.get("macro_mode", False)

    if alpha_mode:
        logger.info(" Routing to ALPHA Framework workflow")
        return "alpha"
    elif scenario_mode:
        logger.info(" Routing to Scenario (Bull/Bear/Base) workflow")
        return "scenario"
    elif macro_mode:
        logger.info(" Routing to Macro Data workflow")
        return "macro"
    else:
        logger.info(" Routing to normal RAG workflow")
        return "normal"


def route_after_alpha_retrieve(state):
    """
    Route after alpha retrieve.
    - insider_trading: response pre-built in Intermediate_message → show_result directly
    - other single pillars: standard generate node
    - full ALPHA (no pillar): alpha_generate
    """
    pillar = state.get("alpha_pillar")
    if pillar == "insider_trading":
        logger.info("---INSIDER TRADING: BYPASSING GENERATE → SHOW RESULT---")
        return "show_result"
    if pillar:
        logger.info(f"---SINGLE PILLAR ({pillar}): DIRECT TO GENERATE---")
        return "generate"
    return "alpha_generate"

def route_question(state):
    """
    SIMPLIFIED FAST ROUTING: Route questions with minimal overhead.
    Context-free routing - no memory checks.
    
    Strategy:
    - Company queries → vectorstore (let retrieval handle it)
    - Real-time requests → web_search
    - Everything else → vectorstore (safe default)
    
    The heavy lifting (verification, scoring, fallback) happens in the retrieve node.
    """
    logger.info("---ROUTE QUESTION---")
    messages = state["messages"]
    question = messages[-1].content
    
    # Check if companies detected (from preprocess analysis)
    companies_detected = state.get("companies_detected", [])
    
    if companies_detected:
        logger.info(f"Company query detected: {companies_detected} → vectorstore")
        return "vectorstore"
    
    # Check for explicit real-time requests (only case for web_search)
    realtime_keywords = [
        "current stock price", "today's price", "right now", "live price",
        "current market price", "stock price today", "this morning", "today's news"
    ]
    
    question_lower = question.lower()
    if any(keyword in question_lower for keyword in realtime_keywords): 
        logger.info("Real-time data request → web_search")
        return "web_search"
    
    # Default: go to vectorstore (let retrieval and grading decide quality)
    logger.info("Default → vectorstore")
    return "vectorstore"
    
def decide_to_generate(state):
    """
    ROUTING DECISION: Route after grading to either generate or web search.

    Flow:
      grade_documents → decide_to_generate
        ├─ "generate"              (sufficient grade, web search done, or qdrant_error)
        └─ "integrate_web_search"  (partial/insufficient and web search not yet done)
    """
    logger.info("---DECIDE TO GENERATE---")

    # Qdrant connection error — skip web search and go straight to generate
    # (generate will return the user-facing error message)
    if state.get("qdrant_error"):
        logger.error("---DECISION: QDRANT ERROR, ROUTE TO GENERATE FOR USER-FACING MESSAGE---")
        return "generate"

    filtered_documents = state["documents"]
    web_searched = state.get("web_searched", False)

    doc_count = len(filtered_documents) if filtered_documents else 0
    logger.info(f"Chunks: {doc_count}, Web searched: {web_searched}")

    # Web search already done → generate with whatever we have
    if web_searched:
        logger.info("---DECISION: WEB SEARCH DONE, GENERATE---")
        return "generate"

    # No documents → go get them
    if not filtered_documents:
        logger.info("---DECISION: NO DOCUMENTS, INTEGRATE WEB SEARCH---")
        return "integrate_web_search"

    financial_grading = state.get("financial_grading", {})

    if not financial_grading or "overall_grade" not in financial_grading:
        logger.info("  No financial grading found, generating with available docs")
        return "generate" if doc_count >= 3 else "integrate_web_search"

    overall_grade = financial_grading.get("overall_grade")
    can_answer = financial_grading.get("can_answer", False)
    logger.info(f"Grade: {overall_grade} | Can Answer: {can_answer}")

    if overall_grade == "sufficient" and can_answer:
        logger.info("---DECISION: SUFFICIENT, GENERATE---")
        return "generate"

    if overall_grade in ["partial", "insufficient"]:
        logger.info(f"---DECISION: {overall_grade.upper()}, INTEGRATE WEB SEARCH---")
        return "integrate_web_search"

    return "generate" if doc_count >= 2 else "integrate_web_search"


def _is_direct_vectordb_mode(state) -> bool:
    """
    Check if the current query should skip grading and web search,
    going directly from retrieve → generate → show_result.

    Applies to: comparison mode, segment queries, geographic queries — but
    ONLY when the resolved filing_types is empty (unresolved), or contains
    only "10-K". The "10-K is the authoritative source, web search only adds
    noise" rationale does not hold for 10-Q/8-K data: those are thinner, less
    comprehensive per-document, so bypassing the grading/web-fallback safety
    net for them risks silently generating from weak retrieval with nothing
    to catch it. When filing_types includes 10-Q or 8-K (alone or combined
    with 10-K), these query types fall through to the normal
    grade_documents → web-fallback path instead.
    """
    filing_types = state.get("filing_types") or []
    if any(ft != "10-K" for ft in filing_types):
        return False
    if state.get("is_comparison_mode", False):
        return True
    query_type = state.get("sub_query_analysis", {}).get("query_type", "")
    return query_type in ("segment", "geographic")


def route_after_retrieve(state):
    """
    Route after retrieval: skip grading for direct-vectordb modes.

    Direct modes (compare/segment/geographic): retrieve → generate
    Normal mode: retrieve → grade_documents (existing flow)
    """
    if _is_direct_vectordb_mode(state):
        query_type = state.get("sub_query_analysis", {}).get("query_type", "comparison")
        logger.info(f"---{query_type.upper()} MODE: SKIPPING GRADING, DIRECT TO GENERATE---")
        return "generate"
    else:
        return "grade_documents"


def decide_chart_generation(state):
    """
    Decides whether to generate a comparison chart after generation.
    Only generates chart if this is a comparison mode request.
    
    Args:
        state (dict): The current graph state
        
    Returns:
        str: Next node to call
    """
    logger.info("---DECIDE CHART GENERATION---")
    is_comparison_mode = state.get("is_comparison_mode", False)
    
    if is_comparison_mode:
        logger.info("---DECISION: COMPARISON MODE ENABLED, GENERATE CHART---")
        return "generate_chart"
    else:
        logger.info("---DECISION: NOT COMPARISON MODE, SKIP CHART---")
        return "show_result"
