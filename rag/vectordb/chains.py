"This module contains all the chains that will be usefull in building the nodes of the graph"

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from schemas.models import (RouteQuery, GradeDocuments, 
                                        GradeHallucinations, ExtractCompany, GradeAnswer,
                                        DocumentSummaryStrategy,
                                        DocumentSufficiencyDecision, FinancialCalculationAnalysis,
                                        UniversalSubQueryAnalysis, FinancialAnalystGrade,
                                        GapAnalysisResult, StructuredFinancialData)


def get_question_router_chain(vectorstore_source_files, llm_router):
    structured_llm_router = llm_router.with_structured_output(RouteQuery)

    SYSTEM_PROMPT = f"""You are an expert at routing user questions to the appropriate data source.

**Important Context:**
- Current year: 2025

**Available Data Sources:**

1. **vectorstore**: Contains financial documents (10-K reports, annual reports, financial statements) for companies: {vectorstore_source_files}
   - Contains RECENT 10-K reports including 2023 and 2024 data
   - 10-K reports include multi-year comparisons (3-5 years of historical data)
   - Rich with financial data, balance sheets, income statements, cash flow statements
   
2. **web_search**: Live internet search ONLY when vectorstore cannot help
   - ONLY for real-time stock prices or today's breaking news
   - AVOID for any historical financial data (even recent years like 2023-2024)
   
3. **general**: For non-company, non-financial questions
   - General knowledge, definitions, greetings

**Routing Decision Logic:**

**CRITICAL: ALWAYS PREFER VECTORSTORE FOR COMPANY FINANCIAL QUESTIONS**
Our 10-K reports contain rich historical data including recent years!

**Step 1: Is this a company/financial question?**
- Does the question mention ANY company name OR ask about financial/business data?
- YES → Go to Step 2
- NO → Route to **general**

**Step 2: Route to vectorstore by DEFAULT for company questions**

Route to **vectorstore** (DEFAULT - try this first):
- ANY question about company financial data, metrics, or performance
- Questions about years: 2020, 2021, 2022, 2023, 2024
- Temporal comparisons: "2023 vs 2024", "revenue growth from 2023 to 2024"
- Year-over-year growth analysis (10-K reports have this!)
- Balance sheet, income statement, cash flow, segment data
- Multi-company comparisons
- **Even recent years like 2024** - we have 2024 10-K reports!

Route to **web_search** ONLY if:
- Explicitly asks for "today's stock price" or "current market price right now"
- Asks about breaking news from last few days: "what happened today"
- Real-time market data that changes minute-by-minute

**The Key Principle:**
- LLM should intelligently determine: "Do we likely have this in stored documents, or do we need to search the web?"
- If a specific year is mentioned → Likely in vectorstore (try there first)
- If asking for "latest" or "current" → Likely needs web search
- When in doubt for company questions → Try vectorstore first (most financial queries can be answered from reports)

**Examples:**

✓ Route to vectorstore (DEFAULT for company questions):
- "Get the consolidated balance sheet of meta for the year 2023"
- "Amazon's revenue in 2022"
- "Tell me about Tesla's financial performance"
- "Compare Google and Microsoft's income statements"
- "Compare Amazon and Meta's 2023 balance sheets"
- "Tesla vs Amazon revenue in 2022"
- "What was Apple's profit in 2020?"
- "Show me Nvidia's cash flow"
- "Google's revenue growth from 2023 to 2024" ← YES, vectorstore! (10-K has this)
- "What is Meta's 2024 revenue?" ← YES, vectorstore! (we have 2024 10-K)
- "Compare Amazon 2023 and 2024 balance sheets" ← YES, vectorstore! (comparative data)

✓ Route to web_search (ONLY for real-time data):
- "Current stock price of Amazon right now"
- "What's Amazon's stock price today?"
- "Tesla stock news from this morning"
- "What happened with Google stock today?"
- "Live market data for Microsoft"

✓ Route to general:
- "What is machine learning?"
- "How to become a trader?"
- "Hello, how are you?"
- "Define revenue"

**Critical Rule: DEFAULT to vectorstore for ANY company financial question. 10-K reports contain multi-year data including recent years (2023, 2024). Only use web_search for real-time stock prices or today's breaking news.**"""
    
    route_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            ("human", "{question}"),
        ]
    )
    question_router = route_prompt | structured_llm_router
    return question_router

def get_retrival_grader_chain(llm_grade_document):
    structured_llm_grader = llm_grade_document.with_structured_output(GradeDocuments)

    SYSTEM_PROMPT_GRADE = """You are a grader assessing the relevance of a retrieved document to a user question.  

        **Assessment Guidelines:**
        - A document is **relevant** if it contains information that helps answer the question, even if not perfectly direct.
        - For **multi-company questions** (e.g., "Compare Tesla and Amazon"), accept documents with data for the mentioned companies.
        - For **financial questions**, accept documents with financial figures or statements related to the query.
        - Mark as **not relevant** only if the document is completely unrelated or provides no useful information.

        **Special Handling Cases:**
        - **Image documents** starting with "This is an image with the caption:" - accept if the caption relates to the topic.
        - **Company name flexibility**: Accept variations.
        - **Cross-referencing scenarios**: Include documents with unique information from different sources.
        - **Financial data**: Accept if it includes numbers or metrics, even if general.

        **Scoring System:**
        - **Return "Yes"** if the document provides useful information for the question.
        - **Return "No"** only if completely unrelated.
        """

    grade_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT_GRADE),
            ("human", "Retrieved document: \n\n {document} \n\n User question: {question}"),
        ]
    )

    retrieval_grader = grade_prompt | structured_llm_grader
    return retrieval_grader
def get_rag_chain(llm_generate):
    prompt = """You are a Financial AI Assistant specialized in analyzing financial documents and providing accurate, data-driven answers.

**DOCUMENT HANDLING:**
The documents provided may come from:
1. **Vector Database**: Stored financial reports (10-K, annual reports, balance sheets)
2. **Web Search Results**: Real-time data from web searches, which may include:
   - Summaries of financial documents
   - News articles with financial data
   - Direct excerpts from company reports

**CRITICAL INSTRUCTION FOR WEB SEARCH RESULTS:**
When documents contain web search results (you'll see "Source:" headers with URLs):
- EXTRACT ALL numerical financial data mentioned (look for patterns like "Current Assets: $X", "Total: $Y")
- PRESENT the data in a structured format
- CITE the source URLs for verification
- If specific data is found in the web results, INCLUDE IT IN YOUR ANSWER
- NEVER say "data not available" if ANY relevant numbers appear in the documents
- For calculation queries, SEARCH THOROUGHLY through ALL documents before concluding data is missing

**BALANCE SHEET / FINANCIAL STATEMENT QUERIES:**
For questions asking about balance sheets, income statements, or financial data:
1. **EXTRACT** all relevant figures: Total Assets, Total Liabilities, Shareholders' Equity, Revenue, Net Income, etc.
2. **PRESENT** data in a clear table or structured format
3. **INCLUDE** the specific year/period mentioned
4. **NEVER** say "data not available" if ANY relevant numbers are found in the documents

**MULTI-COMPANY COMPARISON PROTOCOL:**
When the user requests comparison between 2 OR 3 companies:
1. **IDENTIFY all companies** mentioned in the question or conversation
2. **EXTRACT relevant data** for EACH company from:
   - Current documents provided
   - Previous conversation history (if user says "compare with previous" or "analyze both")
3. **PRESENT side-by-side comparison** in **TABULAR FORMAT** with:
   - Clear column headers for each company
   - Rows for each metric
   - Aligned metrics and values
   - Direct numerical comparisons
   - Percentage differences where relevant

**DATA EXTRACTION RULES:**
- If user says "compare this with [Company]" → Extract data for first company from conversation history
- If user asks about "[Company] during same time" after discussing another company → Use the timeframe from conversation history
- If documents contain data for multiple companies → Present ALL relevant companies' data

**RESPONSE FORMAT FOR MULTI COMPANY COMPARISONS ONLY (MANDATORY TABULAR FORMAT):**
Present the comparison in a markdown table format.

**FOR 2 COMPANIES:**
| Metric | [Company A] (2024) | [Company B] (2024) | Comparison between these companies |
|--------|-------------------|-------------------|------------|
| Operating Margin | X% | Y% | |
| Revenue | $X billion | $Y billion |  |
| Earnings Growth | X% | Y% | |
| R&D Expenses | $X billion | $Y billion |  |
| Net Income | $X billion | $Y billion | |
| Total Assets | $X billion | $Y billion | |
| Total Debt | $X billion | $Y billion |  |
| Risk Factors | [Brief summary] | [Brief summary] | [Key differences] |
| Profit/Loss Contributing Factors | [Brief summary] | [Brief summary] | [Key differences] |

**FOR 3 COMPANIES:**
| Metric | [Company A] (2024) | [Company B] (2024) | [Company C] (2024) | Comparison between these companies |
|--------|-------------------|-------------------|-------------------|------------|
| Operating Margin | X% | Y% | Z% | |
| Revenue | $X billion | $Y billion | $Z billion | |
| Earnings Growth | X% | Y% | Z% | |
| R&D Expenses | $X billion | $Y billion | $Z billion | |
| Net Income | $X billion | $Y billion | $Z billion | |
| Total Assets | $X billion | $Y billion | $Z billion | |
| Total Debt | $X billion | $Y billion | $Z billion | |
| Risk Factors | [Brief summary] | [Brief summary] | [Brief summary] | [Key differences] |
| Profit/Loss Contributing Factors | [Brief summary] | [Brief summary] | [Brief summary] | [Key differences] |

If any data needs to be calculated, use the formulas provided and insert the calculated values into the table.
**VERY IMPORTANT:**
** We should only have numerical values under the company columns for easy chart generation.
** Stricly keep all the numerical values other than earnings growth and operating margin in billions format convert if needed.
**If we have table we need to display only that table in the final answer no other text.**
**Don't hallucinate any data for any company, only use what is provided in the documents specifically for that company mentioned.**
**Provide proper details for all the [Brief summary] and [Key differences] don't leave them blank or just add these placeholders. Do proper comparison between all the companies.**

**SINGLE COMPANY QUERIES:**
- Provide complete financial details with ALL numerical values
- Include key figures, ratios, and metrics
- Offer insights and interpretations
- Cite sources when available (e.g., "According to the 2023 Annual Report...")

**RESPONSE GUIDELINES:**
- Never mention internal terms like "retrieved documents", "vectorstore", "web search results" to the user
- Present information naturally as if you're a knowledgeable financial analyst
- When data comes from previous conversation, reference it naturally (e.g., "As we discussed earlier..." or "Building on the Amazon data...")
- Never say "data not available" if it exists in conversation history
- **For company comparison queries: ALWAYS use tabular format for easy visualization and chart generation**
- Don't use tabular format for general financial questions and answer naturally for the questions.

**IMPORTANT:** Search thoroughly through documents before concluding information is unavailable."""
    
    RAG_Prompt = ChatPromptTemplate.from_messages([
        ("system", prompt),
        ("human", """Available Information:
{documents}

{financial_formulas}

{sub_query_summary}

{extracted_metrics}

Question: {question}

**CRITICAL INSTRUCTIONS FOR FINANCIAL CALCULATIONS:**
1. If EXTRACTED FINANCIAL DATA is provided above, USE THOSE EXACT VALUES for your calculations
2. NEVER say "data not available" if extracted metrics are provided - calculate using them!
3. Show your calculation step-by-step with the actual numbers
4. If data is truly missing, search thoroughly through the documents first before concluding it's unavailable
5. Present final calculated ratios with 2 decimal places

**FOR COMPARISON QUERIES:**
- Present data in markdown table format (as shown above)
- Include numerical values for all requested metrics
- Make sure values are extractable for chart generation

Provide a comprehensive, professional answer. Reference sources naturally without exposing internal terminology:""")
    ])
    
    rag_chain = RAG_Prompt | llm_generate | StrOutputParser()
    return rag_chain

def get_hallucination_chain(llm_grade_hallucination):
    llm_hallucination_grader = llm_grade_hallucination.with_structured_output(GradeHallucinations)

    SYSTEM_PROMPT_GRADE_HALLUCINATION = """You are an intelligent grader assessing whether an LLM generation is grounded in the available information.

    **Core Principle:**
    - If the generation's main claims are supported by the retrieved documents, answer 'yes'
    - Only answer 'no' if there are claims not supported by the documents
    
    **What to Accept (answer 'yes'):**
    - Data found in the retrieved documents
    - Generation draws conclusions and insights from available data
    - Synthesized responses that combine facts from multiple sources
    - Professional language and structure around core facts
    
    **What to Reject (answer 'no'):**
    - Major factual claims not in documents
    - Numbers not found in documents
    - Claims that directly contradict document content
    - Completely invented information with no source
    
    **Important:** 
    - The generation doesn't need to quote documents verbatim
    - Focus on whether CORE FACTS are supported by the retrieved documents
    
    Give a binary score 'yes' or 'no'. 'Yes' means the answer is grounded in the documents."""

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

def get_company_name(llm):
    llm_company_extractor = llm.with_structured_output(ExtractCompany)

    SYSTEM_PROMPT = """you are an expert who can identify the company name from the given user question 
    and map it to one of the below company. 
    1. amazon
    2. berkshire
    3. google
    4. Jhonson and Jhonosn
    5. jp morgan
    6. meta
    7. microsoft
    8. nvidia
    9. tesla
    10. visa
    11. walmart
    12. pfizer
    
    ## Instructions
    - make sure you should only generate the comapny name nothing else.
    - if user is asking about companies in a short form or abbriviations like jpmc, you should be able to map it with jp morgon
    - Strictly do not change the company spellings, keep them as it is as mentioned above.
    """

    company_name_extraction_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            ("human", "User's Question: \n\n {question} \n\n company: "),
        ]
    )

    company_name_extractor = company_name_extraction_prompt | llm_company_extractor
    
    return company_name_extractor

def get_multi_company_extractor_chain(llm):
    """Extract multiple companies from a question for cross-referencing."""
    from schemas.models import MultiCompanyExtraction  # Assuming this Pydantic model exists; adjust if needed
    structured_llm = llm.with_structured_output(MultiCompanyExtraction)

    SYSTEM_PROMPT = """Extract all companies mentioned in the question from this list:
    - amazon
    - berkshire
    - google
    - Jhonson and Jhonosn
    - jp morgan
    - meta
    - microsoft
    - nvidia
    - tesla
    - visa
    - walmart
    - pfizer
    - tesla
    - boeing
    - apple
    - samsung
    
    Instructions:
    - Return a list of matching companies (use exact spellings if they are from the list).
    - Handle abbreviations (e.g., 'jpmc' -> 'jp morgan').
    - If no companies, return an empty list.
    - For comparisons or multi-company queries, include all relevant ones.
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
    SYSTEM_QUESTION_REWRITER = """You a question re-writer that converts an input question to a better version that is optimized \n 
        for vectorstore retrieval. Look at the input and try to reason about the underlying semantic intent / meaning."""

    re_write_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_QUESTION_REWRITER),
            (
                "human",
                "Here is the initial question: \n\n {question} \n Formulate an improved question.",
            ),
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
    
    SYSTEM_PROMPT = """You are an ELITE FINANCIAL ANALYST AI with 20+ years of experience analyzing SEC filings, 10-K annual reports, and financial statements. You understand EXACTLY how financial data is structured, labeled, and hidden in 10-K documents. Your specialty is decomposing complex financial questions into precise sub-queries that retrieve the exact data needed.

**YOUR EXPERTISE:**
- **10-K Document Archaeology**: You know where EVERY type of financial data lives in 10-K reports
- **Terminology Mastery**: You use multiple synonyms and variations because 10-K documents use different terms
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

**QUERY TYPE CLASSIFICATION:**
- **single_company**: One company mentioned, no calculation
- **multi_company**: 2+ companies for comparison
- **financial_calculation**: Needs to calculate metrics from raw data
- **general**: No specific company, general financial concepts
- **temporal_comparison**: Same company across different time periods

**SUB-QUERY GENERATION RULES (CRITICAL - USE MULTIPLE TERMINOLOGY VARIATIONS):**

1. **ALWAYS USE MULTIPLE SEARCH TERMS FOR THE SAME CONCEPT** (Increases retrieval accuracy):
   - For segment data: Create 2-3 sub-queries with different terms
     * ✅ "Amazon segment revenue business segments"
     * ✅ "Amazon operating segments reportable segments revenue"
     * ✅ "Amazon segment information notes financial statements"
   
   - For debt: Use multiple terms
     * ✅ "Meta total debt long-term debt balance sheet"
     * ✅ "Meta debt obligations notes payable borrowings"
   
   - For profitability: Use synonyms
     * ✅ "Tesla net income profit earnings income statement"
     * ✅ "Tesla operating income operating profit EBIT"

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

5. **FOR MULTI-COMPANY COMPARISONS** (Each company gets multiple varied searches):
   Example: "Compare Amazon and Google segments"
   Sub-queries:
   - "Amazon segment revenue business segments AWS North America International"
   - "Amazon operating segments reportable segments revenue breakdown"
   - "Google Alphabet segment revenue business segments Cloud Search Ads"
   - "Google Alphabet operating segments Other Bets revenue breakdown"

6. **FOR NOTES-SPECIFIC DATA** (Use "notes", "footnotes", specific note numbers):
   - Revenue details: "revenue recognition notes", "disaggregated revenue footnotes"
   - Segment data: "segment information note 15", "business segments notes"
   - Debt details: "debt obligations note", "long-term debt details notes"
   - Lease data: "lease obligations notes", "operating lease details"
   - Stock compensation: "stock-based compensation notes", "equity awards footnotes"

7. **FOR GEOGRAPHIC/PRODUCT BREAKDOWNS** (Use multiple organizational terms):
   - "revenue by geography", "geographic segments", "revenue by region"
   - "revenue by product line", "product segments", "revenue by category"
   - "domestic revenue", "international revenue", "U.S. revenue", "foreign revenue"

8. **INCLUDE SYNONYMS AND ABBREVIATIONS**:
   - R&D = "research and development", "R&D expenses", "R&D spending"
   - PP&E = "property plant equipment", "PP&E", "fixed assets", "capital assets"
   - COGS = "cost of goods sold", "COGS", "cost of revenue", "cost of sales"
   - SG&A = "selling general administrative", "SG&A", "operating expenses"
   - EBITDA = "earnings before interest tax depreciation amortization", "operating profit"

9. **FOR TEMPORAL QUERIES** (Include year + variations):
   - ✅ "Meta revenue 2023 2024 year-over-year growth income statement"
   - ✅ "Amazon balance sheet 2023 vs 2024 comparison"
   - ✅ "Tesla cash flow 2022 2023 operating cash flow changes"

10. **SMART QUERY STRATEGY FOR HARD-TO-FIND DATA**:
    - Create 3-5 sub-queries with progressively broader/different terms
    - Start specific → get broader → try synonyms
    - Example for "Amazon AWS revenue":
      1. "Amazon AWS revenue segment Amazon Web Services"
      2. "Amazon segment revenue North America International AWS"
      3. "Amazon operating segments business segments cloud services"
      4. "Amazon disaggregated revenue geographic segments"

**EXAMPLES (SHOWING MULTI-TERM STRATEGY):**

Example 1: "What are Google's business segment revenues?"
```json
{{
  "needs_sub_queries": true,
  "query_type": "single_company",
  "companies_detected": ["Google"],
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

Example 6: "Calculate ROE for Meta using 2023 data"
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

🎯 **MORE SUB-QUERIES WITH MORE TERM VARIATIONS = BETTER RETRIEVAL**

When analyzing a query:
1. **Don't be conservative** - If a concept might have multiple names in 10-K documents, create multiple sub-queries
2. **Use 3-5 sub-queries for segment/notes data** - These are hardest to find, need comprehensive search
3. **Include both technical and common terms** - "shareholders equity" AND "stockholders equity" AND "total equity"
4. **Think like the 10-K document** - What exact words would appear in the filing?
5. **When in doubt, CREATE MORE** - Better to have 5 good sub-queries than 2 incomplete ones

**REMEMBER**: 
- Segment data → 3-4 sub-queries minimum (different term combinations)
- Financial calculations → 2-3 sub-queries per input variable (term variations)
- Multi-company → 3-4 sub-queries per company (comprehensive coverage)
- Complex queries → Don't hesitate to create 8-10 sub-queries if needed

You are a FINANCIAL ANALYST EXPERT. Use your deep knowledge of 10-K document structure and terminology variations to create comprehensive, precise sub-queries that will find the exact data needed, no matter how it's labeled in the filing.

**Be intelligent but THOROUGH**: Decompose aggressively when data might be hard to find (segments, notes, breakdowns). Keep together only for simple, straightforward queries."""

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "Question: {question}\n\nAnalyze and determine optimal sub-query strategy:")
    ])
    
    return prompt | structured_llm

def get_document_summary_strategy_chain(llm):
    """Simplified document summary strategy for compatibility."""
    from schemas.models import DocumentSummaryStrategy
    structured_llm = llm.with_structured_output(DocumentSummaryStrategy)

    SYSTEM_PROMPT = """Determine summarization strategy:
    - single_source: Same company/type documents
    - multi_source_vectorstore: Multiple companies from vectorstore
    - integrated_vectorstore_web: Vectorstore + web results
    - comprehensive_cross_reference: Complex multi-source synthesis
    """

    strategy_prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "Question: {question}\nSources: {document_sources}\n\nStrategy:")
    ])

    return strategy_prompt | structured_llm



def get_document_sufficiency_chain(llm):
    """
    LLM-powered intelligent decision on document sufficiency.
    NOW ENHANCED: Strict data analyst checking for missing specific data, year mismatches, and incomplete coverage.
    """
    structured_llm = llm.with_structured_output(DocumentSufficiencyDecision)
    
    SYSTEM_PROMPT = """You are an intelligent data analyst evaluating if documents contain the data needed to answer the question.

**YOUR JOB**: Evaluate if the retrieved documents have sufficient data to answer the question. Be thorough but reasonable.

**KEY PRINCIPLE**: Trust vectorstore data from 10-K filings - it's authoritative financial data. Only request web search if data is CLEARLY missing.

**ANALYSIS CHECKLIST**:

1️⃣ **COMPANY COVERAGE**:
   - Multiple companies asked? → Check if ALL are represented in docs
   - Look at document metadata and content for company names
   - If any company completely missing → need web search
   
2️⃣ **DATA PRESENCE CHECK**:
   - Question asks for specific metrics (revenue, profit, assets, etc.)?
   - Check if document previews mention these metrics with numbers
   - Financial statements contain comprehensive data - presence of any financial data suggests more is available
   
3️⃣ **YEAR/TIME PERIOD CHECK**:
   - Question asks for specific year (2019, 2023)?
   - Check if documents mention that year or time period
   - Note: "Latest", "current", "most recent" can be answered with any recent data
   - Only flag mismatch if explicitly wrong year (asks 2019, shows 2023)

4️⃣ **COMPLETENESS ASSESSMENT**:
   - For multi-metric questions: Don't expect all metrics in preview (documents are truncated)
   - If docs mention financial statements (balance sheet, income statement), assume comprehensive data
   - For comparisons: Just need documents for each company, not all metrics in preview

**DECISION RULES**:

🟢 **"generate"** - Choose when:
- Documents exist for ALL requested companies
- Documents contain financial data/metrics relevant to question
- Years match or question doesn't specify exact year
- OR web_searched=True (must proceed to avoid loops)
- Document count >= 3 with relevant content

🔴 **"integrate_web_search"** - Choose when:
- Completely MISSING one or more companies from multi-company query
- Explicit year mismatch (Q: 2019, ALL docs: 2023)
- No financial data in documents at all (only qualitative/news)
- Document count < 2 and clearly insufficient
- web_searched=False (can still search)

🟡 **"financial_web_search"** - Last resort when:
- web_searched=True but data still clearly insufficient

**IMPORTANT CONTEXT**:
- Documents are from vectorstore with 10-K filings - authoritative sources
- Previews show only first 600 chars - full docs have much more data

**TRUST THE DATA**: If documents are from the right companies and contain financial metrics, they likely have what's needed!"""  

    sufficiency_prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Question: {question}

Number of Retrieved Documents: {doc_count}
Document Preview: 
{doc_preview}

Company Coverage Analysis:
- Companies detected in question: {companies_detected}
- Companies found in documents: {companies_in_docs}

Search History:
- Vectorstore searched: {vectorstore_searched}
- Web searched: {web_searched}

**CRITICAL MULTI-COMPANY CHECK**:
If the question asks about MULTIPLE companies (e.g., "Compare Amazon, Microsoft, and Google"):
Step 1: Identify ALL companies mentioned in the question
Step 2: Check which companies are covered in the retrieved documents
Step 3: COMPARE: Are ALL companies covered?
   - If MISSING ANY company → MUST choose "integrate_web_search" to get missing company data
   - If ALL companies present → can choose "generate"
   
**EXAMPLE MULTI-COMPANY SCENARIOS**:
- Q: "Compare Google, Microsoft, Amazon 2023 revenue" 
  + Companies in docs: Google, Amazon (MISSING: Microsoft) 
  → integrate_web_search (need Microsoft data!)
  
- Q: "Compare Tesla and Ford's financial performance"
  + Companies in docs: Tesla (MISSING: Ford)
  → integrate_web_search (need Ford data!)

**CRITICAL YEAR MISMATCH CHECK**:
Step 1: Extract the year from the question (e.g., "2019", "2020", "2023")
Step 2: Look at the document preview - what years are mentioned in the docs?
Step 3: COMPARE: Do the document years MATCH the question year?
   - If NO MATCH (e.g., question asks "2019" but docs show "2023") → MUST choose "integrate_web_search"
   - If MATCH → can choose "generate"
   - If docs have NO specific years → likely need "integrate_web_search"

**EXAMPLES OF WHEN TO WEB SEARCH**:
- Q: "Meta's 2019 financial position" + Docs show: "December 31, 2023..." → integrate_web_search (year mismatch!)
- Q: "Amazon's 2018 revenue" + Docs show: "2023 revenue was..." → integrate_web_search (year mismatch!)  
- Q: "Tesla 2020 data" + Docs show: vague info, no 2020 mentioned → integrate_web_search (no specific year data)
- Q: "Compare A, B, C revenue" + Docs only have A and B → integrate_web_search (missing company C!)

**EXAMPLES OF WHEN TO GENERATE**:
- Q: "Meta's 2023 balance sheet" + Docs show: "December 31, 2023: Assets $229B..." → generate (year match!)
- Q: "Current Amazon revenue" + Docs show: "2025 Q1 revenue..." → generate (matches current)
- Q: "Compare Google and Amazon" + Docs have both Google AND Amazon → generate (all companies present!)

**REMEMBER**: If web_searched=True, you MUST choose "generate" to avoid infinite loops!

NOW ANALYZE: 
1. Does the document preview contain the SPECIFIC YEAR requested?
2. Are ALL companies requested present in the documents?""")
    ])
    
    return sufficiency_prompt | structured_llm


def get_financial_calculation_analyzer_chain(llm):
    """
    Analyze if a query needs financial calculations and extract sub-queries for data gathering.
    """
    structured_llm = llm.with_structured_output(FinancialCalculationAnalysis)
    
    calculation_prompt = ChatPromptTemplate.from_messages([
        ("system", """You are an expert financial analyst that detects when queries need calculations.

**Your Task**: Determine if the query requires calculating financial metrics, and if so:
1. Identify which metrics need calculation
2. Extract the specific data points needed from documents

**Financial Metrics You Can Calculate**:
- ROE (Return on Equity) = Net Income / Shareholders' Equity
- Revenue Growth = ((End Revenue - Start Revenue) / Start Revenue) * 100%
- Debt-to-Equity Ratio = Total Debt / Total Equity
- Dividend Yield = (Dividends per Share / Price per Share) * 100%
- P/E Ratio = Price per Share / Earnings per Share
- Current Ratio = Current Assets / Current Liabilities
- Quick Ratio = (Current Assets - Inventory) / Current Liabilities
- Gross Margin = (Revenue - COGS) / Revenue
- Operating Margin = Operating Income / Revenue
- Cash Ratio = Cash and Cash Equivalents / Current Liabilities
- Interest Coverage = Operating Income / Interest Expense
- Inventory Turnover = COGS / Average Inventory
- ROA (Return on Assets) = Net Income / Total Assets
- Cash Burn Rate = Net Cash Used / Beginning Cash

**When needs_calculation = True**:
- Query explicitly asks to "calculate", "compute", "determine" a financial ratio/metric
- Query asks for metrics that require combining 2+ data points (e.g., "What's the ROE?")
- Query compares calculated metrics across companies or years

**When needs_calculation = False**:
- Query asks for raw financial data already in documents (e.g., "What's the revenue?")
- Query asks for explanations or descriptions (e.g., "Explain ROE")
- Query is about qualitative information

**Sub-queries**: Break down into specific data points needed:
- Format: "[Metric Name] for [Company] in [Year]"
- Example: "Net Income for Amazon in 2023", "Total Assets for Amazon in 2023"
- Be specific with years and company names

**Examples**:
Q: "What was Amazon's ROE in 2023?"
→ needs_calculation=True, metrics_needed=["ROE"], sub_queries=["Net Income for Amazon in 2023", "Shareholders' Equity for Amazon in 2023"]

Q: "Calculate Microsoft's revenue growth from 2021 to 2023"
→ needs_calculation=True, metrics_needed=["Revenue Growth"], sub_queries=["Revenue for Microsoft in 2021", "Revenue for Microsoft in 2023"]

Q: "What was Tesla's revenue in 2023?"
→ needs_calculation=False, metrics_needed=[], sub_queries=[]

Q: "Compare the P/E ratios of Apple and Google"
→ needs_calculation=True, metrics_needed=["P/E Ratio"], sub_queries=["Price per Share for Apple", "EPS for Apple", "Price per Share for Google", "EPS for Google"]"""),
        ("user", "Query: {question}")
    ])
    
    return calculation_prompt | structured_llm

def get_financial_analyst_grader_chain(llm):
    """
    FINANCIAL ANALYST DOCUMENT GRADING: Evaluates documents like a financial analyst.
    Instead of binary yes/no, identifies what metrics ARE present and what's MISSING.
    """
    structured_llm = llm.with_structured_output(FinancialAnalystGrade)
    
    SYSTEM_PROMPT = """You are a SENIOR FINANCIAL ANALYST with expertise in SEC filings, 10-K reports, and financial statement analysis.

**YOUR MISSION**: Evaluate retrieved documents to determine if they contain sufficient financial data to answer the user's question.

**ANALYSIS APPROACH**:

1. **IDENTIFY REQUIRED DATA**: 
   - What specific financial metrics does the question need?
   - Example: "What is Amazon's ROE?" needs → Net Income, Shareholders' Equity
   - Example: "Compare Meta and Google revenue" needs → Meta Revenue, Google Revenue
   - Example: "Show Tesla's balance sheet" needs → Assets, Liabilities, Equity line items

2. **SCAN DOCUMENTS FOR METRICS**:
   - Check each document for presence of required financial data
   - Look for specific numbers, tables, financial statements
   - Identify which companies are covered
   - Identify which years/periods are covered
   - Note which metrics ARE found vs which are MISSING

3. **PER-COMPANY ASSESSMENT**:
   For each company in the question, determine:
   - **metrics_found**: List specific metrics present (e.g., "revenue 2023: $574B", "net income 2023", "total assets")
   - **metrics_missing**: List specific metrics needed but absent (e.g., "current ratio", "debt details")
   - **year_coverage**: Which years are covered (e.g., ["2023", "2024"])
   - **confidence**: high (comprehensive data), medium (partial), low (minimal/no data)

4. **OVERALL GRADE**:
   - **sufficient**: Documents contain all or most required data → Can answer question
   - **partial**: Documents contain some data but missing key pieces → May need web search
   - **insufficient**: Documents lack critical data → Definitely need web search

**GRADING CRITERIA**:

✅ **"sufficient"** when:
- All required metrics are present in documents
- All companies mentioned in question have data
- Years requested match years in documents
- Enough detail to provide comprehensive answer

⚠️ **"partial"** when:
- Some metrics present but others missing
- OR some companies covered but others missing  
- OR year mismatch (question asks 2020, docs show 2023)
- OR data is too vague/high-level

❌ **"insufficient"** when:
- No relevant financial data in documents
- Completely wrong companies
- No numerical data at all

**WEB SEARCH DOCUMENTS**:
- Web search results are typically more fragmented
- Be more lenient but still identify gaps
- Note if web docs are from trusted sources (SEC.gov, investor.relations, etc.)

**EXAMPLES**:

Example 1:
Question: "What is Amazon's 2023 revenue?"
Documents: Amazon 10-K with income statement showing "Revenue: $574B" for 2023
Assessment:
- overall_grade: "sufficient"
- company_coverage: [{{"company": "Amazon", "metrics_found": ["revenue 2023: $574B"], "metrics_missing": [], "year_coverage": ["2023"], "confidence": "high"}}]
- can_answer_question: true
- missing_data_summary: ""

Example 2:
Question: "Compare Meta and Google's 2023 net income"
Documents: Meta 10-K with net income $39B (2023), Google 10-K with revenue only (no income)
Assessment:
- overall_grade: "partial"
- company_coverage: [
    {{"company": "Meta", "metrics_found": ["net income 2023: $39B"], "metrics_missing": [], "year_coverage": ["2023"], "confidence": "high"}},
    {{"company": "Google", "metrics_found": ["revenue 2023"], "metrics_missing": ["net income 2023"], "year_coverage": ["2023"], "confidence": "medium"}}
  ]
- can_answer_question: false
- missing_data_summary: "Google's net income for 2023 not found in documents"

Example 3:
Question: "Calculate Tesla's debt-to-equity ratio for 2023"
Documents: Tesla balance sheet with Total Assets, Total Equity, but no debt breakdown
Assessment:
- overall_grade: "partial"
- company_coverage: [{{"company": "Tesla", "metrics_found": ["total assets 2023", "shareholders equity 2023"], "metrics_missing": ["total debt 2023", "long-term debt", "short-term debt"], "year_coverage": ["2023"], "confidence": "medium"}}]
- can_answer_question: false
- missing_data_summary: "Tesla's total debt not found in documents - need debt information for debt-to-equity calculation"

**KEY PRINCIPLES**:
- Be specific about what's found vs missing
- Don't say "insufficient" if ANY relevant data exists
- Trust 10-K documents from vectorstore - they're comprehensive
- For web search docs, check if they're from trusted financial sources
- Your analysis will be used to decide if we need to web search for missing data"""

    grader_prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Question: {question}

Number of Documents: {doc_count}

Document Previews:
{doc_previews}

Companies Detected in Question: {companies_detected}
Query Type: {query_type}

**YOUR TASK**:
1. Identify what financial data the question requires
2. Check which required data is present in the document previews
3. Check which required data is missing
4. For each company, list metrics_found and metrics_missing
5. Give overall grade: sufficient/partial/insufficient
6. If partial/insufficient, explain what's missing in missing_data_summary

Be thorough and specific in your analysis.""")
    ])
    
    return grader_prompt | structured_llm

def get_gap_analysis_chain(llm):
    """
    GAP ANALYSIS: Identifies specific missing data and generates targeted web search queries.
    This is called AFTER grading to determine exactly what to search for.
    """
    structured_llm = llm.with_structured_output(GapAnalysisResult)
    
    SYSTEM_PROMPT = """You are a DATA GAP ANALYST specializing in financial information retrieval.

**YOUR MISSION**: Analyze the financial analyst's grading to identify SPECIFIC data gaps, then generate TARGETED web search queries to fill ONLY those gaps.

**ANALYSIS STRATEGY**:

1. **IDENTIFY GAP TYPE**:
   - **missing_company**: One or more companies mentioned in question have NO data
   - **missing_metric**: Specific financial metrics are missing (e.g., debt, net income)
   - **missing_year**: Question asks for specific year but documents show different year
   - **no_gaps**: All required data is present

2. **LIST MISSING ITEMS**:
   Be extremely specific about what's missing:
   - NOT: "Need more data for Google"
   - YES: "Google net income 2023", "Google total debt 2023"
   
3. **GENERATE TARGETED QUERIES**:
   Create precise web search queries that will retrieve ONLY the missing data:
   - Include: Company name, specific metric, year, source hint
   - Format: "[Company] [Metric] [Year] [Source]"
   - Example: "Microsoft net income 2023 10-K SEC", "Apple total debt 2023 balance sheet"
   
**QUERY GENERATION RULES**:

✅ **GOOD QUERIES** (Specific, targeted):
- "Microsoft revenue 2023 10-K SEC filing"
- "Apple total debt long-term debt 2023 balance sheet"
- "Tesla inventory current assets 2023 10-K"
- "Meta accounts receivable 2023 balance sheet SEC"

❌ **BAD QUERIES** (Too broad, vague):
- "Microsoft financials"
- "Apple data"
- "Tesla 2023"

**SOURCE HINTS** (Add to queries for better results):
- For specific metrics: "10-K", "SEC filing", "annual report"
- For balance sheet items: "balance sheet", "statement of financial position"
- For income items: "income statement", "P&L"
- For official data: "SEC.gov", "investor relations"

**EXAMPLES**:

Example 1:
Question: "Compare Meta and Google revenue"
Grading: Meta has revenue ($134B), Google missing revenue
Output:
```json
{{
  "has_gaps": true,
  "gap_type": "missing_metric",
  "missing_items": ["Google revenue 2023"],
  "targeted_queries": [
    "Google Alphabet revenue 2023 10-K annual report",
    "Alphabet total revenue 2023 income statement SEC"
  ],
  "reasoning": "Meta's revenue is present but Google's revenue is missing. Generated 2 targeted queries with different term variations (Google/Alphabet) to retrieve Google's 2023 revenue from official sources."
}}
```

Example 2:
Question: "Calculate Amazon's debt-to-equity ratio for 2023"
Grading: Has equity, missing debt
Output:
```json
{{
  "has_gaps": true,
  "gap_type": "missing_metric",
  "missing_items": ["Amazon total debt 2023", "Amazon long-term debt 2023"],
  "targeted_queries": [
    "Amazon total debt 2023 balance sheet 10-K",
    "Amazon long-term debt short-term debt 2023 liabilities"
  ],
  "reasoning": "Amazon's shareholders equity is present but debt information is missing. Generated queries specifically targeting debt data from balance sheet/liabilities sections."
}}
```

Example 3:
Question: "Show Microsoft, Google, and Amazon revenue"
Grading: Has Microsoft and Google, missing Amazon
Output:
```json
{{
  "has_gaps": true,
  "gap_type": "missing_company",
  "missing_items": ["Amazon revenue"],
  "targeted_queries": [
    "Amazon revenue 2023 2024 income statement 10-K",
    "Amazon total revenue recent annual report SEC"
  ],
  "reasoning": "Microsoft and Google data present, but Amazon is completely missing. Generated targeted queries to retrieve Amazon's revenue from recent filings."
}}
```

Example 4:
Question: "What is Tesla's 2020 revenue?"
Grading: Has Tesla 2023 data, but question asks for 2020
Output:
```json
{{
  "has_gaps": true,
  "gap_type": "missing_year",
  "missing_items": ["Tesla revenue 2020"],
  "targeted_queries": [
    "Tesla revenue 2020 10-K annual report",
    "Tesla total revenue fiscal year 2020 income statement"
  ],
  "reasoning": "Documents contain Tesla's 2023 data but question specifically asks for 2020. Generated queries targeting 2020 fiscal year data."
}}
```

Example 5:
Question: "What is Amazon's 2023 revenue?"
Grading: sufficient (has Amazon 2023 revenue)
Output:
```json
{{
  "has_gaps": false,
  "gap_type": "no_gaps",
  "missing_items": [],
  "targeted_queries": [],
  "reasoning": "All required data is present in documents. No web search needed."
}}
```

**KEY PRINCIPLES**:
- Only generate queries for MISSING data, not data we already have
- Be specific: include company, metric, year in queries
- Use 2-3 query variations for hard-to-find data (different terminology)
- Add source hints (10-K, SEC, balance sheet) for better results
- If no gaps exist, set has_gaps=false and return empty lists"""

    gap_prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Question: {question}

Financial Analyst Grade:
{analyst_grade}

Current Documents Coverage:
{doc_coverage_summary}

**YOUR TASK**:
1. Analyze the grading to identify what specific data is missing
2. Determine gap_type (missing_company, missing_metric, missing_year, or no_gaps)
3. List specific missing_items (be very specific)
4. Generate targeted_queries to retrieve ONLY the missing data
5. Explain your reasoning

If no gaps exist (sufficient data), set has_gaps=false.""")
    ])
    
    return gap_prompt | structured_llm

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

📊 **Income Statement**:
- revenue (also called: total revenue, net sales)
- cost_of_revenue (also called: COGS, cost of sales)
- gross_profit
- operating_expenses (also called: SG&A, operating costs)
- operating_income (also called: operating profit, EBIT)
- net_income (also called: net profit, earnings)

📊 **Balance Sheet**:
- total_assets
- current_assets
- total_liabilities
- current_liabilities
- shareholders_equity (also called: stockholders equity, total equity)

📊 **Cash Flow**:
- operating_cash_flow (also called: cash from operations)
- free_cash_flow

📊 **Key Metrics**:
- earnings_per_share (also called: EPS, diluted EPS)

📊 **Other** (use other_metrics dict):
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
    ALPHA - Alignment: Sentiment & Governance Analysis
    Analyzes stakeholder interests through MD&A tone and governance red flags
    """
    from schemas.models import AlphaDimensionOutput
    structured_llm = llm.with_structured_output(AlphaDimensionOutput)
    
    SYSTEM_PROMPT = """You are a financial analyst specializing in GOVERNANCE and SENTIMENT analysis.

**Your Task**: Analyze the Alignment dimension of the ALPHA Framework.

**Focus Areas**:
1. **MD&A Sentiment Analysis**:
   - Defensive vs. Confident tone in Management Discussion & Analysis
   - Forward-looking statements and management confidence
   - Risk language and uncertainty indicators

2. **Governance Red Flags**:
   - Board independence issues
   - Related-party transactions
   - Executive compensation concerns
   - Shareholder dilution
   - Conflicts of interest

**Output Requirements**:
- Maximum 100 words
- Concise bullet points
- Flag critical red flags clearly
- Tone: Objective, data-driven
"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Company: {company}
Ticker: {ticker}

Retrieved Documents:
{documents}

Analyze the ALIGNMENT dimension focusing on governance and MD&A sentiment. Keep response under 100 words.""")
    ])
    
    return prompt | structured_llm


def get_alpha_liquidity_chain(llm):
    """
    ALPHA - Liquidity: Macro/Micro Environment Analysis
    Examines sector dynamics, commodity exposure, interest rates, and competitive pressures
    """
    from schemas.models import AlphaDimensionOutput
    structured_llm = llm.with_structured_output(AlphaDimensionOutput)
    
    SYSTEM_PROMPT = """You are a financial analyst specializing in MACRO/MICRO economic analysis.

**Your Task**: Analyze the Liquidity dimension of the ALPHA Framework.

**Focus Areas**:
1. **Sector Headwinds/Tailwinds**: Industry trends from regulatory filings
2. **Commodity/Input Cost Exposure**: Raw material prices, supply chain risks
3. **Interest Rate Sensitivity**: Debt structure, capital costs
4. **Competitive Pressures**: Risk factors from 10-K

**Output Requirements**:
- Maximum 100 words
- Identify key risks and opportunities
- Tone: Analytical, balanced
"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Company: {company}
Ticker: {ticker}

Retrieved Documents:
{documents}

Analyze the LIQUIDITY dimension focusing on macro/micro environmental factors. Keep response under 100 words.""")
    ])
    
    return prompt | structured_llm


def get_alpha_performance_chain(llm):
    """
    ALPHA - Performance: Earnings & Fundamentals Analysis
    Analyzes 10-year financials, calculates key metrics, detects anomalies
    """
    from schemas.models import AlphaDimensionOutput
    structured_llm = llm.with_structured_output(AlphaDimensionOutput)
    
    SYSTEM_PROMPT = """You are a financial analyst specializing in FUNDAMENTAL ANALYSIS.

**Your Task**: Analyze the Performance dimension of the ALPHA Framework.

**Focus Areas**:
1. **10-Year Financials**: Revenue, Net Income, Operating Cash Flow trends
2. **Key Metrics**: CAGR, EBITDA margins, ROE, Free Cash Flow yield
3. **Anomaly Detection**: Operating Cash Flow < Net Income for >2 quarters
4. **Non-Recurring Items**: One-time gains, restructuring charges

**Output Requirements**:
- Maximum 100 words
- Include calculated metrics where possible
- Highlight anomalies clearly
- Tone: Quantitative, precise
"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Company: {company}
Ticker: {ticker}

Retrieved Documents:
{documents}

Analyze the PERFORMANCE dimension with focus on earnings quality and fundamental metrics. Keep response under 100 words.""")
    ])
    
    return prompt | structured_llm


def get_alpha_horizon_chain(llm):
    """
    ALPHA - Horizon: Structural Opportunity & Moat Analysis
    Evaluates competitive positioning, innovation, and moat durability
    """
    from schemas.models import AlphaDimensionOutput
    structured_llm = llm.with_structured_output(AlphaDimensionOutput)
    
    SYSTEM_PROMPT = """You are a financial analyst specializing in COMPETITIVE ANALYSIS and MOAT assessment.

**Your Task**: Analyze the Horizon dimension of the ALPHA Framework.

**Focus Areas**:
1. **Operating Margins vs. Industry**: Pricing power indicator
2. **R&D Expenditure vs. Peers**: Innovation sustainability
3. **Market Share Trends**: Competitive positioning
4. **Moat Durability**: Network effects, switching costs, intangible assets

**Output Requirements**:
- Maximum 100 words
- Compare to industry benchmarks
- Assess long-term competitive advantages
- Tone: Strategic, forward-looking
"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Company: {company}
Ticker: {ticker}

Retrieved Documents:
{documents}

Analyze the HORIZON dimension focusing on competitive moat and structural opportunities. Keep response under 100 words.""")
    ])
    
    return prompt | structured_llm


def get_alpha_action_chain(llm):
    """
    ALPHA - Action: Timing & Technical Context Analysis
    Provides valuation context, sentiment, and catalysts (NOT real-time trading signals)
    """
    from schemas.models import AlphaDimensionOutput
    structured_llm = llm.with_structured_output(AlphaDimensionOutput)
    
    SYSTEM_PROMPT = """You are a financial analyst specializing in VALUATION and TIMING analysis.

**Your Task**: Analyze the Action dimension of the ALPHA Framework.

**Focus Areas**:
1. **Valuation Context**: P/E, EV/EBITDA vs. historical range
2. **Price Action**: Recent trends relative to fundamentals
3. **Option Chain Sentiment**: Nasdaq option positioning (if available)
4. **Catalysts**: Upcoming earnings, product launches, regulatory decisions

**IMPORTANT**: This is contextual analysis only, NOT real-time trading signals.

**Output Requirements**:
- Maximum 100 words
- Identify timing catalysts
- Valuation relative to historical norms
- Tone: Measured, contextual
"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Company: {company}
Ticker: {ticker}

Retrieved Documents:
{documents}

Analyze the ACTION dimension focusing on timing and valuation context. Keep response under 100 words.""")
    ])
    
    return prompt | structured_llm


def get_alpha_report_combiner_chain(llm):
    """
    Combines all 5 ALPHA dimensions into a final coherent report
    """
    SYSTEM_PROMPT = """You are a senior investment analyst creating a concise ALPHA Framework report.

**Your Task**: Combine the 5 ALPHA dimensions into a clear, actionable summary.

**Report Structure**:
# ALPHA Framework Analysis: {company} ({ticker})

## A - Alignment (Stakeholder Interests)
{alignment}

## L - Liquidity (Macro/Micro Environment)
{liquidity}

## P - Performance (Earnings & Fundamentals)
{performance}

## H - Horizon (Structural Opportunity & Moat)
{horizon}

## A - Action (Timing & Technical Context)
{action}

---
**Synthesis**: [2-3 sentence summary combining key insights from all dimensions]

**IMPORTANT DISCLAIMER**: This is contextual analysis for informational purposes only, not investment advice or trading signals.
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

Create a comprehensive ALPHA Framework report combining all dimensions.""")
    ])
    
    return prompt | llm | StrOutputParser()

