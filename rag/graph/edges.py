"his modules has all info about the graph edges"
from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq
from rag.vectordb.chains import (get_question_router_chain,
                                                          get_hallucination_chain, 
                                                          get_answer_quality_chain,
                                                          get_document_sufficiency_chain,
                                                          get_gap_analysis_chain)
from rag.vectordb.client import load_vector_database



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
    
    # Check query type from preprocess analysis
    sub_query_analysis = state.get("sub_query_analysis", {})
    query_type = sub_query_analysis.get("query_type")
    
    # SUMMARIZE queries → directly to generate (use conversation messages)
    if query_type == "summarize":
        print("Smart Reuse: Summarize query → generate (using messages)")
        return "generate"
    
    # MORE_INFO queries → to vectorstore for incremental retrieval
    elif query_type == "more_info":
        print("Smart Reuse: More info query → vectorstore (incremental)")
        return "vectorstore"
    
    # FOLLOW_UP queries → directly to generate (use persisted documents)
    elif query_type == "follow_up":
        print("Smart Reuse: Follow-up query → generate (using docs)")
        return "generate"

    # New query → normal vectorstore retrieval
    print("New query → vectorstore")
    return "vectorstore"
    
def decide_to_generate(state):
    """
    GAP ANALYSIS DECISION: Intelligently determines if we have all needed data.
    
    NEW APPROACH:
    1. Uses financial analyst grading from previous step
    2. Performs gap analysis to identify specific missing data
    3. If gaps exist, generates targeted web search queries for ONLY missing data
    4. If no gaps, proceeds to generate answer
    
    This replaces generic "yes/no" decisions with precise gap identification.
    """
    print("---GAP ANALYSIS DECISION---")
    messages = state["messages"]
    question = messages[-1].content
    filtered_documents = state["documents"]
    vectorstore_searched = state.get("vectorstore_searched", False)
    web_searched = state.get("web_searched", False)
    
    doc_count = len(filtered_documents) if filtered_documents else 0
    print(f"Documents: {doc_count}")
    print(f"Vectorstore searched: {vectorstore_searched}, Web searched: {web_searched}")
    
    # CRITICAL: Prevent infinite loops - if web search already done, must generate
    if web_searched and vectorstore_searched:
        if not filtered_documents:
            print("---DECISION: NO DOCS AFTER BOTH SEARCHES, FALLBACK TO FINANCIAL WEB SEARCH---")
            return "financial_web_search"
        else:
            print(f"---DECISION: BOTH SEARCHES COMPLETE, MUST GENERATE TO AVOID LOOP---")
            return "generate"
    
    # Handle edge case: No documents at all
    if not filtered_documents:
        if not web_searched and vectorstore_searched:
            print("---DECISION: NO DOCUMENTS, TRY WEB SEARCH---")
            return "integrate_web_search"
        else:
            print("---DECISION: NO DOCS AFTER ALL SEARCHES, FALLBACK---")
            return "financial_web_search"
    
    # Get financial analyst grading from previous step
    financial_grading = state.get("financial_grading", {})
    
    if not financial_grading or "overall_grade" not in financial_grading:
        print("  No financial grading found, using fallback logic")
        # Fallback to simple heuristic
        if doc_count >= 3:
            return "generate"
        elif not web_searched:
            return "integrate_web_search"
        else:
            return "generate"
    
    overall_grade = financial_grading.get("overall_grade")
    can_answer = financial_grading.get("can_answer", False)
    missing_data_summary = financial_grading.get("missing_data_summary", "")
    
    print(f"Financial Analyst Grade: {overall_grade}")
    print(f"Can Answer: {can_answer}")
    
    # DECISION LOGIC based on financial analyst grade:
    
    # 1. SUFFICIENT grade → Generate directly
    if overall_grade == "sufficient" and can_answer:
        print("---DECISION: SUFFICIENT DATA, GENERATE ANSWER---")
        return "generate"
    
    # 2. INSUFFICIENT/PARTIAL grade and web search NOT done → Perform gap analysis
    if overall_grade in ["partial", "insufficient"] and not web_searched:
        print(f"\n {overall_grade.upper()} GRADE: Performing gap analysis...")
        
        # Initialize gap analyzer
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        gap_analyzer = get_gap_analysis_chain(llm)
        
        # Prepare document coverage summary
        company_coverage = financial_grading.get("company_coverage", [])
        coverage_summary = []
        for cc in company_coverage:
            coverage_summary.append(
                f"Company: {cc.get('company')} | "
                f"Confidence: {cc.get('confidence')} | "
                f"Metrics Found: {', '.join(cc.get('metrics_found', [])[:3])} | "
                f"Metrics Missing: {', '.join(cc.get('metrics_missing', [])[:3])}"
            )
        coverage_text = "\n".join(coverage_summary) if coverage_summary else "No detailed coverage data"
        
        try:
            # Perform gap analysis
            gap_result = gap_analyzer.invoke({
                "question": question,
                "analyst_grade": str(financial_grading),
                "doc_coverage_summary": coverage_text
            })
            
            print(f"\n GAP ANALYSIS RESULT:")
            print(f"  Has Gaps: {gap_result.has_gaps}")
            print(f"  Gap Type: {gap_result.gap_type}")
            
            if gap_result.has_gaps and gap_result.missing_items:
                print(f"  Missing Items: {', '.join(gap_result.missing_items[:5])}")
                print(f"  Targeted Queries Generated: {len(gap_result.targeted_queries)}")
                for i, query in enumerate(gap_result.targeted_queries[:3], 1):
                    print(f"    {i}. {query}")
                print(f"  Reasoning: {gap_result.reasoning}")
                
                # Store targeted queries in state for web search node
                state["targeted_gap_queries"] = gap_result.targeted_queries
                state["gap_analysis"] = gap_result.dict()
                
                print(f"\n---DECISION: GAPS IDENTIFIED, WEB SEARCH FOR MISSING DATA---")
                return "integrate_web_search"
            
            else:
                print(f"  No significant gaps found")
                print(f"  Reasoning: {gap_result.reasoning}")
                print(f"\n---DECISION: NO MAJOR GAPS, GENERATE WITH AVAILABLE DATA---")
                return "generate"
        
        except Exception as e:
            print(f"  Gap analysis failed: {e}")
            if missing_data_summary:
                print(f"  Using missing data summary: {missing_data_summary[:200]}")
                print(f"---DECISION: FALLBACK TO WEB SEARCH---")
                return "integrate_web_search"
            else:
                print(f"---DECISION: FALLBACK TO GENERATE---")
                return "generate"
    
    # 3. PARTIAL grade but web search already done → Generate with what we have
    if overall_grade == "partial" and web_searched:
        print("---DECISION: PARTIAL DATA + WEB SEARCH DONE, GENERATE WITH AVAILABLE---")
        return "generate"
    
    # 4. INSUFFICIENT grade and web search already done → Try financial web search as last resort
    if overall_grade == "insufficient" and web_searched:
        print("---DECISION: STILL INSUFFICIENT AFTER WEB SEARCH, FINANCIAL WEB SEARCH---")
        return "financial_web_search"
    
    # 5. Default fallback
    print(f"---DECISION: DEFAULT FALLBACK (grade={overall_grade}, web_searched={web_searched})---")
    if doc_count >= 2:
        return "generate"
    elif not web_searched:
        return "integrate_web_search"
    else:
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
