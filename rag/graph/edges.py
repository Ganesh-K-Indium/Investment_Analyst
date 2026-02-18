"his modules has all info about the graph edges"
from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq
from rag.vectordb.chains import (get_question_router_chain,
                                                          get_hallucination_chain,
                                                          get_answer_quality_chain,
                                                          get_document_sufficiency_chain)
from rag.vectordb.client import load_vector_database


def route_alpha_workflow(state):
    """
    Route to ALPHA workflow if alpha_mode is enabled.
    
    Returns:
        str: "alpha" if alpha_mode is True, else "normal"
    """
    alpha_mode = state.get("alpha_mode", False)
    
    if alpha_mode:
        print(" Routing to ALPHA Framework workflow")
        return "alpha"
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
    ROUTING DECISION: Determines whether to generate, perform gap analysis, or web search.

    Gap analysis itself has been moved to a dedicated 'gap_analysis' node so that
    the targeted queries it produces are properly persisted in graph state.

    Flow:
      grade_documents → decide_to_generate
        ├─ "generate"          (sufficient grade, or web search already done)
        ├─ "gap_analysis"      (partial/insufficient, web search not yet done)
        └─ "financial_web_search" (no docs after all searches)
    """
    print("---DECIDE TO GENERATE---")
    filtered_documents = state["documents"]
    vectorstore_searched = state.get("vectorstore_searched", False)
    web_searched = state.get("web_searched", False)

    doc_count = len(filtered_documents) if filtered_documents else 0
    print(f"Documents: {doc_count}")
    print(f"Vectorstore searched: {vectorstore_searched}, Web searched: {web_searched}")

    # CRITICAL: Prevent infinite loops - if both searches done, must generate
    if web_searched and vectorstore_searched:
        if not filtered_documents:
            print("---DECISION: NO DOCS AFTER BOTH SEARCHES, FALLBACK TO FINANCIAL WEB SEARCH---")
            return "financial_web_search"
        print("---DECISION: BOTH SEARCHES COMPLETE, GENERATE TO AVOID LOOP---")
        return "generate"

    # No documents at all
    if not filtered_documents:
        if not web_searched and vectorstore_searched:
            print("---DECISION: NO DOCUMENTS, PERFORM GAP ANALYSIS THEN WEB SEARCH---")
            return "gap_analysis"
        print("---DECISION: NO DOCS AFTER ALL SEARCHES, FALLBACK---")
        return "financial_web_search"

    financial_grading = state.get("financial_grading", {})

    if not financial_grading or "overall_grade" not in financial_grading:
        print("  No financial grading found, using fallback logic")
        if doc_count >= 3:
            return "generate"
        elif not web_searched:
            return "gap_analysis"
        return "generate"

    overall_grade = financial_grading.get("overall_grade")
    can_answer = financial_grading.get("can_answer", False)

    print(f"Financial Analyst Grade: {overall_grade}")
    print(f"Can Answer: {can_answer}")

    # 1. SUFFICIENT grade → Generate directly
    if overall_grade == "sufficient" and can_answer:
        print("---DECISION: SUFFICIENT DATA, GENERATE ANSWER---")
        return "generate"

    # 2. PARTIAL/INSUFFICIENT and web search NOT done → Gap analysis node
    if overall_grade in ["partial", "insufficient"] and not web_searched:
        print(f"---DECISION: {overall_grade.upper()} GRADE, RUNNING GAP ANALYSIS---")
        return "gap_analysis"

    # 3. PARTIAL + web search done → Generate with available data
    if overall_grade == "partial" and web_searched:
        print("---DECISION: PARTIAL DATA + WEB SEARCH DONE, GENERATE WITH AVAILABLE---")
        return "generate"

    # 4. INSUFFICIENT + web search done → Financial web search as last resort
    if overall_grade == "insufficient" and web_searched:
        print("---DECISION: STILL INSUFFICIENT AFTER WEB SEARCH, FINANCIAL WEB SEARCH---")
        return "financial_web_search"

    # 5. Default fallback
    print(f"---DECISION: DEFAULT FALLBACK (grade={overall_grade}, web_searched={web_searched})---")
    if doc_count >= 2:
        return "generate"
    elif not web_searched:
        return "gap_analysis"
    return "generate"

    
def grade_generation_v_documents_and_question(state):
    """
    Determines whether the generation is grounded in the document and answers question.
    Enhanced for cross-referencing scenarios.

    Args:
        state (dict): The current graph state

    Returns:
        str: Decision for next node to call
    """

    print("---CHECK HALLUCINATIONS---")
    messages = state["messages"]
    question = messages[-1].content
    
    documents = state["documents"]
    Intermediate_message = state["Intermediate_message"]

    #  Track retry count in state
    retry_count = state.get("retry_count", 0)
    max_retries = 2  # or 3, depending on how strict you want to be

    # NEW: Structured financial data extraction for hallucination checking
    from rag.graph.nodes import smart_extract_financial_data
    
    total_chars = sum(len(doc.page_content) if hasattr(doc, 'page_content') else len(str(doc)) for doc in documents)
    MAX_HALLUCINATION_CHARS = 80000  # Smaller limit for hallucination checking
    
    # NEW: Use structured extraction instead of truncation
    if total_chars > MAX_HALLUCINATION_CHARS:
        print(f"[EXTRACT] Hallucination check: {total_chars:,} -> {MAX_HALLUCINATION_CHARS:,} chars")
        documents = smart_extract_financial_data(documents, MAX_HALLUCINATION_CHARS)
        print(f"[EXTRACT] ✓ Structured extraction complete - all metrics preserved")
    else:
        print(f"[DOC SIZE] Hallucination check: {total_chars:,} chars (within limit)")

    llm = ChatOpenAI(model="gpt-4o")
    hallucination_grader = get_hallucination_chain(llm)
    
    # Log what we're checking
    print(f" Grading against {len(documents)} document(s)")
    print(f" Generation length: {len(Intermediate_message)} chars")
    print(f" Generation preview: {Intermediate_message[:200]}...")
    
    # Grade against documents only (context-free)
    score = hallucination_grader.invoke({
        "documents": documents, 
        "generation": Intermediate_message
    })
    grade = score.binary_score
    reasoning = getattr(score, 'reasoning', 'No reasoning provided')
    
    print(f" Hallucination Grader Decision: {grade}")
    print(f" Reasoning: {reasoning}")

    # Check hallucination
    if grade.lower() == "yes":
        print("---DECISION: GENERATION IS GROUNDED IN DOCUMENTS---")
        # Check question-answering
        print("---GRADE GENERATION vs QUESTION---")
        print(f"Question: {question}, Answer {Intermediate_message}")
        answer_grader = get_answer_quality_chain(llm)
        answer_score = answer_grader.invoke(
            {"question": question, "generation": Intermediate_message}
        )
        answer_grade = answer_score.binary_score
        if answer_grade.lower() == "yes":
            print("---DECISION: GENERATION ADDRESSES QUESTION---")
            return "useful"
        else:
            print("---DECISION: GENERATION DOES NOT ADDRESS QUESTION---")
            return "not useful"
    else:
        # Retry logic
        if retry_count >= max_retries:
            print(f"---MAX RETRIES REACHED ({retry_count}), stopping loop---")
            return "useful"  # fallback → treat as final result instead of looping forever
        else:
            print(f"---DECISION: GENERATION IS NOT GROUNDED, RETRY {retry_count + 1}/{max_retries}---")
            # Increment retry count in state
            state["retry_count"] = retry_count + 1
            return "not supported"


def decide_after_web_integration(state):
    """
    Decides next step after web search integration.
    
    Args:
        state (dict): The current graph state
        
    Returns:
        str: Next node to call
    """
    print("---DECIDE AFTER WEB INTEGRATION---")
    documents = state.get("documents", [])
    
    if documents:
        print("---DECISION: DOCUMENTS AVAILABLE AFTER WEB INTEGRATION, GRADE THEM---")
        return "grade_documents"
    else:
        print("---DECISION: NO DOCUMENTS AFTER WEB INTEGRATION, FALLBACK TO FINANCIAL WEB SEARCH---")
        return "financial_web_search"


# REMOVED: decide_cross_reference_approach and decide_after_cross_reference_analysis
# Cross-reference analysis now happens BEFORE retrieval (after routing), not after grading.
# This optimization reduces overhead by analyzing needs upfront.


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


def route_after_generate(state):
    """
    Route after generation: skip hallucination/answer grading for direct-vectordb modes.

    Direct modes (compare/segment/geographic): generate → decide_chart
    Normal mode: generate → grade_generation (existing flow)
    """
    if _is_direct_vectordb_mode(state):
        query_type = state.get("sub_query_analysis", {}).get("query_type", "comparison")
        print(f"---{query_type.upper()} MODE: SKIPPING GENERATION GRADING, DIRECT TO CHART DECISION---")
        return "decide_chart"
    else:
        return "grade_generation"


def decide_after_gap_analysis(state):
    """
    Routes after the gap_analysis node.

    - If targeted queries were generated → integrate_web_search (fetch missing components)
    - If no actionable gaps → generate (proceed with available data)
    """
    print("---DECIDE AFTER GAP ANALYSIS---")
    targeted_gap_queries = state.get("targeted_gap_queries", [])
    gap_analysis = state.get("gap_analysis", {})

    if targeted_gap_queries:
        print(f"---DECISION: {len(targeted_gap_queries)} TARGETED QUERIES, RUNNING WEB SEARCH---")
        for i, q in enumerate(targeted_gap_queries[:3], 1):
            print(f"  {i}. {q}")
        return "integrate_web_search"
    else:
        has_gaps = gap_analysis.get("has_gaps", False)
        reasoning = gap_analysis.get("reasoning", "")
        print(f"---DECISION: NO TARGETED QUERIES (has_gaps={has_gaps}), GENERATE WITH AVAILABLE DATA---")
        if reasoning:
            print(f"  Reasoning: {reasoning[:200]}")
        return "generate"


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
