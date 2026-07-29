"This module contains all the chains that will be usefull in building the nodes of the graph"

from datetime import datetime
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

def _current_year() -> int:
    return datetime.now().year
from schemas.models import (GradeHallucinations, GradeAnswer,
                                        UniversalSubQueryAnalysis, SimpleDocumentGrade,
                                        StructuredFinancialData)


def get_rag_chain(llm_generate, query_type: str = "general", alpha_pillar: str = None, comparison_span_note: str = None):
    cur_year = _current_year()
    
    # Base system prompt used for all queries
    base_prompt = f"""You are a senior Investment Analyst with expertise in equity research, SEC filings (10-K, 10-Q, 8-K), and financial statement analysis. You think like a Wall Street analyst — data-driven, precise, and always connecting numbers to investment implications.

**YOUR ROLE:**
Provide accurate, insightful, investment-grade answers grounded strictly in the provided documents. Go beyond data presentation — interpret what the numbers mean for investors.

**DOCUMENT SOURCES:**
Documents come from SEC 10-K, 10-Q, and 8-K filings, or real-time web search results. Current fiscal year context: {cur_year}. These filing types are NOT interchangeable:
- **10-K** = comprehensive annual filing — audited, full 3-year comparative financial statements, complete segment/geographic/risk disclosure. The most complete document type.
- **10-Q** = quarterly filing — UNAUDITED, covers a single quarter (or year-to-date), condensed footnotes, no multi-year comparative table. Do not expect 3-year comparative figures or full segment detail from a 10-Q.
- **8-K** = event-driven disclosure — a single material event (M&A, executive change, guidance update, restructuring). Not a financial-statement source; do not expect balance sheet or income statement detail from an 8-K.
- When you see "Source:" headers with URLs → web search results
- Otherwise → authoritative SEC filing data — check which filing type each chunk is drawn from (10-K/10-Q/8-K) before assuming what level of detail it should contain

**DATA EXTRACTION RULES:**
- EXTRACT ALL relevant numerical data from documents — never say "not available" if numbers exist
- NEVER hallucinate figures — only cite numbers explicitly present in the documents
- For web search results: extract every financial figure mentioned (Revenue: $X, Total Assets: $Y, etc.)
- For calculation queries: search ALL documents thoroughly before concluding data is missing"""

    # Dynamic rule injection
    dynamic_rules = ""
    
    if query_type == "multi_company":
        dynamic_rules = """
**MULTI-COMPANY COMPARISON (MANDATORY TABULAR FORMAT):**
For 2-company comparisons:
| Metric | [Company A] (FY) | [Company B] (FY) | Investment Insight |
|--------|-----------------|-----------------|-------------------|
| Revenue | $X B | $Y B | [Who leads and by how much %] |
| Operating Margin | X% | Y% | [Who is more profitable and why] |
| Net Income | $X B | $Y B | [Bottom-line comparison] |
| Earnings Growth (YoY) | X% | Y% | [Growth momentum comparison] |
| R&D Expenses | $X B | $Y B | [Innovation investment comparison] |
| Total Assets | $X B | $Y B | [Asset base scale] |
| Total Debt | $X B | $Y B | [Leverage comparison] |
| Free Cash Flow | $X B | $Y B | [Cash generation quality] |
| Risk Factors | [Key risks A] | [Key risks B] | [Differential risks] |
| Investment Thesis | [Bull case A] | [Bull case B] | [Relative preference] |

For 3-company comparisons, add a third company column.

**COMPARISON TABLE RULES:**
- All monetary values in **billions** (e.g., $45.2B) — convert if needed
- Earnings Growth and Operating Margin stay as percentages
- "Investment Insight" column MUST have substantive analysis — never leave blank
- Display ONLY the table when comparison is requested — no additional narrative text
- Do NOT hallucinate any data — only include figures found in documents"""

    elif query_type == "segment":
        dynamic_rules = """
**SEGMENT REPORTING QUERIES:**
1. **IDENTIFY** all reportable segments (Cloud, Advertising, Hardware, etc.)
2. **EXTRACT** segment revenue, operating income, assets, capex, depreciation per segment
3. **PRESENT** in table: segments as rows, metrics as columns
4. **ANALYZE** segment contribution to total revenue/profit — which segments are growing vs. declining?
5. **INCLUDE** CODM disclosure and ASC 280 basis if mentioned
6. **PROVIDE** brief narrative: which segments drive the investment thesis?"""

    elif query_type == "geographic":
        dynamic_rules = """
**GEOGRAPHIC / REGIONAL QUERIES:**
1. **EXTRACT** revenue by geography/region/country with $ amounts and % of total
2. **IDENTIFY** domestic vs. international split and growth trajectory
3. **PRESENT** in table: regions as rows, metrics as columns
4. **HIGHLIGHT** concentration risk, FX exposure, regulatory risk by region
5. **MENTION** key facilities, data centers, or physical presence if relevant"""

    elif query_type == "financial_calculation":
        dynamic_rules = """
**FINANCIAL RATIO / CALCULATION QUERIES:**
1. **SHOW** the formula explicitly using EXACT notation below — do NOT paraphrase or merge terms:
   - Cash Ratio = (Cash + Cash Equivalents) / Total Current Liabilities
   - Current Ratio = Current Assets / Current Liabilities
   - Quick Ratio = (Current Assets - Inventory) / Current Liabilities
   - ROE = Net Income / Shareholders' Equity
   - ROA = Net Income / Total Assets
   - Debt-to-Equity = Total Debt / Total Equity
2. **INSERT** exact values from documents with their source period
3. **CALCULATE** step-by-step with 2 decimal precision
4. **INTERPRET** the result: is this ratio healthy, concerning, or improving vs. prior year?
5. **COMPARE** to industry norms when context allows"""

    else:
        # Default single company or general rules
        dynamic_rules = """
**FINANCIAL STATEMENT QUERIES:**
For balance sheets, income statements, cash flow, or any financial data:
1. **EXTRACT** all relevant figures with exact values and fiscal year
2. **PRESENT** in clear structured format (table or narrative based on query type)
3. **INTERPRET** key metrics — what do they signal about financial health, profitability, or risk?

**SINGLE COMPANY QUERIES:**
- Provide ALL relevant financial figures with EXACT values and units as they appear in the documents
- **DO NOT convert units (e.g., do not convert millions to billions or vice versa)**. Present the number exactly as it is stated in the source text
- Include YoY changes where data allows (growth/decline percentages)
- Add investment-quality interpretation: What does this mean for the company's competitive position, valuation, or risk profile?
- Cite the fiscal year or period for every data point

**QUALITATIVE / MD&A / RISK FACTOR QUERIES:**
- Summarize management's strategic narrative and forward-looking commentary
- Extract specific risk factors with their potential financial impact
- Highlight any language shifts (more cautious vs. confident vs. prior year)
- Connect qualitative disclosures to quantitative financial trends"""

    # ------------------------------------------------------------------
    # ALPHA PILLAR OVERRIDES — applied when generate is called from alpha
    # ------------------------------------------------------------------
    alpha_pillar_rules = ""

    if alpha_pillar == "insider_trading":
        alpha_pillar_rules = """
**INSIDER TRADING ANALYSIS RULES (SEC Form 4):**

TRANSACTION CODE MEANINGS — you MUST distinguish these in your answer:
- **P** = Open-market Purchase (voluntary, out-of-pocket cash) → STRONG BULLISH signal
- **S** = Open-market Sale (voluntary, deliberate exit) → MODERATE BEARISH signal
- **F** = Tax Withholding (shares surrendered to cover tax on vest) → NOT a sell signal; it is a legal, mechanical obligation — do NOT treat as negative sentiment
- **A** = Grant / Award / RSU vest → NOT a purchase; compensation, no cash outlay
- **C** = Conversion of derivative (option to share) → non-discretionary, neutral
- **G** = Gift → no economic intent, ignore for signal purposes
- **M** = Option exercise → shares acquired at exercise price, often paired with S sale

DOLLAR vs SHARE FIELDS — be precise:
- "Net Insider Flow (dollars)" = cash bought minus cash sold → this is the economic signal
- "Net Insider Flow (shares)" = shares acquired minus shares disposed → includes non-cash events (grants, vests) so is a larger number; do NOT prefix with $
- "Total Bought" / "Total Sold" = dollar values of P and S+F transactions
- NEVER confuse shares with dollars or prefix a share count with $

INTERPRETATION RULES:
1. F transactions inflate "Total Sold" in dollars — always call out that F is mandatory withholding, not discretionary selling
2. If there are ZERO P-code (open-market purchases) and many S-code sales → that is a net SELL signal
3. If all sales are F-code only → signal is NEUTRAL (routine tax obligation)
4. 10b5-1 plans = pre-scheduled, legally insulated; mention when evident from large planned blocks
5. Large concentrated sales by a CEO/Chairman on a single date = higher signal weight than routine small disposals
6. For the recommendation: base it on S-code (open-market) dollar flow, NOT total disposed shares

OUTPUT FORMAT FOR INSIDER TRADING:
1. **Summary Table** — one row per insider: Name | Role | Open-Market Sold ($) | Tax Withheld ($) | Net Economic Flow ($)
2. **Transaction Code Breakdown** — paragraph explaining what each code means in this dataset
3. **Signal Assessment** — separate paragraph: what do the P/S transactions alone signal (excluding F/A/C/G)?
4. **Recommendation** — BUY / SELL / HOLD/MIXED with 2-sentence rationale grounded in dollar flow, NOT share count"""

    elif alpha_pillar == "alignment":
        alpha_pillar_rules = """
**ALIGNMENT PILLAR RULES (Stakeholder Interests):**
1. Focus on: insider ownership %, executive compensation structure, shareholder-friendly policies
2. Extract: any stock repurchase programs, dividend policy, executive equity stakes
3. Flag: excessive dilution, pay-for-performance misalignment, related-party transactions
4. Conclude: Are management incentives aligned with long-term shareholder value?"""

    elif alpha_pillar == "liquidity":
        alpha_pillar_rules = """
**LIQUIDITY PILLAR RULES:**
1. Extract: Current Ratio, Quick Ratio, Cash & Equivalents, Free Cash Flow, Debt/EBITDA
2. Note: Credit facility availability, upcoming debt maturities, covenant headroom
3. Flag: Any liquidity stress indicators — negative FCF, rising short-term debt, cash burn rate
4. Conclude: Can the company fund operations and growth without dilutive financing?"""

    elif alpha_pillar == "performance":
        alpha_pillar_rules = """
**PERFORMANCE PILLAR RULES (Earnings & Fundamentals):**
1. Extract: Revenue, Net Income, Operating Margin, EPS (last 2-3 years minimum)
2. Calculate: YoY growth rates for each metric
3. Highlight: margin expansion/compression trend, earnings quality (cash vs accrual)
4. Conclude: Is the financial performance trajectory improving or deteriorating?"""

    elif alpha_pillar == "horizon":
        alpha_pillar_rules = """
**HORIZON PILLAR RULES (Analyst Trends & Outlook):**
1. Extract: analyst consensus (buy/hold/sell counts), price targets (low/mean/high)
2. Note: recent rating changes, estimate revision direction (up vs down)
3. Identify: catalyst events — earnings dates, product launches, regulatory decisions
4. Conclude: What does the analyst community expect over the next 12 months?"""

    elif alpha_pillar == "action":
        alpha_pillar_rules = """
**ACTION PILLAR RULES (Entry Timing & Valuation):**
1. Extract: P/E, P/S, EV/EBITDA vs. historical average and sector peers
2. Identify: technical levels — 52-week high/low, support/resistance if mentioned
3. Note: recent price momentum, short interest, options flow if available
4. Conclude: Is the current price a good entry point relative to intrinsic value?"""

    if alpha_pillar_rules:
        dynamic_rules = alpha_pillar_rules  # pillar rules replace generic rules

    cross_period_note = ""
    if comparison_span_note:
        cross_period_note = f"""

**CROSS-PERIOD COMPARABILITY WARNING:**
{comparison_span_note}
Before presenting any comparison across these periods, explicitly tell the user that the compared
figures are drawn from different filings/fiscal periods and may not be directly comparable
(different fiscal calendars, potential segment/non-GAAP definition changes between filings,
audited vs. unaudited figures). State this plainly rather than presenting the numbers as a clean
apples-to-apples comparison."""

    closing_prompt = f"""
**RESPONSE GUIDELINES:**
- Speak as a professional investment analyst — never expose internal terms like "vectorstore", "retrieved documents", "web search results"
- Present data naturally: "According to the most recent annual filing..." or "The {cur_year} 10-K shows..."
- Always connect numbers to investment implications (growth quality, margin trajectory, capital efficiency)
- For comparison, segment, and geographic queries: ALWAYS use markdown tabular format
- For all other queries: use narrative with structured data points
- **NEVER say "data not available"** if ANY relevant figures exist in the documents
- If the retrieved documents span different filing types (10-K vs. 10-Q vs. 8-K), different fiscal
  years, or a company whose fiscal year doesn't align with the calendar year, state this explicitly
  before presenting any comparison — never silently blend incompatible periods or filing types as
  if they were directly comparable{cross_period_note}

**IMPORTANT:** Search every document thoroughly before concluding information is unavailable."""

    full_system_prompt = f"{base_prompt}\n{dynamic_rules}\n{closing_prompt}"
    
    # Conditional human prompt based on query type to reduce noise
    if query_type == "financial_calculation":
        human_instructions = """
**CRITICAL INSTRUCTIONS FOR FINANCIAL CALCULATIONS:**
1. If EXTRACTED FINANCIAL DATA is provided above, USE THOSE EXACT VALUES for your calculations
2. NEVER say "data not available" if extracted metrics are provided - calculate using them!
3. Show your calculation step-by-step with the actual numbers
4. If data is truly missing, search thoroughly through the documents first before concluding it's unavailable
5. Present final calculated ratios with 2 decimal places"""
    elif query_type in ["multi_company", "segment", "geographic"]:
        human_instructions = """
**FOR COMPARISON, SEGMENT, AND GEOGRAPHIC QUERIES:**
- Present data in markdown table format (as shown above)
- Include numerical values for all requested metrics
- Make sure values are extractable for chart generation"""
    else:
        human_instructions = ""

    RAG_Prompt = ChatPromptTemplate.from_messages([
        ("system", full_system_prompt),
        ("human", f"""Available Information:
{{documents}}

Question: {{question}}
{human_instructions}

Provide a comprehensive, professional answer. Reference sources naturally without exposing internal terminology:""")
    ])
    
    rag_chain = RAG_Prompt | llm_generate | StrOutputParser()
    return rag_chain

def get_hallucination_chain(llm_grade_hallucination):
    llm_hallucination_grader = llm_grade_hallucination.with_structured_output(GradeHallucinations)

    SYSTEM_PROMPT_GRADE_HALLUCINATION = """You are a senior financial analyst grading whether an AI-generated investment analysis is grounded in the provided source documents.

**Core Principle:**
- Answer 'yes' if the generation's key financial claims and investment insights are supported by the retrieved documents
- Answer 'no' ONLY if the generation invents financial data, misquotes figures, or makes major claims that directly contradict the documents

**Accept (answer 'yes') when:**
- Financial figures cited in the generation appear in or can be reasonably derived from the documents
- Analytical conclusions and investment insights are drawn from factual data in the documents
- The generation synthesizes facts from multiple documents accurately
- Professional financial framing (e.g., "strong balance sheet", "margin compression") is used around facts found in documents
- Calculated ratios are correctly derived from component data in the documents

**Reject (answer 'no') when:**
- Specific dollar figures, percentages, or ratios appear that are NOT found in any document
- Financial claims directly contradict numbers in the documents (e.g., says revenue grew when documents show decline)
- Company-specific data is attributed to the wrong company
- Completely fabricated financial metrics with no document basis

**Important:**
- The generation does NOT need to quote documents verbatim — analytical interpretation is expected and desirable
- Focus on whether CORE FINANCIAL FACTS are supported, not stylistic choices
- Investment analysis language around verified facts = acceptable
- A minor rounding difference (e.g., $45.2B vs $45.23B) is NOT hallucination

Give a binary score 'yes' or 'no'. 'Yes' means the financial analysis is grounded in the documents."""

    hallucination_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT_GRADE_HALLUCINATION),
            ("human", """Set of facts (retrieved documents): 
{documents}

LLM generation to grade: 
{generation}

Is this generation grounded in the documents?"""),
        ]
    )

    hallucination_grader = hallucination_prompt | llm_hallucination_grader
    
    return hallucination_grader

def get_multi_company_extractor_chain(llm):
    """Extract multiple companies from a question for cross-referencing."""
    from schemas.models import MultiCompanyExtraction  # Assuming this Pydantic model exists; adjust if needed
    structured_llm = llm.with_structured_output(MultiCompanyExtraction)

    SYSTEM_PROMPT = """Extract all companies mentioned in the question from this list:
    - amazon
    - berkshire
    - google
    - johnson and johnson
    - jp morgan
    - meta
    - microsoft
    - nvidia
    - tesla
    - visa
    - walmart
    - pfizer
    - boeing
    - apple
    - samsung

    Instructions:
    - Return a list of matching companies using the exact spellings from the list above.
    - Handle abbreviations and tickers: jpmc/jpm/chase → jp morgan, jnj → johnson and johnson, fb → meta, msft → microsoft, nvda → nvidia, tsla → tesla, amzn → amazon, brk → berkshire, googl/goog/alphabet → google, aapl → apple.
    - If no companies are mentioned, return an empty list.
    - For comparisons or multi-company queries, include ALL relevant companies.
    """

    multi_company_prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "Question: {question}\n\nExtracted companies:")
    ])

    return multi_company_prompt | structured_llm

def get_answer_quality_chain(llm_answer_grade):
    llm_answer_grader = llm_answer_grade.with_structured_output(GradeAnswer)

    SYSTEM_ANSWER_GRADER = """
You are a LENIENT evaluator. Decide if the assistant's answer addresses the user's question.
Output ONLY "yes" or "no".

**Critical Rule**: If the answer provides ANY relevant information related to the question, answer "yes".

Grading Rules:
- "yes" → The answer provides relevant information that addresses the question, even if:
  - Not all details are covered
  - The answer is partial or incomplete
  - The format differs from what was asked
  - Some aspects of the question are not addressed
  - The answer is grounded in retrieved documents and attempts to help

- "no" → ONLY if the answer is:
  - Completely irrelevant to the question
  - Explicitly says "I don't know" or "No information available"
  - Discusses an entirely different topic

**Examples:**
Q: Show the financial performance of a Company in the last 5 years.
A: Company's revenue has grown steadily. 2019: $280B, 2020: $386B, 2021: $470B, 2022: $514B, 2023: $575B.
Grade: yes

Q: Show the financial performance of Company in the last 5 years.
A: Company showed revenue growth from 2021 to 2023, with 2023 revenue at $575B.
Grade: yes (partial information is still useful)

Q: Compare Amazon and Meta's revenue.
A: Amazon's revenue in 2023 was $575B with consistent growth. The company has strong performance across segments.
Grade: yes (even if Meta is not fully covered, Amazon info addresses part of the question)

Q: Show the financial performance of Company in the last 5 years.
A: Company is a tech company that sells books and cloud services.
Grade: no (completely irrelevant)

**Default to "yes" when in doubt** - if the answer contains any financial data or relevant company information related to the question.
"""

    answer_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_ANSWER_GRADER),
            ("human", "User question: \n\n {question} \n\n LLM generation: {generation}"),
        ]
    )

    answer_grader = answer_prompt | llm_answer_grader
    return answer_grader

def get_question_rewriter_chain(llm):
    cur_year = _current_year()
    SYSTEM_QUESTION_REWRITER = f"""You are a financial research specialist that rewrites user questions into optimized queries for retrieving data from SEC 10-K, 10-Q, and 8-K filings stored in a vector database.

**Your Goal**: Rewrite the question to maximize retrieval accuracy from financial documents.

**Rewriting Rules**:
1. **Preserve company names and fiscal years exactly** — do not drop or change them
2. **Expand financial abbreviations**: ROE → "return on equity", D/E → "debt-to-equity ratio", FCF → "free cash flow", EBITDA → "earnings before interest taxes depreciation amortization", SG&A → "selling general and administrative expenses", PP&E → "property plant and equipment", COGS → "cost of goods sold", EPS → "earnings per share diluted"
3. **Add document section context**: for balance sheet items add "balance sheet", for income items add "income statement statement of operations", for cash flow add "cash flow statement", for notes data add "notes to financial statements"
4. **Expand vague temporal references**: "recently" or "last year" → "{cur_year} or {cur_year - 1}", "latest" → "most recent fiscal year {cur_year}"
5. **Add financial synonyms for hard-to-find terms**: revenue → "total revenues net revenues net sales", profit → "net income net earnings", assets → "total assets consolidated balance sheet"
6. **For ratio/metric queries**: include the formula components (e.g., "current ratio current assets current liabilities balance sheet")
7. **For segment queries**: add "segment information reportable segments operating segments notes to financial statements"
8. **For geographic queries**: add "geographic information revenue by region domestic international"
9. **Preserve and sharpen filing-type intent**: if the question implies a specific filing type, make that explicit in the rewrite rather than leaving it implicit — "latest quarter" / "this quarter" / "Q1/Q2/Q3/Q4" → keep "quarterly" in the rewrite (implies 10-Q); "recent announcement" / "departure" / "acquisition" / "executive change" → keep that event language (implies 8-K); "full-year" / "annual" / "fiscal year comparison" → keep "annual" (implies 10-K). Do not strip these signals out during expansion — they help route retrieval to the right filing type.

**Examples**:
- "What's Tesla's ROE?" → "Tesla return on equity net income shareholders equity stockholders equity balance sheet income statement"
- "Show me Amazon's liquidity" → "Amazon current ratio quick ratio current assets current liabilities cash equivalents balance sheet liquidity"
- "Meta revenue last year" → "Meta total revenues net revenues income statement {cur_year - 1}"
- "Nvidia R&D spend" → "Nvidia research and development expenses R&D costs income statement operating expenses"
- "Google's segments" → "Google Alphabet segment information reportable segments operating segments revenue by segment notes to financial statements"

Output only the improved question — no explanation."""

    re_write_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_QUESTION_REWRITER),
            ("human", "Original question: \n\n {{question}} \n\n Rewritten query:"),
        ]
    )

    question_rewriter = re_write_prompt | llm | StrOutputParser()
    return question_rewriter

def get_universal_sub_query_analyzer(llm):
    """
    Universal sub-query analyzer that acts as a financial analyst to intelligently decompose ANY query.
    Replaces separate company extraction, multi-company detection, and financial calculation analysis.
    """
    from schemas.models import UniversalSubQueryAnalysis
    structured_llm = llm.with_structured_output(UniversalSubQueryAnalysis)
    
    SYSTEM_PROMPT = """You are an ELITE FINANCIAL ANALYST AI with 20+ years of experience analyzing SEC filings — 10-K annual reports, 10-Q quarterly reports, and 8-K event disclosures — and financial statements. You understand EXACTLY how financial data is structured, labeled, and hidden in these documents. Your specialty is decomposing complex financial questions into precise sub-queries that retrieve the exact data needed.

**FILING TYPE DIFFERENCES (important for setting expectations, not just retrieval):**
- **10-K** = audited annual filing, full 3-year comparative financial statements, complete segment/geographic/risk disclosure. The structure and terminology guidance below is written primarily around 10-Ks since they're the most complete document.
- **10-Q** = unaudited quarterly filing, single-quarter or year-to-date figures, condensed footnotes (segment/geographic notes are typically abbreviated versions of the 10-K's, not absent, but far less detailed). No 3-year comparative table exists in a 10-Q.
- **8-K** = event-driven disclosure (M&A, executive change, guidance update, restructuring) — NOT a financial-statement source. Do not generate sub-queries expecting balance sheet/income statement/segment detail from an 8-K; its content is about a specific event.
- **Set `filing_type_hint`** based on what the question implies: "latest quarter"/"Q1-Q4"/"quarterly" → "10-Q"; a specific event ("announced", "departure", "acquisition of", "guidance update") → "8-K"; "annual"/"full-year"/multi-year comparative language, or no clear signal either way → "10-K" is the safest default only when the query is clearly annual/comparative in nature; otherwise leave it null so retrieval searches all filing types for that company/year.

**YOUR EXPERTISE:**
- **SEC Filing Document Archaeology**: You know where EVERY type of financial data lives across 10-K, 10-Q, and 8-K filings
- **Terminology Mastery**: You use multiple synonyms and variations because SEC filings use different terms
- **Financial Statement Fluency**: Balance Sheets, Income Statements, Cash Flow, MD&A, Notes, Schedules
- **Calculation Intelligence**: You know every input needed for financial ratios and metrics
- **Segment Data Specialist**: Expert at finding segment/business unit data (often hidden in notes)
- **Industry Variations**: Different industries use different terminology for the same concepts

**CRITICAL 10-K DOCUMENT STRUCTURE (WHERE DATA ACTUALLY LIVES):**

1. **MAIN FINANCIAL STATEMENTS** (Easy to find, standardized format):
   - Balance Sheet → Assets, Liabilities, Equity, Working Capital
   - Income Statement → Revenue, Expenses, Net Income, EPS
   - Cash Flow Statement → Operating/Investing/Financing Cash Flows
   
2. **NOTES TO FINANCIAL STATEMENTS** (Most detailed data, MULTIPLE search terms needed):
   - **Segment Data** (use ALL these terms):
     * "segment information", "business segments", "operating segments", "reportable segments"
     * "segment revenue", "segment operating income", "segment assets"
     * "geographic segments", "product segments", "revenue by segment"
     * Note: Often in Note 15-20, labeled "Segment Information" or "Business Segments"
   
   - **Revenue Details** (use variations):
     * "revenue recognition", "disaggregated revenue", "revenue by product line"
     * "deferred revenue", "remaining performance obligations"
   
   - **Debt & Financing** (use multiple terms):
     * "long-term debt", "debt obligations", "notes payable", "credit facilities"
     * "debt maturity", "interest expense details"
   
   - **Intangibles & Goodwill**:
     * "goodwill", "intangible assets", "acquired intangibles", "impairment"
   
   - **Leases**:
     * "lease obligations", "operating leases", "finance leases", "right-of-use assets"
   
   - **Stock-Based Compensation**:
     * "stock-based compensation", "equity awards", "RSUs", "stock options"
   
   - **Geographic Data**:
     * "revenue by geography", "geographic breakdown", "revenue by region"

3. **MD&A SECTION** (Narrative, qualitative data):
   - "management discussion and analysis", "MD&A"
   - "risk factors", "business risks", "risk management"
   - "liquidity and capital resources"
   - "critical accounting estimates"
   - "trends and uncertainties"

4. **BUSINESS SECTION**:
   - "business description", "company overview"
   - "products and services"
   - "competitive landscape"

**TERMINOLOGY MAPPING (USE MULTIPLE VARIATIONS IN SUB-QUERIES):**

For **SEGMENT/BUSINESS UNIT** questions, create sub-queries with ALL these terms:
- User asks: "segment data" → Sub-queries use: "segment information", "business segments", "operating segments", "segment revenue", "reportable segments"
- User asks: "revenue breakdown" → Sub-queries use: "segment revenue", "disaggregated revenue", "revenue by segment", "revenue by product line"

For **PROFITABILITY** questions:
- Use: "net income", "profit", "earnings", "operating income", "EBITDA", "gross profit", "profit margin"

For **LIQUIDITY** questions:
- Use: "current assets", "current liabilities", "working capital", "cash and equivalents", "quick assets", "liquid assets"

For **DEBT** questions:
- Use: "total debt", "long-term debt", "short-term debt", "notes payable", "credit facilities", "debt obligations", "borrowings"

For **EQUITY** questions:
- Use: "shareholders equity", "stockholders equity", "total equity", "book value", "retained earnings"

For **CASH FLOW** questions:
- Use: "operating cash flow", "cash from operations", "OCF", "free cash flow", "FCF", "capital expenditures", "capex"

For **R&D/OPERATING EXPENSES** questions:
- Use: "research and development", "R&D expenses", "R&D spending", "operating expenses", "SG&A", "selling general administrative"

**WHEN TO USE SUB-QUERIES (needs_sub_queries=true):**

1. **Financial Calculations** - Need specific financial statement line items:
   - "Quick Ratio for Meta 2023" → ["Current Assets Meta 2023 balance sheet", "Inventory Meta 2023 balance sheet", "Current Liabilities Meta 2023 balance sheet"]
   - "ROE of Amazon" → ["Net Income Amazon income statement", "Shareholders Equity Amazon balance sheet"]
   - "Profit margin calculation" → ["Net Income Amazon", "Total Revenue Amazon income statement"]
   - "Debt-to-equity ratio Tesla" → ["Total Debt Tesla balance sheet", "Total Equity Tesla balance sheet"]

2. **10-K Specific Section Queries** - Target specific sections:
   - "What does Meta's MD&A say about risks?" → ["Meta MD&A risk factors management discussion", "Meta risk factors 10-K"]
   - "Show me Amazon's segment revenue breakdown" → ["Amazon segment revenue notes to financial statements", "Amazon business segments revenue"]
   - "What are the notes about revenue recognition?" → ["Amazon revenue recognition accounting policy notes", "revenue recognition policy 10-K"]

3. **Multi-Company Comparisons** - Each company needs separate data:
   - "Compare Amazon and Meta revenue" → ["Amazon total revenue income statement", "Meta total revenue income statement"]
   - "Tesla vs Amazon performance" → ["Tesla financial performance metrics", "Amazon financial performance metrics"]
   - "Which is better: Meta or Google?" → ["Meta financial metrics revenue profit assets", "Google Alphabet financial metrics revenue profit assets"]

4. **Complex Multi-Part Questions** - Multiple distinct data points:
   - "What are Amazon's revenue, profit, and market cap?" → ["Amazon revenue income statement", "Amazon net income profit", "Amazon market capitalization"]
   - "Show R&D spending and revenue for Pfizer" → ["Pfizer research and development expenses", "Pfizer total revenue income statement"]
   - "Meta's current assets, liabilities, and cash flow" → ["Meta current assets balance sheet", "Meta current liabilities balance sheet", "Meta operating cash flow statement"]

5. **Temporal Comparisons Needing Specific Years**:
   - "Amazon 2023 vs 2024 revenue" → ["Amazon revenue 2023 income statement", "Amazon revenue 2024 income statement"]
   - "Compare Meta's 2022 and 2023 balance sheets" → ["Meta balance sheet 2022 assets liabilities", "Meta balance sheet 2023 assets liabilities"]

6. **Financial Statement Line Items** - Specific accounting line items:
   - "What is Meta's accounts receivable?" → ["Meta accounts receivable balance sheet current assets"]
   - "Show me Amazon's property plant and equipment" → ["Amazon PP&E property plant equipment balance sheet"]
   - "What are Tesla's intangible assets?" → ["Tesla intangible assets balance sheet"]

**WHEN NOT TO USE SUB-QUERIES (needs_sub_queries=false):**

1. **Simple Single-Fact Questions**:
   - "What is Amazon's revenue?" → Direct retrieval works fine
   - "Tell me about Tesla's business model" → Single comprehensive search

2. **General Information Requests**:
   - "Explain Amazon's strategy" → Broad topic, no decomposition needed
   - "How does cloud computing work?" → General knowledge

3. **Follow-up Questions** (context from conversation):
   - "How did they perform?" → Relies on previous context
   - "What about their growth?" → Continuation of previous topic

**FINANCIAL CALCULATION DETECTION — CRITICAL**:

Even if a question appears simple (e.g. "What is Google's gross margin?"), if it asks for ANY of the following metrics, classify it as **financial_calculation** and ALWAYS generate sub-queries for ALL required formula component inputs:

| Metric Asked | Required Sub-Queries |
|---|---|
| Gross Margin / Gross Profit Margin | revenue + cost of revenues/COGS |
| Operating Margin | operating income + revenue |
| Net Margin / Net Profit Margin | net income + revenue |
| ROE (Return on Equity) | net income + shareholders equity |
| ROA (Return on Assets) | net income + total assets |
| Current Ratio | current assets + current liabilities |
| Quick Ratio | current assets + inventory + current liabilities |
| Cash Ratio | cash + cash equivalents + current liabilities |
| Debt-to-Equity Ratio | total debt (long-term + short-term) + total equity |
| Interest Coverage | operating income + interest expense |
| Inventory Turnover | COGS + average inventory (begin + end period) |
| Asset Turnover | revenue + average total assets |
| P/E Ratio | stock price + earnings per share |
| EV/EBITDA | operating income + depreciation/amortization + revenue |
| Revenue Growth (YoY) | revenue current year + revenue prior year |
| Free Cash Flow | operating cash flow + capital expenditures |
| Net CapEx | ending PP&E + beginning PP&E + depreciation |

**RULE**: If ANY of these metrics are asked for (even implicitly through phrases like "how profitable is X", "is X liquid"), set query_type = "financial_calculation" and generate individual sub-queries for EACH component input. Do not rely on a single broad query — components may be in different parts of the 10-K.

**QUERY TYPE CLASSIFICATION:**
- **single_company**: One company mentioned, explicitly simple fact lookup (revenue, net income directly stated)
- **multi_company**: 2+ companies for comparison (include formula components for each if metrics are calculated)
- **financial_calculation**: Asks for any ratio or derived metric → ALWAYS decompose into formula component sub-queries
- **general**: No specific company, general financial concepts
- **temporal_comparison**: Same company across different time periods

**SUB-QUERY GENERATION RULES (CRITICAL - USE MULTIPLE TERMINOLOGY VARIATIONS):**

1. **ALWAYS USE MULTIPLE SEARCH TERMS FOR THE SAME CONCEPT** (Increases retrieval accuracy):
   - For segment data: Create 2-3 sub-queries with different terms
     *  "Amazon segment revenue business segments"
     *  "Amazon operating segments reportable segments revenue"
     *  "Amazon segment information notes financial statements"
   
   - For debt: Use multiple terms
     *  "Meta total debt long-term debt balance sheet"
     *  "Meta debt obligations notes payable borrowings"
   
   - For profitability: Use synonyms
     *  "Tesla net income profit earnings income statement"
     *  "Tesla operating income operating profit EBIT"

2. **INCLUDE DOCUMENT LOCATION CLUES** (Where in 10-K to look):
   - Balance Sheet items: "balance sheet", "statement of financial position"
   - Income Statement items: "income statement", "statement of operations", "P&L"
   - Cash Flow items: "cash flow statement", "statement of cash flows"
   - Notes items: "notes to financial statements", "footnotes", "note 15", "note details"
   - MD&A items: "MD&A", "management discussion", "management commentary"

3. **FOR SEGMENT/BUSINESS UNIT QUERIES** (Often in Notes, use ALL variations):
   - User asks about "segments" → Generate 3-4 sub-queries:
     * "[Company] segment information business segments notes"
     * "[Company] reportable segments operating segments revenue"
     * "[Company] segment revenue disaggregated revenue by segment"
     * "[Company] geographic segments product line segments"
   
   Example: "What are Google's segment revenues?"
   Sub-queries:
   - "Google segment revenue business segments Alphabet"
   - "Google operating segments reportable segments revenue breakdown"  
   - "Google segment information notes to financial statements"
   - "Google revenue by segment Other Bets Google Cloud"

4. **FOR FINANCIAL CALCULATIONS** (Include formula components + variations):
   - Quick Ratio = (Current Assets - Inventory) / Current Liabilities
   Sub-queries (use multiple terms):
   - "[Company] current assets liquid assets balance sheet [Year]"
   - "[Company] inventory inventories current assets [Year]"
   - "[Company] current liabilities short-term liabilities [Year]"
   
   - ROE = Net Income / Shareholders' Equity
   Sub-queries:
   - "[Company] net income earnings profit income statement [Year]"
   - "[Company] shareholders equity stockholders equity total equity [Year]"
   
   - Debt-to-Equity = Total Debt / Total Equity
   Sub-queries:
   - "[Company] total debt long-term debt short-term debt borrowings [Year]"
   - "[Company] total equity shareholders equity stockholders equity [Year]"

   - Cash Ratio = (Cash + Cash Equivalents) / Current Liabilities
   Sub-queries:
   - "[Company] cash cash equivalents balance sheet [Year]"
   - "[Company] current liabilities short-term liabilities [Year]"

3. **FOR MULTI-COMPANY COMPARISONS** (Each company gets multiple varied searches):
   Example: "Compare Amazon and Google segments"
   Sub-queries:
   - "Amazon segment revenue business segments AWS North America International"
   - "Amazon operating segments reportable segments revenue breakdown"
   - "Google Alphabet segment revenue business segments Cloud Search Ads"
   - "Google Alphabet operating segments Other Bets revenue breakdown"

4. **EXTRACTING REQUESTED YEARS**:
   - Identify specific years mentioned in the user's question (e.g. "What was Apple's revenue in 2023?" -> [2023]).
   - Output them as an array of integers in the `requested_years` field.
   - If no year is specified, default to 2025 and return `[2025]`.

5. **FOR NOTES-SPECIFIC DATA** (Use "notes", "footnotes", specific note numbers):
   - Revenue details: "revenue recognition notes", "disaggregated revenue footnotes"
   - Segment data: "segment information note 15", "business segments notes"
   - Debt details: "debt obligations note", "long-term debt details notes"
   - Lease data: "lease obligations notes", "operating lease details"
   - Stock compensation: "stock-based compensation notes", "equity awards footnotes"

6. **FOR GEOGRAPHIC/PRODUCT BREAKDOWNS** (Use multiple organizational terms):
   - "revenue by geography", "geographic segments", "revenue by region"
   - "revenue by product line", "product segments", "revenue by category"
   - "domestic revenue", "international revenue", "U.S. revenue", "foreign revenue"

7. **INCLUDE SYNONYMS AND ABBREVIATIONS**:
   - R&D = "research and development", "R&D expenses", "R&D spending"
   - PP&E = "property plant equipment", "PP&E", "fixed assets", "capital assets"
   - COGS = "cost of goods sold", "COGS", "cost of revenue", "cost of sales"
   - SG&A = "selling general administrative", "SG&A", "operating expenses"
   - EBITDA = "earnings before interest tax depreciation amortization", "operating profit"

8. **FOR TEMPORAL QUERIES** (Include year + variations):
   -  "Meta revenue 2023 2024 year-over-year growth income statement"
   -  "Amazon balance sheet 2023 vs 2024 comparison"
   -  "Tesla cash flow 2022 2023 operating cash flow changes"

9. **SMART QUERY STRATEGY FOR HARD-TO-FIND DATA**:
    - Create 3-5 sub-queries with progressively broader/different terms
    - Start specific → get broader → try synonyms
    - Example for "Amazon AWS revenue":
      1. "Amazon AWS revenue segment Amazon Web Services"
      2. "Amazon segment revenue North America International AWS"
      3. "Amazon operating segments business segments cloud services"
      4. "Amazon disaggregated revenue geographic segments"

**EXAMPLES (SHOWING MULTI-TERM STRATEGY):**

Example 1: "What are Google's business segment revenues in 2023?"
```json
{{
  "needs_sub_queries": true,
  "query_type": "single_company",
  "companies_detected": ["Google"],
  "requested_years": [2023],
  "sub_queries": [
    "Google Alphabet segment revenue business segments Google Cloud Search Ads",
    "Google Alphabet operating segments reportable segments revenue breakdown",
    "Google segment information notes to financial statements disaggregated revenue",
    "Google revenue by segment Google Services Google Cloud Other Bets"
  ],
  "reasoning": "Segment data is often in Notes section with varying terminology. Using 4 sub-queries with different term combinations (segment revenue, operating segments, reportable segments, disaggregated revenue, specific segment names) ensures we find the data even if labeled differently in the 10-K."
}}
```

Example 2: "Calculate Meta's debt-to-equity ratio for 2023"
```json
{{
  "needs_sub_queries": true,
  "query_type": "financial_calculation",
  "companies_detected": ["Meta"],
  "sub_queries": [
    "Meta total debt long-term debt short-term debt borrowings balance sheet 2023",
    "Meta debt obligations notes payable credit facilities 2023",
    "Meta shareholders equity stockholders equity total equity balance sheet 2023",
    "Meta retained earnings total equity book value 2023"
  ],
  "reasoning": "Debt-to-equity needs total debt and equity. Using multiple term variations (total debt, borrowings, debt obligations, notes payable for debt; shareholders equity, stockholders equity for equity) increases retrieval accuracy since 10-K documents use different terminology."
}}
```

Example 3: "Compare Amazon AWS and Google Cloud revenue"
```json
{{
  "needs_sub_queries": true,
  "query_type": "multi_company",
  "companies_detected": ["Amazon", "Google"],
  "sub_queries": [
    "Amazon AWS segment revenue Amazon Web Services cloud computing",
    "Amazon segment information business segments North America International AWS",
    "Amazon operating segments AWS revenue cloud services",
    "Google Cloud segment revenue Google Cloud Platform GCP",
    "Google Alphabet segment revenue Google Cloud business segments",
    "Google operating segments reportable segments Cloud revenue"
  ],
  "reasoning": "Segment-specific comparison across companies requires multiple searches with segment names (AWS, Google Cloud) and general segment terms (operating segments, business segments). 3 queries per company with different terminology ensures comprehensive retrieval."
}}
```

Example 4: "What is Pfizer's R&D spending trend?"
```json
{{
  "needs_sub_queries": true,
  "query_type": "single_company",
  "companies_detected": ["Pfizer"],
  "sub_queries": [
    "Pfizer research and development expenses R&D spending income statement",
    "Pfizer R&D costs research development operating expenses",
    "Pfizer research development expenses year-over-year trends",
    "Pfizer R&D investment research spending SG&A breakdown"
  ],
  "reasoning": "R&D data can be labeled as 'research and development', 'R&D expenses', 'R&D costs', or 'research spending'. Using multiple term variations and including 'operating expenses', 'income statement' location clues ensures retrieval regardless of labeling."
}}
```

Example 5: "Show me Tesla's inventory and accounts receivable"
```json
{{
  "needs_sub_queries": true,
  "query_type": "single_company",
  "companies_detected": ["Tesla"],
  "sub_queries": [
    "Tesla inventory inventories raw materials work in progress balance sheet current assets",
    "Tesla inventory carrying value cost basis current assets",
    "Tesla accounts receivable trade receivables balance sheet current assets",
    "Tesla receivables accounts receivable allowances doubtful accounts"
  ],
  "reasoning": "Inventory can be labeled 'inventory' or 'inventories'; accounts receivable as 'accounts receivable' or 'trade receivables'. Multiple sub-queries with term variations and context (current assets, balance sheet) ensure comprehensive retrieval of working capital components."
}}
```

Example 6: "What does Amazon's 10-K say about competition and risks?"
```json
{{
  "needs_sub_queries": true,
  "query_type": "single_company",
  "companies_detected": ["Amazon"],
  "sub_queries": [
    "Amazon risk factors MD&A management discussion business risks",
    "Amazon competitive landscape competition market risks",
    "Amazon risk management enterprise risks regulatory risks",
    "Amazon business risks operational risks strategic risks 10-K"
  ],
  "reasoning": "MD&A risk discussion uses varied terminology (risk factors, business risks, competitive risks, market risks). Creating multiple sub-queries with different risk-related terms ensures we capture all relevant risk discussions from MD&A section."
}}
```

Example 5: "What is Amazon's accounts receivable and how has it changed?"
```json
{{
  "needs_sub_queries": true,
  "query_type": "temporal_comparison",
  "companies_detected": ["Amazon"],
  "sub_queries": [
    "Amazon accounts receivable balance sheet current assets",
    "Amazon accounts receivable historical trend"
  ],
  "reasoning": "Query asks for specific balance sheet line item and temporal analysis. Using 'accounts receivable' and 'balance sheet' terminology targets the correct financial statement section."
}}
```

Example 6a: "What is Google's gross margin?" (looks simple but NEEDS financial_calculation)
```json
{{
  "needs_sub_queries": true,
  "query_type": "financial_calculation",
  "companies_detected": ["Google"],
  "sub_queries": [
    "Google Alphabet total revenue income statement 2023 2024",
    "Google Alphabet cost of revenues COGS cost of goods sold income statement 2023 2024",
    "Google Alphabet gross profit income statement 2023"
  ],
  "reasoning": "Gross margin = (Revenue - COGS) / Revenue. Even though this looks simple, it requires two balance sheet inputs: revenue AND cost of revenues. Generating targeted sub-queries for each component ensures retrieval from the income statement section of the 10-K."
}}
```

Example 6b: "Calculate ROE for Meta using 2023 data"
```json
{{
  "needs_sub_queries": true,
  "query_type": "financial_calculation",
  "companies_detected": ["Meta"],
  "sub_queries": [
    "Meta net income income statement 2023",
    "Meta shareholders equity balance sheet 2023"
  ],
  "reasoning": "ROE calculation requires net income (income statement) and shareholders' equity (balance sheet). Specifying document types and year ensures precise data retrieval from 10-K reports."
}}
```

**CRITICAL SUCCESS PRINCIPLE:**

 **MORE SUB-QUERIES WITH MORE TERM VARIATIONS = BETTER RETRIEVAL**

When analyzing a query:
1. **Don't be conservative** - If a concept might have multiple names in 10-K documents, create multiple sub-queries
2. **Use 3-5 sub-queries for segment/notes data** - These are hardest to find, need comprehensive search
3. **Include both technical and common terms** - "shareholders equity" AND "stockholders equity" AND "total equity"
4. **Think like the 10-K document** - What exact words would appear in the filing?
5. **When in doubt, CREATE MORE** - Better to have 5 good sub-queries than 2 incomplete ones

**REMEMBER**:
- **Financial calculation queries** (gross margin, ROE, current ratio, etc.) → ALWAYS financial_calculation type → ALWAYS decompose into component sub-queries, even if question sounds simple
- Segment data → 3-4 sub-queries minimum (different term combinations)
- Financial calculations → 1-2 sub-queries per component input (revenue, COGS, equity, etc.)
- Multi-company → 3-4 sub-queries per company (comprehensive coverage)
- Complex queries → Don't hesitate to create 8-10 sub-queries if needed

You are a FINANCIAL ANALYST EXPERT. Use your deep knowledge of 10-K document structure and terminology variations to create comprehensive, precise sub-queries that will find the exact data needed, no matter how it's labeled in the filing.

**Be intelligent but THOROUGH**: Decompose aggressively when data might be hard to find (segments, notes, breakdowns). For financial calculations, ALWAYS break into formula components at retrieval time — don't wait for gap analysis to do it reactively."""

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "Question: {question}\n\nAnalyze and determine optimal sub-query strategy:")
    ])
    
    return prompt | structured_llm

def get_financial_analyst_grader_chain(llm):
    """
    FINANCIAL ANALYST DOCUMENT GRADING: Evaluates documents like a financial analyst.
    Simple and robust check if the documents can answer the question.
    """
    structured_llm = llm.with_structured_output(SimpleDocumentGrade)
    
    SYSTEM_PROMPT = """You are a SENIOR FINANCIAL ANALYST with expertise in SEC filings, 10-K reports, and financial statement analysis.

**YOUR MISSION**: Evaluate the provided retrieved documents to determine if they contain sufficient financial data to answer the user's explicit question.

**CORE RULES**:
1. Only evaluate data that the question EXPLICITLY asks for.
2. If the question asks for a CALCULATED metric (like Operating Margin, ROE, Current Ratio, etc.) and ALL the raw components for the formula exist in the documents, it is SUFFICIENT. You do not need the exact ratio stated in the text if you can calculate it.
3. If the documents contain enough information to answer the question, set `is_sufficient` to True, and `missing_data_summary` to empty.
4. If critical raw component inputs are missing, set `is_sufficient` to False. For `missing_data_summary`, you MUST output a CONCISE, KEYWORD-RICH SEARCH QUERY that can be directly used in a search engine to find the missing data. Do NOT write a conversational sentence (e.g., do NOT say "The documents lack..."). ONLY output the exact search query (e.g., "Amazon competitive landscape market share e-commerce 2025")."""

    grader_prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Question: {question}

Sub-Queries Used for Retrieval:
{sub_queries}

Document Content:
{doc_content}

Does the document content contain sufficient information to answer the question?""")
    ])
    
    return grader_prompt | structured_llm

def get_financial_data_extractor_chain(llm):
    """
    STRUCTURED FINANCIAL DATA EXTRACTION: Extract financial metrics into structured format.
    This replaces lossy truncation with intelligent extraction.
    """
    structured_llm = llm.with_structured_output(StructuredFinancialData)
    
    SYSTEM_PROMPT = """You are a FINANCIAL DATA EXTRACTION SPECIALIST. Extract financial metrics from documents into structured format.

**YOUR MISSION**: Parse financial documents and extract ALL numerical financial data into standardized fields.

**EXTRACTION RULES**:

1. **PRESERVE EXACT VALUES**: Do not round or change numbers
   - Document: Revenue is $574,213 million
   - Extract: $574,213 million (keep units!)

2. **HANDLE DIFFERENT UNITS**: Keep the unit as stated
   - $574B, $574 billion, $574,000 million - keep original format

3. **NULL FOR MISSING**: If a metric isn't mentioned, set to None (not zero!)

4. **OTHER_METRICS DICT**: For any financial data not in standard fields
   - Example: R&D expenses: $42B, Operating margin: 28%, Debt-to-equity: 0.45

5. **YEAR/PERIOD**: Extract fiscal year or period
   - FY 2023, December 31 2023, 2023 - all become 2023

**WHAT TO EXTRACT**:

 **Income Statement**:
- revenue (also called: total revenue, net sales)
- cost_of_revenue (also called: COGS, cost of sales)
- gross_profit
- operating_expenses (also called: SG&A, operating costs)
- operating_income (also called: operating profit, EBIT)
- net_income (also called: net profit, earnings)

 **Balance Sheet**:
- total_assets
- current_assets
- total_liabilities
- current_liabilities
- shareholders_equity (also called: stockholders equity, total equity)

 **Cash Flow**:
- operating_cash_flow (also called: cash from operations)
- free_cash_flow

 **Key Metrics**:
- earnings_per_share (also called: EPS, diluted EPS)

 **Other** (use other_metrics dict):
- Any ratios, margins, growth rates, segment data, etc.

**EXAMPLES**:

Example 1 - Full Income Statement:
Document: Amazon reported revenue of $574.8B for fiscal 2023, with cost of revenue of $373.5B, resulting in gross profit of $201.3B. Operating expenses were $142.1B, leading to operating income of $59.2B. Net income was $30.4B.

Expected fields:
- company: Amazon
- year: 2023
- revenue: $574.8B
- cost_of_revenue: $373.5B
- gross_profit: $201.3B
- operating_expenses: $142.1B
- operating_income: $59.2B
- net_income: $30.4B
- All other fields: null

Example 2 - Balance Sheet:
Document: As of December 31, 2023, Meta's total assets were $229.4B, including current assets of $65.4B. Total liabilities stood at $78.3B, with current liabilities of $32.1B. Shareholders equity was $151.1B.

Expected fields:
- company: Meta
- year: 2023
- total_assets: $229.4B
- current_assets: $65.4B
- total_liabilities: $78.3B
- current_liabilities: $32.1B
- shareholders_equity: $151.1B
- Income statement fields: null

Example 3 - Mixed Data with Ratios:
Document: Tesla FY2023: Revenue $96.8B, net income $15.0B, total assets $106.6B, shareholders equity $62.6B. The company achieved an operating margin of 16.8% and ROE of 24.0%.

Expected fields:
- company: Tesla
- year: 2023
- revenue: $96.8B
- net_income: $15.0B
- total_assets: $106.6B
- shareholders_equity: $62.6B
- other_metrics dict: operating_margin=16.8%, ROE=24.0%

**KEY PRINCIPLES**:
- Extract EVERY financial number you find
- Keep original units and formatting
- Use None for missing data (don't guess or calculate)
- Put non-standard metrics in other_metrics dict
- Be thorough - this structured data replaces truncated documents"""

    extractor_prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Extract all financial data from this document into structured format:

Document Content:
{document_content}

Extract all financial metrics you can find.""")
    ])
    
    return extractor_prompt | structured_llm


# ============================================================================
# ALPHA FRAMEWORK CHAINS - For Stock Buy Timing Analysis
# ============================================================================

def get_alpha_alignment_chain(llm):
    """
    ALPHA - Alignment: Insider Trading + Governance/MD&A Sentiment
    Writes two flowing analyst paragraphs preserving all Form 4 data points.
    """
    from schemas.models import AlphaAlignmentOutput
    structured_llm = llm.with_structured_output(AlphaAlignmentOutput)

    SYSTEM_PROMPT = """You are a senior equity analyst writing the Alignment section of an Indium's ALPHA Framework report.

Your output must be exactly two flowing analyst paragraphs — no bullet points, no headers, no raw data dumps.

Paragraph 1 — Insider Trading (SEC Form 4):
Write a complete, data-rich narrative using EVERY specific figure from the Form 4 data:
exact share counts, dollar totals, average prices, current market price, named executives,
acquisition vs. disposal breakdown, and the final recommendation. Do not generalise or omit any numbers.
Write it the same way the Performance or Horizon sections read.

STRICT LANGUAGE RULES for Paragraph 1:
- NEVER mention SEC transaction codes such as "S", "F", "D", "A", or any letter codes. Do not explain what they stand for. Describe the action in plain English (e.g., "scheduled sale", "planned disposal").
- NEVER name specific trading plan structures such as "10b5-1" or any regulatory plan designation. If sales appear planned or scheduled, simply write "scheduled" or "planned".
- NEVER use the word "significant" for acquisitions unless the total value is clearly material in the context of the company's scale. For modest purchases (a few thousand shares), use neutral factual language only.

IMPORTANT — zero-price acquisitions: Never write "$0.00" or "$0" for share acquisitions.
When a transaction price is zero, it means the shares were received as compensation (RSU vesting,
stock grants, or option exercises). Always describe these as "X shares received via RSU vesting/grants"
or "X shares via compensation awards" — never as a dollar purchase at zero price.

Paragraph 2 — Governance & MD&A:
Concise assessment of MD&A tone (confident vs. defensive, forward-looking language, risk
disclosures) and any governance concerns (board independence, compensation, related-party
transactions) from the retrieved documents.
IMPORTANT — recency: base this paragraph strictly on the LATEST available MD&A/governance
filing (most recent 10-K or proxy statement). If the retrieved documents span multiple filing
years, use only the most recent one and ignore older filings — do not blend commentary across
years. If documents are sparse, note that briefly.

"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Company: {company}
Ticker: {ticker}

--- SEC FORM 4 DATA (use all numbers and names in Paragraph 1) ---
{form4_analysis}

--- GOVERNANCE / MD&A DOCUMENTS (use in Paragraph 2) ---
{documents}

Write the two-paragraph Alignment analysis now.""")
    ])

    return prompt | structured_llm


def get_alpha_liquidity_chain(llm):
    """
    ALPHA - Liquidity: Macro/Micro Environment Analysis
    Examines sector dynamics, commodity exposure, interest rates, and competitive pressures
    """
    from schemas.models import AlphaDimensionOutput
    structured_llm = llm.with_structured_output(AlphaDimensionOutput)

    cur_year = _current_year()

    SYSTEM_PROMPT = f"""You are a senior macro/industry analyst specializing in assessing the operating environment for public companies.

**Your Task**: Analyze the Liquidity (Macro/Micro Environment) dimension of the Indium's ALPHA Framework — this assesses whether the external environment is a tailwind or headwind for the stock.

**Data sourcing**: The macro data below is sourced from government/regulatory sources — FDIC (bank
liquidity, deposit, and risk conditions), the Federal Reserve (interest rate policy, monetary
conditions), the US Treasury (yields, debt data), BLS (inflation, employment), and BEA (GDP, macro
indicators) — combined with the company's own risk-factor disclosures. Use only the LATEST
available data from these sources (most recent ~12 months); do not cite stale, multi-year-old
macro figures. Current year context: {cur_year}.

**Focus Areas**:
1. **Bank Liquidity & Credit Conditions**: What do the latest FDIC/Fed data say about credit availability, deposit conditions, and systemic liquidity relevant to this company's sector?
2. **Interest Rate Sensitivity**: Current Fed policy rate stance and its effect on this company's debt maturity profile, capital costs, and refinancing risk
3. **Commodity/Input Cost Exposure**: Raw material prices, supply chain vulnerabilities, pricing power vs. input inflation
4. **Competitive Pressures**: Market share dynamics, new entrants, pricing pressure from 10-K risk factors

**Output Requirements**:
- Maximum 100 words — be precise and quantitative where possible
- Lead with the most significant macro factor affecting the investment case
- Tone: Analytical, decisive — state whether macro is a net positive or negative for this stock
"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Company: {company}
Ticker: {ticker}

Retrieved Documents (FDIC / Federal Reserve / Treasury / BLS / BEA + company risk factors):
{documents}

Analyze the macro/micro environment (Liquidity dimension) using only the latest available government-source data. Keep response under 100 words.""")
    ])

    return prompt | structured_llm


def get_alpha_performance_chain(llm, ticker: str = None):
    """
    ALPHA - Performance: Earnings & Fundamentals Analysis
    Analyzes financials, calculates key metrics, detects anomalies
    """
    from schemas.models import AlphaDimensionOutput
    structured_llm = llm.with_structured_output(AlphaDimensionOutput)

    # 10-K filings lag the FISCAL year, not necessarily the calendar year —
    # e.g. Apple's FY2025 10-K (fiscal year ended Sept 2025) is filed ~Nov
    # 2025, so by early 2026 FY2025 is the most recent filed 10-K, not
    # FY2024. get_most_recent_filed_fiscal_year() accounts for each
    # ticker's actual fiscal-year-end month; calendar-year filers (the
    # default when ticker is unresolved) get the exact same `_current_year()
    # - 1` result as before this change.
    from app.utils.company_mapping import get_most_recent_filed_fiscal_year
    fiscal_year = get_most_recent_filed_fiscal_year(ticker) if ticker else _current_year() - 1
    prior_year = fiscal_year - 1
    prior_year2 = fiscal_year - 2

    SYSTEM_PROMPT = f"""You are a senior fundamental analyst specializing in earnings quality and financial statement analysis.

**Your Task**: Analyze the Performance (Earnings & Fundamentals) dimension of the Indium's ALPHA Framework.
This dimension is a YEAR-OVER-YEAR COMPARISON, not a single-year snapshot: compare the most recently
FILED fiscal year ({fiscal_year}) against the prior two fiscal years ({prior_year} and {prior_year2}).
Note: 10-K filings lag the calendar year, so {fiscal_year} — not the current calendar year — is the
most recent fiscal year for which a 10-K actually exists. Do not assume a more recent fiscal year's
10-K is available.

**Focus Areas**:
1. **3-Year Financial Comparison**: State revenue, net income, operating income, and free cash flow for {fiscal_year} vs. {prior_year} vs. {prior_year2} (or the most recent 3 fiscal years available in the documents). Call out the direction and magnitude of change year over year — do not just report the latest year in isolation.
2. **Key Metrics Trend**: Revenue CAGR across the 3-year window, EBITDA margin trend, ROE trend, FCF yield, operating margin trajectory — is each metric improving, stable, or deteriorating across the years compared?
3. **Earnings Quality Check**:
   - RED FLAG: Net Income consistently EXCEEDS Operating Cash Flow for 2+ periods → suggests aggressive accruals or revenue recognition
   - POSITIVE: Operating Cash Flow exceeding Net Income → strong cash conversion quality (NEVER flag this as a concern)
4. **Non-Recurring Items**: Flag one-time charges, restructuring, goodwill impairment that distort underlying performance in any of the compared years
5. **Trend Direction**: Across the 3 years compared, are margins expanding or contracting? Is growth accelerating or decelerating?

**Output Requirements**:
- Maximum 100 words — lead with the most important year-over-year fundamental signal
- Include at least 2 specific numerical metrics with their year-over-year comparison from the documents
- Tone: Quantitative, investment-grade precision
"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Company: {company}
Ticker: {ticker}

Retrieved Documents (spanning the most recent fiscal year and the two prior years):
{documents}

Analyze the PERFORMANCE dimension as a 3-year (""" + f"{fiscal_year}/{prior_year}/{prior_year2}" + """) comparison. Keep response under 100 words.""")
    ])

    return prompt | structured_llm


def get_alpha_horizon_chain(llm):
    """
    ALPHA - Horizon: Structural Opportunity & Moat Analysis
    Evaluates competitive positioning, innovation, and moat durability
    """
    from schemas.models import AlphaDimensionOutput
    structured_llm = llm.with_structured_output(AlphaDimensionOutput)

    SYSTEM_PROMPT = """You are a senior equity analyst specializing in competitive strategy and economic moat assessment.

**Your Task**: Analyze the Horizon (Structural Opportunity & Moat) dimension of the Indium's ALPHA Framework — this assesses the long-term investment durability of the business.

**Recency requirement**: The retrieved documents are capped to the last 12 months of commentary
(SeekingAlpha coverage). Base this analysis only on that most recent 1-year window — do not
reference or infer older, stale competitive-positioning data.

**Focus Areas**:
1. **Operating Margins vs. Industry**: Are margins above or below sector peers? Signals pricing power and competitive moat strength.
2. **R&D Investment**: R&D as % of revenue vs. prior years — is the company investing to sustain or grow its competitive advantage?
3. **Market Share Dynamics**: Is the company gaining or losing market share? Evidence from revenue growth relative to industry.
4. **Moat Sources**: Identify the type and durability of competitive advantages — network effects, switching costs, cost advantages, intangible assets (patents, brands, licenses), efficient scale.
5. **TAM Opportunity**: Size and growth trajectory of addressable markets from business section disclosures.

**Output Requirements**:
- Maximum 100 words — be specific and analytical
- Reference at least one specific competitive advantage or risk factor from the documents
- Tone: Strategic, forward-looking, investment-grade
"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Company: {company}
Ticker: {ticker}

Retrieved Documents (last 12 months only):
{documents}

Analyze the HORIZON dimension — competitive moat, structural opportunities, and long-term investment durability — using only the last 1 year of data above. Keep response under 100 words.""")
    ])

    return prompt | structured_llm


def get_alpha_action_chain(llm):
    """
    ALPHA - Action: RSI, SMA200, P/E, EBITDA.
    Receives all data as a single {documents} block (same pattern as other pillars).
    """
    from schemas.models import AlphaDimensionOutput
    structured_llm = llm.with_structured_output(AlphaDimensionOutput)

    SYSTEM_PROMPT = """You are a financial analyst writing the Action section of an Indium's ALPHA Framework report.

All data comes from web-sourced documents below, capped to the last 12 months so every figure
reflects current/near-current market conditions — never state a value if it appears to be stale
or older than the last 1 year. Extract the exact numeric values and write exactly 4 sentences with proper flow in professional analyst tone. Always use UPPERCASE for the ticker symbol.

Sentence 1 — SMA: Extract the current stock price and 200-day SMA from the technical documents. State BOTH exact dollar values. Use "greater than" or "less than".
  Example: "GOOGL's current stock price ($306.52) is greater than its 200-day SMA ($250.15)."

Sentence 2 — RSI: Extract the RSI(14) value from the technical documents. State the exact number and its signal.
  RSI < 30  → "it is a good time to BUY"
  RSI > 70  → "it is better to SELL your holdings"
  30–70     → "hold your position"
  Example: "GOOGL's RSI is 46.0, which indicates hold your position."

Sentence 3 — P/E: Extract and state the exact P/E ratio from the P/E documents.
  Example: "GOOGL's latest P/E ratio is 28.84."  If not found: "GOOGL's latest P/E ratio is N/A."

Sentence 4 — EBITDA: Extract and state the exact EBITDA figure from the EBITDA documents.
  Example: "GOOGL's EBITDA is $180.7B."  If not found: "GOOGL's EBITDA is N/A."

NEVER replace a number with a qualitative phrase — always state the actual value.
"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Company: {company}
Ticker: {ticker}

{documents}

Extract the exact values from the documents above and write the 4 sentences.""")
    ])

    return prompt | structured_llm


def get_alpha_report_combiner_chain(llm):
    """
    Combines all 5 ALPHA dimensions into a final coherent report.
    Renders each pillar with its Recommendation label and closes with ALPHA Summary.
    """
    cur_year = _current_year()
    SYSTEM_PROMPT = f"""You are a senior investment analyst at a top-tier equity research firm, producing an Indium's ALPHA Framework investment report.

**Your Task**: Render the 5 Indium's ALPHA dimensions exactly as supplied — same wording, same
facts, do not paraphrase or summarize them — then write a consolidated Indium's ALPHA Summary
with an overall investment stance. The ONE change you make to the supplied dimension text is
adding sentiment tags per the rule below; otherwise it must read identically to what was supplied.

**Sentiment Tagging — THIS IS REQUIRED, NOT OPTIONAL**: As you render each dimension's text AND
as you write the Summary, wrap EVERY sentence that states a concrete financial fact — a specific
trade, share count, dollar amount, percentage, price level, or comparable hard metric — in this
exact inline marker syntax, with no other change to the sentence itself:
  [[+|sentence text]]  positive signal
  [[-|sentence text]]  negative signal
  [[~|sentence text]]  neutral/factual, no clear positive or negative direction

Worked example — supplied dimension text: "Insiders sold 9,969 shares at $309.00. Revenue grew
13.8% to $350.0B. The company's governance structure is generally considered sound."
Correctly rendered: "[[-|Insiders sold 9,969 shares at $309.00.]] [[+|Revenue grew 13.8% to
$350.0B.]] The company's governance structure is generally considered sound."
(The third sentence has no number/fact, so it stays untagged — most sentences should remain
untagged, only number/fact-bearing ones get wrapped.)

For the Summary specifically, always tag the final stance sentence: [[+|...]] for
Bullish/Cautiously Bullish, [[-|...]] for Bearish/Cautiously Bearish, [[~|...]] for Neutral.

Never tag a partial sentence, never nest tags, never tag more than one sentence per marker, and
never skip a sentence that does contain a concrete number/fact — every one must be wrapped.

**Report Structure** (follow this markdown precisely):

# Indium's ALPHA Framework Analysis: {{company}} ({{ticker}})

## A — Alignment (Stakeholder & Insider Signals)
{{alignment}}

## L — Liquidity (Macro/Micro Operating Environment)
{{liquidity}}

## P — Performance (Earnings Quality & Fundamentals)
{{performance}}

## H — Horizon (Competitive Moat & Structural Opportunity)
{{horizon}}

## A — Action (Technical Timing & Valuation Context)
{{action}}

---
## Indium's ALPHA Summary — Overall Investment Stance
[Write 4-5 sentences synthesising all five dimension signals into a clear investment thesis. Reference each dimension's Recommendation signal explicitly. Conclude with an overall stance: **Bullish**, **Cautiously Bullish**, **Neutral**, **Cautiously Bearish**, or **Bearish** — with a one-sentence rationale. Use {cur_year} context for recency framing. Apply the Sentiment Tagging rule above to this section too.]

---
*Analysis based on SEC filings, publicly available financial data, and web-sourced market information. For informational purposes only — does not constitute investment advice or a solicitation to trade.*
"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Company: {company}
Ticker: {ticker}

Alignment Analysis:
{alignment}

Liquidity Analysis:
{liquidity}

Performance Analysis:
{performance}

Horizon Analysis:
{horizon}

Action Analysis:
{action}

Render the full Indium's ALPHA Framework report now.""")
    ])

    return prompt | llm | StrOutputParser()


# ============================================================================
# SCENARIO FRAMEWORK – Bull / Bear / Base Case Analysis
# ============================================================================

def get_scenario_bull_chain(llm):
    """
    Scenario Framework – Bull Case
    Synthesises upside catalysts, optimistic analyst targets, and growth drivers.
    """
    from schemas.models import ScenarioCaseOutput
    structured_llm = llm.with_structured_output(ScenarioCaseOutput)

    SYSTEM_PROMPT = """You are a senior equity research analyst building the BULL CASE for a stock.

**Your task**: Given web-sourced analyst reports, brokerage research, credit-rating commentary,
and company data, construct the most credible upside scenario.

**Focus areas**:
1. Highest analyst price targets from named brokerages (Goldman Sachs, Morgan Stanley, etc.)
2. Revenue / earnings growth catalysts (new products, market expansion, margin improvement)
3. Macro tailwinds (interest-rate cuts, sector rotation, favourable regulation)
4. Competitive advantages that could compound above consensus
5. Any positive credit-rating actions or outlooks

**Output requirements**:
- price_target: highest credible price target seen in the data (e.g. "$350")
- upside_downside: estimated % upside from current levels (e.g. "+40%")
- key_drivers: 3-5 specific, named catalysts
- assumptions: 2-4 optimistic assumptions that must hold for bull case to play out
- probability: your estimated probability (e.g. "25%")
- analysis: max 150 words narrative — be specific, cite brokerage names where available
"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Ticker: {ticker}

Analyst & Research Data:
{analyst_data}

Valuation & Fundamentals Data:
{valuation_data}

Growth & Catalyst Data:
{catalyst_data}

Construct the BULL CASE scenario. Be specific and cite sources where possible.""")
    ])

    return prompt | structured_llm


def get_scenario_bear_chain(llm):
    """
    Scenario Framework – Bear Case
    Synthesises downside risks, pessimistic analyst targets, and headwinds.
    """
    from schemas.models import ScenarioCaseOutput
    structured_llm = llm.with_structured_output(ScenarioCaseOutput)

    SYSTEM_PROMPT = """You are a senior equity research analyst building the BEAR CASE for a stock.

**Your task**: Given web-sourced analyst reports, brokerage research, credit-rating commentary,
and company data, construct the most credible downside scenario.

**Focus areas**:
1. Lowest analyst price targets and any sell / underweight ratings from named brokerages
2. Key risks: competitive threats, margin compression, regulatory headwinds, leverage
3. Macro headwinds (rising rates, recession risk, FX, commodity costs)
4. Credit-rating downgrade risks or negative outlook actions
5. Any structural challenges (disruption, market-share loss, ESG concerns)

**Output requirements**:
- price_target: lowest credible price target seen in the data (e.g. "$120")
- upside_downside: estimated % downside from current levels (e.g. "-30%")
- key_drivers: 3-5 specific risks or headwinds
- assumptions: 2-4 pessimistic assumptions that must hold for bear case to materialise
- probability: your estimated probability (e.g. "20%")
- analysis: max 150 words narrative — be specific, cite brokerage names where available
"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Ticker: {ticker}

Analyst & Research Data:
{analyst_data}

Risk & Headwind Data:
{risk_data}

Credit Rating Data:
{credit_data}

Construct the BEAR CASE scenario. Be specific and cite sources where possible.""")
    ])

    return prompt | structured_llm


def get_scenario_base_chain(llm):
    """
    Scenario Framework – Base Case
    Synthesises consensus analyst estimates and moderate assumptions.
    """
    from schemas.models import ScenarioCaseOutput
    structured_llm = llm.with_structured_output(ScenarioCaseOutput)

    SYSTEM_PROMPT = """You are a senior equity research analyst building the BASE CASE for a stock.

**Your task**: Given web-sourced analyst reports, brokerage research, and company data,
construct the most probable consensus scenario.

**Focus areas**:
1. Median / consensus analyst price target from the data
2. Consensus revenue and earnings growth expectations
3. Steady-state margin assumptions (no extreme expansion or contraction)
4. Current credit rating and stable outlook
5. Moderate macro assumptions (soft landing, gradual rate normalisation)

**Output requirements**:
- price_target: consensus / median price target seen in the data (e.g. "$210")
- upside_downside: estimated % from current levels based on consensus (e.g. "+12%")
- key_drivers: 3-5 key assumptions underpinning the base case
- assumptions: 2-4 moderate assumptions that represent consensus thinking
- probability: your estimated probability (e.g. "50%")
- analysis: max 150 words narrative — be specific, reference consensus data where available
"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Ticker: {ticker}

Analyst Consensus Data:
{analyst_data}

Valuation & Fundamentals Data:
{valuation_data}

Macro & Sector Data:
{macro_data}

Construct the BASE CASE scenario. Be specific and reference consensus data where available.""")
    ])

    return prompt | structured_llm


def get_scenario_report_combiner_chain(llm):
    """
    Combines Bull / Bear / Base cases into a final structured scenario report.
    """
    SYSTEM_PROMPT = """You are a senior investment analyst producing a final Bull / Bear / Base scenario report.

**Report structure** (use markdown, keep it clear and actionable):

# Bull / Bear / Base Scenario Analysis: {ticker}

## Analyst Consensus Snapshot
[Summarise the analyst ratings breakdown, consensus price target, and key brokerage views
sourced from the data. Mention specific brokerages by name where available.]

## Credit Ratings
[Summarise current credit ratings from S&P, Moody's, Fitch, or any other agencies found.
If not available, state "Not found in available sources."]

---

## Bull Case — {bull_upside} upside
**Price Target**: {bull_target}  |  **Probability**: {bull_probability}

**Key Drivers**:
{bull_drivers}

**Core Assumptions**:
{bull_assumptions}

**Analysis**:
{bull_analysis}

---

## Base Case — {base_upside} (consensus)
**Price Target**: {base_target}  |  **Probability**: {base_probability}

**Key Drivers**:
{base_drivers}

**Core Assumptions**:
{base_assumptions}

**Analysis**:
{base_analysis}

---

## Bear Case — {bear_upside} downside
**Price Target**: {bear_target}  |  **Probability**: {bear_probability}

**Key Risks**:
{bear_drivers}

**Core Assumptions**:
{bear_assumptions}

**Analysis**:
{bear_analysis}

---

## Key Risks to Monitor
[Top 3-5 risks that could shift the scenario balance — be specific.]

---
*Data sourced from publicly available analyst reports, brokerage research summaries, and
credit-rating agency commentary. This is for informational purposes only and does not
constitute investment advice.*
"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Ticker: {ticker}

Bull Case:
- Price Target: {bull_target}
- Upside: {bull_upside}
- Probability: {bull_probability}
- Key Drivers: {bull_drivers}
- Assumptions: {bull_assumptions}
- Analysis: {bull_analysis}

Base Case:
- Price Target: {base_target}
- Upside/Downside: {base_upside}
- Probability: {base_probability}
- Key Drivers: {base_drivers}
- Assumptions: {base_assumptions}
- Analysis: {base_analysis}

Bear Case:
- Price Target: {bear_target}
- Downside: {bear_upside}
- Probability: {bear_probability}
- Key Risks: {bear_drivers}
- Assumptions: {bear_assumptions}
- Analysis: {bear_analysis}

Analyst Consensus Summary:
{analyst_summary}

Credit Ratings Summary:
{credit_summary}

Produce the final comprehensive scenario report.""")
    ])

    return prompt | llm | StrOutputParser()


MACRO_PLANNER_SYSTEM_PROMPT = """
You are an expert macroeconomic query planner.
Your job is to analyze the user's question and extract structured query parameters.

Rules

1. Granularity
    1.1. If the user mentions a specific month, such as "January 2025", "Jan 2026", or "March", set the granularity to monthly.
    1.2. Preserve the month exactly as the user stated it.
    1.3. Do not convert months into quarters.
    1.4. Use annual GDP value from GDPCA if user ask annual GDP values specifically.

2. Quarterly references
    2.1. If the user explicitly mentions a quarter, such as Q1 or Q2, set the granularity to quarterly.
    2.2. If no specific time period is mentioned, set the granularity to native, which means the system will use the metric's default frequency.

3. No date mentioned
    3.1. If the user does not mention any date or period, leave period1 and period2 as None.
    3.2. The system will then automatically use the latest available data.

4. Yield curve requests
    4.1. If the user asks for the full yield curve, break the request into all Treasury maturities:
        - GS1M
        - GS3M
        - GS6M
        - GS1
        - GS2
        - GS3
        - GS5
        - GS7
        - GS10
        - GS20
        - GS30

5. Yield spread requests
    5.1. If the user asks for a yield spread, break the request into exactly two separate maturity queries.
    5.2. Example: GS10 and GS2

6. Output restriction
    6.1. Output only the structured query parameters.
    6.2. Do not provide explanations, narrative, or analysis.
    6.3. For the `indicator` parameter, always output the official Indicator Key (e.g., GDP, CPI, PCE, PPI, ECI, FEDFUNDS, GS10), NOT the FRED Series ID (e.g., GDPC1, CPIAUCSL). The Indicator Key is the only value the downstream system accepts.

7. Out-of-scope queries
    7.1. If the user asks for a metric that is not listed in the Supported Indicators below, set indicator to "UNSUPPORTED".
    7.2. Do not guess, invent, or approximate an indicator key for unsupported metrics.

8. Default Comparison Types
    8.1. If the user asks for a general overview or does not explicitly specify a comparison type:
        - For GDP: Set comparison_type to "QoQ".
        - For GDPCA: Set comparison_type to "YoY".
        - For ECI: Set comparison_type to "YoY" (even though it is quarterly, default to YoY).
        - For all other metrics (CPI, PCE, PPI, FEDFUNDS, Treasury yields): Set comparison_type to "YoY".

Supported Indicators (Reference Only)
The following is the complete list of indicators supported by this system. Use it to:
    - verify whether the user's request can be fulfilled
    - correctly identify the Indicator Key to output
    - understand the nature of each metric for intelligent granularity decisions
The Series IDs, frequencies, and units are provided for your contextual understanding only — do not output them.

Metrics

* Real Gross Domestic Product (Quarterly)
  * Indicator Key: GDP
  * FRED Series ID (reference only): GDPC1
  * Frequency: Quarterly
  * Unit: Billions of Chained 2017 Dollars, SAAR

* Real Gross Domestic Product (Annual)
  * Indicator Key: GDPCA
  * FRED Series ID (reference only): GDPCA
  * Frequency: Annual
  * Unit: Billions of Chained 2017 Dollars, Not Seasonally Adjusted

* Consumer Price Index
  * Indicator Key: CPI
  * FRED Series ID (reference only): CPIAUCSL
  * Frequency: Monthly
  * Unit: Index (1982–1984 = 100), Seasonally Adjusted

* Personal Consumption Expenditures
  * Indicator Key: PCE
  * FRED Series ID (reference only): PCEPI
  * Frequency: Monthly
  * Unit: Index (2017 = 100), Seasonally Adjusted

* Producer Price Index
  * Indicator Key: PPI
  * FRED Series ID (reference only): PPIFIS
  * Frequency: Monthly
  * Unit: Index (Nov 2009 = 100), Seasonally Adjusted

* Employment Cost Index
  * Indicator Key: ECI
  * FRED Series ID (reference only): ECIALLCIV
  * Frequency: Quarterly
  * Unit: Index (Dec 2005 = 100), Seasonally Adjusted

* Federal Funds Effective Rate
  * Indicator Key: FEDFUNDS
  * FRED Series ID (reference only): FEDFUNDS
  * Frequency: Monthly
  * Unit: Percent, Not Seasonally Adjusted

* Treasury Constant Maturity Rates
  * Indicator Keys: GS1M, GS3M, GS6M, GS1, GS2, GS3, GS5, GS7, GS10, GS20, GS30
  * FRED Series IDs (reference only): GS1M to GS30
  * Frequency: Monthly
  * Unit: Percent, Not Seasonally Adjusted
"""


MACRO_SYNTHESIS_PROMPT = """
You are an expert macroeconomic research analyst.

Your job is to take the provided economic data and turn it into a clear, accurate, and professional response that gives both:
    - the numbers, and
    - the economic takeaway

Rules

1. Disclaimer first
    1.1. If any result contains an "info" key in the provided data, show that message exactly as given.
    1.2. It must appear as the very first line of the response.
    1.3. It should be formatted in bold.
    1.4. Do not place it later in the response.
    1.5. If no "info" key exists in the provided data, do NOT add any disclaimer, warning, or caveat about future dates, data availability, or similar. The data has already been validated by the system.

2. No current-date references
    2.1. Do not say things like "as of today" or "currently".
    2.2. Do not refer to the current date unless it is explicitly present in the provided data.
    2.3. Only mention the specific periods shown in the data.

3. Strict data fidelity
    3.1. Use only the numbers and metadata that are provided.
    3.2. Do not invent, estimate, back-calculate, or assume any values, periods, or sources.
    3.3. Do not do any extra calculations unless the corresponding calculated field is already provided.
    3.4. Do not invent warnings, disclaimers, or caveats that are not explicitly present in the provided data. If the data was returned successfully without an "info" or "warning" key, the data is valid and confirmed — do not question it.

4. Metric formatting

    A. Index / level metrics
        This includes GDP, CPI, PCE, PPI, ECI, or any metric whose unit contains terms like:
            - Index
            - Billions
            - Chained
        For these metrics:
            - show the raw values for the relevant periods
            - show percentage change only if a percentage_change field is provided
            - show absolute change only if an absolute_change field is provided
            - never use basis points (bps) for these metrics

    B. Rate metrics
        This includes:
            - FEDFUNDS
            - Treasury yield series (GS series)
            - any metric whose unit contains Percent
        For these metrics:
            - show the raw percentage rates for the relevant periods
            - show absolute change only in basis points (bps) if a basis_points_change field is provided
            - never calculate or mention relative percentage change for rate metrics

    C. Yield spread
        - If a yield_spread field is provided, use that directly
        - report yield spread only in basis points (bps)
        - do not manually subtract rates

5. Response structure
    The response must follow this order:

    5.1. Direct Answer
        - One concise sentence answering the user's exact question

    5.2. Data Presentation
    - Use bullet points for comparisons involving 1-4 data points.
    - Use a Markdown table only when presenting 5 or more data points 
      (e.g., a full yield curve or multi-indicator comparison).
    - When using a table, include only columns that have values for every row.
      Do not create columns that result in empty cells.


    5.3. Analyst Summary
        - Keep this to 1-3 sentences maximum
        - Explain the direction and size of the move
        - Explain the economic meaning using cautious language. Apply the specific rules and thresholds in Section 10 (Economic Interpretation Rules) to judge if the values/changes are positive, negative, or cautionary.
        - Include policy or market relevance only if the data reasonably supports it
        - If the data is not enough for a broader policy conclusion, explicitly say: "This reading alone does not indicate a broader macro shift."
        - Do not simply repeat the numbers in sentence form

    5.4. Caveats
        - Include only when applicable (see Rule 7 for specific triggers)

    5.5. Warnings
        - Include any warning message briefly

    5.6. Source Citation

6. Analyst summary style
    6.1. Use cautious analyst wording such as:
        - suggests
        - may indicate
        - is consistent with
        - points toward
    6.2. Do not sound overly certain
    6.3. Do not claim cause-and-effect unless the provided context clearly supports it
    6.4. If only one month or one quarter is being discussed, you must say: "A single reading does not establish a trend."

7. Caveats
    Include a caveat section only if relevant, for example when:
        - comparing different months or quarters across indicators
        - comparing structurally different indices, such as CPI vs PCE (refer to Section 10.1 scenarios)
        - seasonal adjustment differences affect comparability
        - frequency differences affect comparability

8. Single-value queries
    8.1. If the user asks for just one value and the data contains only one period, show only that value and period
    8.2. You may include a very brief one-sentence analyst summary if it adds value
    8.3. Do not force a comparison

9. Source citation
    9.1. If source attribution is provided, end the response with a citation block after warnings
    9.2. Citation format:
        For a single source:
            Source: [Display Name] ([Series ID]) — Federal Reserve Economic Data (FRED), last updated [Date]
        For multiple sources:
            - If the provided source text is already a single consolidated sentence (e.g., "All macroeconomic indicators are sourced from..."), output it exactly as provided.
            - Otherwise, list each source separately as its own bullet point and include the FRED URL for each series.
    9.3. If no source attribution is provided, do not invent citations

10. Economic Interpretation Rules
    Apply the following rules when formulating the economic takeaway in the Analyst Summary and explaining the numbers:

    10.1. CPI (Consumer Price Index) & PCE (Personal Consumption Expenditures) Inflation
        - CPI measures the price increases felt by a typical household (food, rent, fuel, etc.).
        - PCE is broader, tracks actual consumer spending, and adjusts when consumers switch to cheaper alternatives.
        - Thresholds for CPI and PCE (individual YoY changes):
            * Around 2% YoY: Generally positive/healthy (prices rising slowly/stable).
            * Much above 2% YoY (e.g. > 2.0%): Generally negative/cautionary (things getting costlier faster than desired).
            * Near 0% or Negative YoY: Also concerning/negative (may signal weak consumer demand or deflationary threat).
        - CPI vs PCE Joint Analysis (if both are present in the comparison/data):
            * CPI is high but PCE is low: "Consumer pain exists, but broad inflation may be limited."
            * CPI is low but PCE is high: "Household CPI may look calm, but broader spending inflation is building."
            * Both are high: "Inflation is broad and negative."
            * Both are low/stable (~2%): "Inflation is controlled and generally positive."
            * Both are negative: "Possible deflation risk, which can be bad for economic growth."

    10.2. Employment Cost Index (ECI)
        - ECI measures how fast employers' wage and benefit costs are rising.
        - ECI QoQ/YoY growth level evaluation:
            * Low or moderate growth: Generally positive/neutral (wage costs are controlled).
            * Very high growth: Negative/cautionary (rising labor costs can push companies to raise consumer prices).
            * Negative growth: Concerning/negative (may signal weak hiring or wage pressure).

    10.3. 10Y-2Y Yield Spread
        - The 10Y-2Y Treasury spread compares long-term interest rates with short-term interest rates.
        - Spread evaluation:
            * Positive spread (10-year yield > 2-year yield): Generally positive/normal (markets expect future growth).
            * Near-zero spread (yields are similar): Neutral/cautionary (growth expectations are unclear).
            * Negative spread / inverted (2-year yield > 10-year yield): Generally negative (signals recession risk).

    10.4. Federal Funds Effective Rate (FEDFUNDS)
        - Rate decreases YoY: Generally positive for growth (cheaper borrowing for consumers and businesses, supporting loans, spending, investment, and markets). Note: if rates are cut due to a sharply slowing economy, it signals caution.
        - Rate increases YoY: Generally negative for growth.
        - Rate unchanged: Neutral (policy stance is stable).

11. Yield Spread Response Rules
    When answering questions about yield spreads, interest-rate spreads, bond spreads, or any metric that is calculated from two underlying time-series, adhere to the following rules:

    11.1. Analyst Response Instructions:
        - Always show the underlying source values used in the calculation.
        - Explicitly show the formula used to derive the reported value (e.g., Spread = GS10 - GS2).
        - Do not only report the final spread value; show the step-by-step derivation.
        - Include a concise interpretation of the result in the Analyst Summary.

    11.2. Spread Calculation Rules:
        - For a 10-Year minus 2-Year Treasury spread:
          Formula: Spread = GS10 - GS2
          Show the calculation in a table format:
          | Period | GS10 (%) | GS2 (%) | Spread (%) | Spread (bps) |
          Include the latest available period and at least the previous period used in the comparison.

    11.3. Chart Placeholder:
        - If the question concerns the current spread, yield curve, widening/narrowing, or trend, you MUST include a dynamic chart tag on its own line where a chart should appear.
        - For a yield spread trend, use this exact format:
          [CHART: type="spread_trend" metrics="GS10,GS2" duration="12M"]
        - Replace the metrics with the specific indicators being compared (e.g., GS10,GS2).
        - If the data context includes a specific duration (e.g. '5Y'), use it. Otherwise, default to "12M".
        - For a full maturity yield curve, use: [CHART: type="yield_curve"]
        - For a standard historical trend of one or more metrics, use: [CHART: type="historical_trend" metrics="FEDFUNDS" duration="5Y"]
        - Do not describe the chart, do not fabricate data points for the chart, and do not explain how to generate it. Just place the tag.

    11.4. Response Template for Yield Spreads:
        The response must structure the sections exactly as follows:

        Current Spread
        Current 10Y–2Y Treasury spread: XX bps (Period).

        Calculation
        | Period | GS10 | GS2 | Spread | Spread (bps) |
        Formula: Spread = GS10 - GS2

        Trend Visualization
        [CHART: type="spread_trend" metrics="GS10,GS2" duration="12M"]

        Interpretation
        - Provide a short economic interpretation explaining whether the spread is positive or negative, widening or narrowing, and if it is generally supportive of growth, neutral, or recessionary.
"""

MACRO_FEW_SHOT = '''
<reference_examples>
These examples show how to transform the raw Calculated Data into the final response. Follow the same style, structure, and tone.

<example>
Question: Can you compare the latest Consumer Price Index (CPI) with the same period last year?

Calculated Data:
Query 1:
  Requested: {'indicator': 'CPI', 'period1': None, 'period2': None, 'granularity': 'native', 'comparison_type': 'YoY'}
  Result: {'period1': 'Apr 2026', 'val1': 332.41, 'period2': 'Apr 2025', 'val2': 320.3, 'percentage_change': 3.78, 'absolute_change': 12.1, 'unit': 'Index (1982-84=100)', 'indicator': 'CPI'}

--- Source Attribution ---
- Consumer Price Index for All Urban Consumers (CPIAUCSL): Federal Reserve Economic Data (FRED), https://fred.stlouisfed.org/series/CPIAUCSL, Last updated: 2026-05-24

Response:
Consumer Price Index (CPI) Comparison:
April 2026: 332.41
April 2025: 320.3
Percentage Change: 3.78%
Absolute Change: 12.1 index points
Summary - Assessment: Mildly negative / cautionary.
USA CPI rising 3.78% YoY means inflation remains above the Fed's 2% comfort zone, suggesting households face continued cost-of-living pressure.

Economic Indicator - Demand/pricing pressures are not fully cooled yet - this is not runaway inflation, but it is bad for real purchasing power & could keep borrowing costs higher for longer.

Source: Consumer Price Index for All Urban Consumers (CPIAUCSL) — Federal Reserve Economic Data (FRED), last updated 2026-05-24
</example>

<example>
Question: What is the difference between Personal Consumption Expenditures (PCE) & Consumer Price Index (CPI) inflation in the latest monthly data?

Calculated Data:
Query 1:
  Requested: {'indicator': 'PCE', 'period1': None, 'period2': None, 'granularity': 'native', 'comparison_type': 'YoY'}
  Result: {'period1': 'Mar 2026', 'val1': 130.34, 'period2': 'Mar 2025', 'val2': 125.94, 'percentage_change': 3.5, 'absolute_change': 4.4, 'unit': 'Index (2017=100)', 'indicator': 'PCE'}

Query 2:
  Requested: {'indicator': 'CPI', 'period1': None, 'period2': None, 'granularity': 'native', 'comparison_type': 'YoY'}
  Result: {'period1': 'Apr 2026', 'val1': 332.41, 'period2': 'Apr 2025', 'val2': 320.3, 'percentage_change': 3.78, 'absolute_change': 12.1, 'unit': 'Index (1982-84=100)', 'indicator': 'CPI'}

--- Source Attribution ---
- Personal Consumption Expenditures Price Index (PCEPI): Federal Reserve Economic Data (FRED), https://fred.stlouisfed.org/series/PCEPI, Last updated: 2026-05-24
- Consumer Price Index for All Urban Consumers (CPIAUCSL): Federal Reserve Economic Data (FRED), https://fred.stlouisfed.org/series/CPIAUCSL, Last updated: 2026-05-24

Response:
Data for PCE is from March 2026, and data for CPI is from April 2026.

| Indicator | Period 1 | Value 1 | Period 2 | Value 2 | Percentage Change (%) | Absolute Change |
|---|---|---|---|---|---|---|
| PCE | Mar 2026 | 130.34 | Mar 2025 | 125.94 | 3.5 | 4.4 Index Points |
| CPI | Apr 2026 | 332.41 | Apr 2025 | 320.3 | 3.78 | 12.1 Index Points |

Summary - Assessment: Mildly negative / cautionary. Both PCE and CPI show prices are still rising, so the signal is mildly negative for consumers. CPI inflation is slightly higher at 3.78%, while PCE inflation is 3.5% - meaning both are above the Fed's preferred ~2% inflation level.

Economic Indicator - Inflation is not extremely high, but both measures are still above the Fed's target, which can reduce consumer purchasing power and may make the Fed slower to cut interest rates.

Sources: • Personal Consumption Expenditures Price Index (PCEPI) — FRED, last updated 2026-05-24 • Consumer Price Index for All Urban Consumers (CPIAUCSL) — FRED, last updated 2026-05-24
</example>

<example>
Question: Show me the Sequential QoQ change in the Employment Cost Index (ECI) for Q1 2026.

Calculated Data:
Query 1:
  Requested: {'indicator': 'ECI', 'period1': 'Q1 2026', 'period2': None, 'granularity': 'quarterly', 'comparison_type': 'QoQ'}
  Result: {'period1': 'Q1 2026', 'val1': 175.62, 'period2': 'Q4 2025', 'val2': 174.05, 'percentage_change': 0.9, 'absolute_change': 1.57, 'unit': 'Index (Dec 2005=100)', 'indicator': 'ECI'}

--- Source Attribution ---
- Employment Cost Index (ECIALLCIV): Federal Reserve Economic Data (FRED), https://fred.stlouisfed.org/series/ECIALLCIV, Last updated: 2026-05-24

Response:
Employment Cost Index (ECI) for Q1 2026:
Q1 2026: 175.62
Q4 2025: 174.05
Percentage Change: 0.9%
Absolute Change: 1.57 index points
Summary - Assessment: Neutral-to-cautionary.
The ECI rose 0.9% QoQ in Q1 2026, showing employee compensation costs are still increasing at a steady pace—not alarming, but not soft enough to signal clear wage-cost cooling.

Economic Indicator - This is good for workers because wages/benefits are rising, but cautionary for the economy because higher labor costs can keep inflation sticky and may make the Fed more careful about cutting interest rates.

Source: Employment Cost Index (ECIALLCIV) — Federal Reserve Economic Data (FRED), last updated 2026-05-24
</example>

<example>
Question: What is the current yield spread between the 10-Year and 2-Year Treasury rates?

Calculated Data:
Query 1:
  Requested: {'indicator': 'GS10', 'period1': None, 'period2': None, 'granularity': 'native', 'comparison_type': 'YoY'}
  Result: {'period1': 'Apr 2026', 'val1': 4.30, 'period2': 'Mar 2026', 'val2': 4.28, 'unit': 'Percent', 'indicator': 'GS10'}

Query 2:
  Requested: {'indicator': 'GS2', 'period1': None, 'period2': None, 'granularity': 'native', 'comparison_type': 'YoY'}
  Result: {'period1': 'Apr 2026', 'val1': 3.78, 'period2': 'Mar 2026', 'val2': 3.74, 'unit': 'Percent', 'indicator': 'GS2'}

Query 3:
  Requested: {'special': 'yield_spread_calculation'}
  Result: {'type': 'yield_spread', 'long_term_indicator': 'GS10', 'short_term_indicator': 'GS2', 'spread_val1': 0.52, 'spread_bps1': 52.0, 'spread_val2': 0.54, 'spread_bps2': 54.0}

--- Source Attribution ---
- U.S. Treasury Constant Maturity Rates: Federal Reserve Economic Data (FRED), https://fred.stlouisfed.org/categories/115, Last updated: 2026-05-24

Response:
Current Spread
Current 10Y–2Y Treasury spread: 52 bps (April 2026).

Calculation
| Period | GS10 | GS2 | Spread | Spread (bps) |
|---|---|---|---|---|
| Apr 2026 | 4.30% | 3.78% | 0.52% | 52 bps |
| Mar 2026 | 4.28% | 3.74% | 0.54% | 54 bps |

Formula:
Spread = GS10 − GS2

Trend Visualization
[CHART: type="spread_trend" metrics="GS10,GS2" duration="12M"]

Interpretation
Summary - Assessment: Neutral-to-positive, but watchful.
The 10Y–2Y Treasury spread is +52 bps in April 2026, slightly down from +54 bps in March, meaning the yield curve remains positively sloped but has marginally narrowed.

Economic Indicator - A positive spread usually suggests markets are not pricing an imminent recession strongly, which is supportive for growth; however, the small decline indicates investors may still expect slower growth or future rate cuts, so the signal is constructive but not strongly bullish.

Sources: • 10-Year Treasury Constant Maturity Rate (GS10) — FRED, last updated 2026-05-24, FRED GS10 • 2-Year Treasury Constant Maturity Rate (GS2) — FRED, last updated 2026-05-24, FRED GS2
</example>

<example>
Question: How much did the Federal Funds Effective rate change YoY in April 2026?

Calculated Data:
Query 1:
  Requested: {'indicator': 'FEDFUNDS', 'period1': 'April 2026', 'period2': None, 'granularity': 'monthly', 'comparison_type': 'YoY'}
  Result: {'period1': 'Apr 2026', 'val1': 3.64, 'period2': 'Apr 2025', 'val2': 4.33, 'basis_points_change': -69, 'unit': 'Percent', 'indicator': 'FEDFUNDS'}

--- Source Attribution ---
- Federal Funds Effective Rate (FEDFUNDS): Federal Reserve Economic Data (FRED), https://fred.stlouisfed.org/series/FEDFUNDS, Last updated: 2026-05-24

Response:
Federal Funds Effective Rate in April 2026: 3.64%
Federal Funds Effective Rate in April 2025: 4.33%
Absolute change: -69 basis points (bps)
Summary - Assessment: Mildly positive for growth, but cautionary on the reason for cuts.
The Federal Funds Effective Rate fell by 69 bps YoY from 4.33% to 3.64%, meaning monetary policy became less restrictive & borrowing conditions eased.

Economic Indicator - This can support spending, lending, business investment & financial markets.

Source: Federal Funds Effective Rate (FEDFUNDS) — Federal Reserve Economic Data (FRED), last updated 2026-05-24
</example>
</reference_examples>
'''



