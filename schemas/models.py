"This module is useful for pydantic classes"
from typing import Literal
from pydantic import BaseModel, Field

class RouteQuery(BaseModel):
    """Route a user query to the most relevant datasource."""

    datasource: Literal["vectorstore", "web_search", "general"] = Field(
        description="Given a user question choose to route it to web search, a vectorstore, or general LLM (for non-financial questions).",
    )
    
class ExtractCompany(BaseModel):
    """Route a user query to the most relevant datasource."""

    company: str = Field(
        description="Given a user question extract the company name from the question.",
    )
    
class GradeDocuments(BaseModel):
    """Binary score for relevance check on retrieved documents."""

    binary_score: str = Field(
        description="Documents are relevant to the question, 'yes' or 'no'"
    )
    
class GradeHallucinations(BaseModel):
    """Binary score for hallucination present in generation answer."""
    binary_score: str = Field(
        description="Answer is grounded in the facts, 'yes' or 'no'"
    )
    reasoning: str = Field(
        description="Brief explanation of why the answer is or isn't grounded"
    )
    
class GradeAnswer(BaseModel):
    """Binary score to assess answer addresses question."""

    binary_score: str = Field(
        description="Answer addresses the question, 'yes' or 'no'"
    )

class DocumentSummaryStrategy(BaseModel):
    """Strategy for summarizing documents based on cross-reference analysis."""
    
    strategy: Literal["single_source", "multi_source_vectorstore", "integrated_web_vectorstore"] = Field(
        description="Strategy for document summarization"
    )
    primary_sources: list[str] = Field(
        description="Primary document sources to use"
    )
    supplementary_sources: list[str] = Field(
        description="Supplementary sources if needed"
    )

class CitationInfo(BaseModel):
    """Information for document citation."""
    
    source_type: Literal["vectorstore_text", "vectorstore_image", "web_search", "financial_web"] = Field(
        description="Type of source being cited"
    )
    document_id: str = Field(
        description="Identifier for the document"
    )
    relevance_score: float = Field(
        description="Relevance score of the document to the query"
    )
    key_information: str = Field(
        description="Key information extracted from this source"
    )

class MultiCompanyExtraction(BaseModel):
    """Extract multiple companies from a question for cross-referencing."""
    
    companies: list[str] = Field(
        description="List of company names found in the question"
    )
    primary_company: str = Field(
        description="The primary/main company being discussed"
    )
    is_comparison: bool = Field(
        description="Whether the question involves comparison between companies"
    )

class FinancialCalculationAnalysis(BaseModel):
    """Analysis of whether query needs financial calculations and what data is required."""
    
    needs_calculation: bool = Field(
        description="Whether the query requires financial metric calculations"
    )
    metrics_needed: list[str] = Field(
        description="List of financial metrics to calculate (e.g., 'ROE', 'Revenue Growth', 'P/E Ratio')"
    )
    sub_queries: list[str] = Field(
        description="List of specific data points needed from documents (e.g., 'Net Income 2023', 'Total Equity 2023')"
    )
    reasoning: str = Field(
        description="Brief explanation of what calculations are needed and why"
    )

class DocumentSufficiencyDecision(BaseModel):
    """LLM-powered decision on whether documents are sufficient or need web search supplement."""
    
    decision: Literal["generate", "integrate_web_search", "financial_web_search"] = Field(
        description="Decision on next action: 'generate' if docs are sufficient, 'integrate_web_search' to supplement with web, 'financial_web_search' as fallback"
    )
    reasoning: str = Field(
        description="Clear explanation of why this decision was made"
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="Confidence level in the decision"
    )

class UniversalSubQueryAnalysis(BaseModel):
    """Universal sub-query analysis for any query type - acts as financial analyst to intelligently decompose queries."""
    
    needs_sub_queries: bool = Field(
        description="Whether this query needs to be decomposed into sub-queries for better data retrieval"
    )
    query_type: Literal["single_company", "multi_company", "financial_calculation", "general", "temporal_comparison"] = Field(
        description="Type of query being analyzed"
    )
    companies_detected: list[str] = Field(
        description="All company names mentioned in the query (empty list if none)"
    )
    sub_queries: list[str] = Field(
        description="List of focused sub-queries to retrieve specific data points. Each should be a standalone search query."
    )
    reasoning: str = Field(
        description="Brief explanation of why these sub-queries are needed and how they help answer the main question"
    )

class FinancialMetricPresence(BaseModel):
    """Tracks which financial metrics are present vs missing in documents for a specific company."""
    
    company: str = Field(description="Company name")
    metrics_found: list[str] = Field(description="Financial metrics found in documents (e.g., 'revenue 2023', 'net income', 'total assets')")
    metrics_missing: list[str] = Field(description="Financial metrics needed but missing (e.g., 'debt-to-equity', 'current ratio')")
    year_coverage: list[str] = Field(description="Years covered in documents (e.g., ['2023', '2024'])")
    confidence: Literal["high", "medium", "low"] = Field(description="Confidence in document coverage for this company")

class FinancialAnalystGrade(BaseModel):
    """Financial analyst evaluation of document quality and completeness."""
    
    overall_grade: Literal["sufficient", "partial", "insufficient"] = Field(
        description="Overall assessment: sufficient (can answer), partial (some data missing), insufficient (cannot answer)"
    )
    company_coverage: list[FinancialMetricPresence] = Field(
        description="Per-company breakdown of metric coverage"
    )
    can_answer_question: bool = Field(
        description="Whether retrieved docs contain enough to answer the question"
    )
    missing_data_summary: str = Field(
        description="Summary of what critical data is missing (empty if sufficient)"
    )
    reasoning: str = Field(
        description="Financial analyst reasoning about document quality"
    )

class GapAnalysisResult(BaseModel):
    """Identifies specific data gaps and generates targeted queries to fill them."""
    
    has_gaps: bool = Field(description="Whether there are data gaps that need to be filled")
    gap_type: Literal["missing_company", "missing_metric", "missing_year", "no_gaps"] = Field(
        description="Type of data gap identified"
    )
    missing_items: list[str] = Field(
        description="Specific missing items (e.g., ['Microsoft revenue 2023', 'Apple debt-to-equity'])"
    )
    targeted_queries: list[str] = Field(
        description="Specific web search queries to retrieve ONLY missing data (e.g., 'Microsoft 2023 revenue 10-K')"
    )
    reasoning: str = Field(
        description="Explanation of what's missing and why these queries will help"
    )

class StructuredFinancialData(BaseModel):
    """Structured extraction of financial data instead of lossy truncation."""
    
    company: str = Field(description="Company name")
    year: str = Field(description="Fiscal year or period")
    
    # Income Statement
    revenue: str | None = Field(default=None, description="Total revenue")
    net_income: str | None = Field(default=None, description="Net income/loss")
    operating_income: str | None = Field(default=None, description="Operating income")
    gross_profit: str | None = Field(default=None, description="Gross profit")
    earnings_per_share: str | None = Field(default=None, description="Earnings per share (EPS)")
    total_assets: str | None = Field(default=None, description="Total assets")
    total_liabilities: str | None = Field(default=None, description="Total liabilities")
    shareholders_equity: str | None = Field(default=None, description="shareholders' equity")
    cash_flow_operations: str | None = Field(default=None, description="Cash flow from operations")
    free_cash_flow: str | None = Field(default=None, description="Free cash flow")
    other_metrics: dict[str, str] = Field(default_factory=dict, description="Other extracted metrics")


class AlphaDimensionOutput(BaseModel):
    """Output for a single ALPHA Framework dimension analysis"""
    analysis: str = Field(
        description="Analysis summary for this dimension (max 100 words)"
    )
    key_points: list[str] = Field(
        default_factory=list,
        description="Key bullet points from the analysis (3-5 points)"
    )