from typing import List, Annotated, Sequence,Dict, Any, Optional
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

class GraphState(TypedDict):
    """
    Represents the state of our graph.

    Attributes:
        question: question
        generation: LLM generation
        documents: list of documents
        vectorstore_searched: whether vectorstore has been searched
        web_searched: whether web search has been conducted
        vectorstore_quality: quality score of vectorstore results
        needs_web_fallback: whether web search is needed as fallback
        document_sources: categorized document sources for citation
        citation_info: citation information for all sources
        summary_strategy: strategy for document summarization
        sub_query_analysis: universal sub-query analysis (companies, type, sub-queries)
        sub_query_results: tracking individual sub-query retrieval results
        is_comparison_mode: whether this is a company comparison request
        comparison_company1: first company name for comparison
        comparison_company2: second company name for comparison
        comparison_company3: third company name for comparison (optional, for 3-way)
        chart_url: Cloudinary URL of generated comparison chart
        chart_filename: filename of generated chart
        context_strategy: strategy for context selection (messages/documents/incremental)
        conversation_messages: extracted AI messages for summarization
        clarification_needed: whether to interrupt for user clarification
        clarification_request: generated question to ask user
        user_clarification: user's response to clarification question
        clarified_intent: LLM-parsed intent from user's clarification
        retrieval_constraints: specific filters/constraints extracted from clarification
        vectordb_instance: pre-initialized vector database instance (portfolio-scoped)
        company_filter: list of companies this DB instance is filtered for
    """
    messages: Annotated[Sequence[BaseMessage], add_messages]
    Intermediate_message: str
    documents: List[str]
    retry_count: int
    tool_calls: List[Dict[str, Any]]
    vectorstore_searched: bool
    web_searched: bool
    vectorstore_quality: str  # "good", "poor", "none"
    needs_web_fallback: bool
    document_sources: Dict[str, List[Any]]  # categorized by source type
    citation_info: List[Dict[str, Any]]
    summary_strategy: str
    companies_detected: List[str]  # Cached company extraction (extracted once, reused)
    company_name: str  # User-provided company name for filtering (DEPRECATED - use company_filter)
    sub_query_analysis: Dict[str, Any]  # Universal sub-query analysis (replaces financial_calculation)
    sub_query_results: Dict[str, Any]  # Results from individual sub-query retrievals
    is_comparison_mode: bool  # Whether this is a company comparison request
    comparison_company1: str  # First company name for comparison
    comparison_company2: str  # Second company name for comparison
    comparison_company3: str  # Third company name for comparison (optional, for 3-way)
    chart_url: str  # Cloudinary URL of generated comparison chart
    chart_filename: str  # Filename of generated chart
    context_strategy: str  # Strategy for context selection: "messages" | "documents" | "incremental"
    conversation_messages: List[str]  # Extracted AI messages for summarization
    clarification_needed: bool  # Whether to interrupt for clarification
    clarification_request: str  # Question to ask user
    user_clarification: str  # User's response to clarification
    clarified_intent: Dict[str, Any]  # LLM-parsed intent from clarification
    retrieval_constraints: Dict[str, Any]  # Specific filters/constraints from user
    #vectordb_instance: Any  # REMOVED: Managed via VectorDBManager singleton
    company_filter: List[str]  # List of companies this DB instance is filtered for
    ticker: Optional[str]  # Ticker symbol for collection selection
    