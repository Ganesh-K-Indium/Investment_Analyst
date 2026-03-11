"his modules has all info about the graph edges"
from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq
from rag.vectordb.client import load_vector_database


def route_alpha_workflow(state):
    """
    Route to ALPHA workflow, Scenario workflow, or normal RAG.

    Priority:
        1. alpha_mode  → "alpha"   (buy-timing ALPHA Framework)
        2. scenario_mode → "scenario" (Bull/Bear/Base scenario analysis)
        3. else        → "normal"  (standard RAG pipeline)

    Returns:
        str: "alpha" | "scenario" | "normal"
    """
    alpha_mode = state.get("alpha_mode", False)
    scenario_mode = state.get("scenario_mode", False)

    if alpha_mode:
        print(" Routing to ALPHA Framework workflow")
        return "alpha"
    elif scenario_mode:
        print(" Routing to Scenario (Bull/Bear/Base) workflow")
        return "scenario"
    else:
        print(" Routing to normal RAG workflow")
        return "normal"



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
    print("---ROUTE QUESTION---")
    messages = state["messages"]
    question = messages[-1].content
    
    # Check if companies detected (from preprocess analysis)
    companies_detected = state.get("companies_detected", [])
    
    if companies_detected:
        print(f"Company query detected: {companies_detected} → vectorstore")
        return "vectorstore"
    
    # Check for explicit real-time requests (only case for web_search)
    realtime_keywords = [
        "current stock price", "today's price", "right now", "live price",
        "current market price", "stock price today", "this morning", "today's news"
    ]
    
    question_lower = question.lower()
    if any(keyword in question_lower for keyword in realtime_keywords): 
        print("Real-time data request → web_search")
        return "web_search"
    
    # Default: go to vectorstore (let retrieval and grading decide quality)
    print("Default → vectorstore")
    return "vectorstore"
    
def decide_to_generate(state):
    """
    ROUTING DECISION: Route after grading to either generate or web search.

    Flow:
      grade_documents → decide_to_generate
        ├─ "generate"              (sufficient grade, or web search already done)
        └─ "integrate_web_search"  (partial/insufficient and web search not yet done)
    """
    print("---DECIDE TO GENERATE---")
    filtered_documents = state["documents"]
    web_searched = state.get("web_searched", False)

    doc_count = len(filtered_documents) if filtered_documents else 0
    print(f"Chunks: {doc_count}, Web searched: {web_searched}")

    # Web search already done → generate with whatever we have
    if web_searched:
        print("---DECISION: WEB SEARCH DONE, GENERATE---")
        return "generate"

    # No documents → go get them
    if not filtered_documents:
        print("---DECISION: NO DOCUMENTS, INTEGRATE WEB SEARCH---")
        return "integrate_web_search"

    financial_grading = state.get("financial_grading", {})

    if not financial_grading or "overall_grade" not in financial_grading:
        print("  No financial grading found, generating with available docs")
        return "generate" if doc_count >= 3 else "integrate_web_search"

    overall_grade = financial_grading.get("overall_grade")
    can_answer = financial_grading.get("can_answer", False)
    print(f"Grade: {overall_grade} | Can Answer: {can_answer}")

    if overall_grade == "sufficient" and can_answer:
        print("---DECISION: SUFFICIENT, GENERATE---")
        return "generate"

    if overall_grade in ["partial", "insufficient"]:
        print(f"---DECISION: {overall_grade.upper()}, INTEGRATE WEB SEARCH---")
        return "integrate_web_search"

    return "generate" if doc_count >= 2 else "integrate_web_search"



# REMOVED: decide_after_web_integration — integrate_web_search now goes directly to generate.
# REMOVED: decide_after_gap_analysis — gap_analysis node removed; routing goes straight to integrate_web_search.


def _is_direct_vectordb_mode(state) -> bool:
    """
    Check if the current query should skip grading and web search,
    going directly from retrieve → generate → show_result.

    Applies to: comparison mode, segment queries, geographic queries.
    These query types use predefined sub-queries and the 10-K is the
    authoritative source — web search only adds noise.
    """
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
        print(f"---{query_type.upper()} MODE: SKIPPING GRADING, DIRECT TO GENERATE---")
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
    print("---DECIDE CHART GENERATION---")
    is_comparison_mode = state.get("is_comparison_mode", False)
    
    if is_comparison_mode:
        print("---DECISION: COMPARISON MODE ENABLED, GENERATE CHART---")
        return "generate_chart"
    else:
        print("---DECISION: NOT COMPARISON MODE, SKIP CHART---")
        return "show_result"
