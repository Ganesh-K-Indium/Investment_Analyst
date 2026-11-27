# RAG Agent Test Questions

Comprehensive test suite for multi-quarter, multi-filing-type, and multi-year query support.

---

## 1. Regression Tests (Baseline Behavior)

These tests ensure existing single-dimension queries still work exactly as before.

### 1.1 Single Quarter, Single Year
- [ ] "What was Google's Q2 2026 revenue?"
- [ ] "Meta's Q1 2026 net income"
- [ ] "Apple Q3 2026 operating expenses"
- [ ] "Tesla's Q4 2025 gross profit"

### 1.2 Single Year, No Quarter Specified
- [ ] "What were Amazon's 2025 annual revenues?"
- [ ] "Microsoft's total expenses in 2026"
- [ ] "JPMorgan's net income for fiscal year 2025"

### 1.3 Single Filing Type, Multi-Year
- [ ] "Google's annual revenue from 2024 to 2026" (should use 10-K only)
- [ ] "Tesla's quarterly earnings Q2 2025 and Q2 2026" (should use 10-Q only)

---

## 2. Multi-Quarter Tests

Test the new plural `requested_fiscal_quarters` support.

### 2.1 Two Quarters, Same Year
- [ ] "Compare Google's Q1 2026 and Q2 2026 revenue"
- [ ] "Q3 vs Q4 2025 net income for Meta"
- [ ] "Apple Q1 2026 and Q3 2026 operating income comparison"
- [ ] "What was the difference between Microsoft's Q2 and Q4 2026 expenses?"

### 2.2 Multiple Quarters (3+), Same Year
- [ ] "Show Q1, Q2, and Q3 2026 revenue for Amazon"
- [ ] "Analyze Google's Q1, Q2, Q3, and Q4 2026 quarterly performance"

### 2.3 Quarters with Different Years
- [ ] "Compare Google's Q1 2025 vs Q1 2026 revenue"
- [ ] "Q2 2025 and Q2 2026 net income for Meta"
- [ ] "Tesla Q4 2025 versus Q4 2026 gross profit"
- [ ] "Apple Q3 2025 and Q3 2026 operating income"

### 2.4 Multiple Quarters, Multiple Years
- [ ] "Compare Google's Q1 2025, Q1 2026, Q2 2025, and Q2 2026 quarterly revenue"
- [ ] "Show Meta's Q1 and Q2 for both 2025 and 2026"
- [ ] "Analyze Q3 and Q4 net income for Amazon comparing 2025 vs 2026"

---

## 3. Multi-Filing-Type Tests

Test the new plural `filing_types` support.

### 3.1 Explicit Multi-Filing-Type Requests
- [ ] "Combine the 10-K and 10-Q data for Google's 2026 revenue"
- [ ] "Show both annual and quarterly net income figures for Tesla"
- [ ] "Compare Google's 10-K full-year revenue with Q2 2026 10-Q quarterly revenue"
- [ ] "Get Microsoft's total expenses from both the annual filing and quarterly reports for 2026"

### 3.2 Quarterly vs Annual Context
- [ ] "How did Google's Q2 2026 quarterly revenue compare to the full-year 2025 annual revenue?"
- [ ] "What percentage of Meta's 2025 annual net income came from Q1 and Q2 combined?"

### 3.3 8-K Event-Based Queries
- [ ] "What major events did Google report in 8-K filings during Q2 2026?"
- [ ] "List any significant changes Tesla reported via 8-K in 2026"

---

## 4. Company Name and Alias Tests

Test improved company alias resolution.

### 4.1 Company Name Aliases
- [ ] "What was Alphabet's Q1 2026 revenue?" (should resolve to GOOGL)
- [ ] "Alphabet Inc. Q2 2026 operating income"
- [ ] "Show Alphabet's quarterly earnings for Q1, Q2, Q3 2026"

### 4.2 Mixed Canonical and Alias Names
- [ ] "Compare Google's Q1 2026 with Alphabet's Q1 2026" (same company, should be understood)
- [ ] "Google's 2025 revenue vs Alphabet's 2026 revenue"

### 4.3 Other Company Aliases (if available)
- [ ] "Facebook's Q2 2026 revenue" (should resolve to Meta)
- [ ] "Elon Musk's Tesla Q3 2026 revenue"

---

## 5. Complex Multi-Dimensional Queries

Test combinations of quarters, filing types, and companies.

### 5.1 Multi-Dimensional: Quarter × Year × Filing Type
- [ ] "Get Google's Q1 2026 10-Q quarterly revenue and 2025 10-K annual revenue"
- [ ] "Combine Meta's Q2 2025 10-Q and Q2 2026 10-Q earnings reports"
- [ ] "Show Apple's Q3 2026 10-Q net income plus full-year 2026 10-K net income"

### 5.2 Multi-Dimensional: Multiple Quarters × Years × Companies
- [ ] "Compare Google's Q1 and Q2 2026 vs Meta's Q1 and Q2 2026 revenue"
- [ ] "Show Apple Q1 2025, Q1 2026, Tesla Q1 2025, and Tesla Q1 2026 quarterly revenue"

### 5.3 Segment and Geographic Analysis Across Quarters
- [ ] "What was Google's North America segment revenue in Q1, Q2, Q3, and Q4 2026?"
- [ ] "Apple's Europe segment operating income for Q2 2025 and Q2 2026"
- [ ] "How did Meta's Asia-Pacific segment perform in Q1 and Q2 compared to Q1 and Q2 2025?"

---

## 6. Edge Cases and Error Handling

### 6.1 Ambiguous/Missing Data
- [ ] "Show revenue for all quarters of 2026 for Google" (should return all Q1-Q4)
- [ ] "Meta's net income from all quarters" (should clarify year if needed)
- [ ] "Compare Q1 across all years we have data for" (should return 2024, 2025, 2026 if available)

### 6.2 Non-Existent Data Handling
- [ ] "Google's Q5 2026 revenue" (should return "no such quarter")
- [ ] "Tesla's Q1 2099 earnings" (should indicate year is too far in future)
- [ ] "Apple's Q2 1999 financial data" (should indicate historical data not available)

### 6.3 Vague Queries (Fallback to Web Search)
- [ ] "What was Google's latest quarterly earnings?" (should infer most recent Q available)
- [ ] "Meta's most recent earnings report" (should web-search if needed)
- [ ] "How is Tesla performing this year?" (should summarize available 2026 data)

---

## 7. Per-Sub-Query Scoping Tests

Verify that individual sub-queries don't get diluted by global `requested_fiscal_quarters`.

### 7.1 Granular Quarter Requests
- [ ] "Google's Q1 2026 net income and operating income" (both should use only Q1 data)
- [ ] "Meta's Q2 revenue, Q3 revenue, and Q4 revenue for 2026" (each should use its specific quarter)

### 7.2 Mixed Granularity
- [ ] "Google's 2025 total revenue and Q1 2026 revenue" (one annual, one quarterly)
- [ ] "Apple's full-year 2025 and Q2 2026 quarterly earnings"

---

## 8. Window-Collapse Behavior (Segment/Geographic Queries)

Test the generalized window-collapse logic for multiple filing types and quarters.

### 8.1 10-K Segment Analysis (Multi-Year Window)
- [ ] "Google's North America segment revenue in 2024, 2025, 2026" (should collapse to 3-year window for annual data)

### 8.2 10-Q Segment Analysis (Quarterly)
- [ ] "Apple's iPhone segment revenue Q1 2026, Q2 2026, Q3 2026"

### 8.3 Mixed Filing Types with Segment/Geographic
- [ ] "Combine Google's 10-K annual Americas segment data with 10-Q quarterly Asia segment data for 2026"

---

## 9. Fiscal Calendar Alignment Tests

Test the fiscal-quarter-misalignment warning for companies with non-calendar fiscal years.

### 9.1 Calendar-Year Companies (Jan-Dec)
- [ ] "Google's Q2 2026" (standard July reporting, should align)
- [ ] "Meta's Q1 2026" (standard April reporting, should align)

### 9.2 Non-Calendar Fiscal Year Companies
- [ ] "Walmart's Q1 2026" (fiscal year ends Jan 31, Q1 = Feb-Apr)
- [ ] "Target's Q2 2026" (fiscal year ends Jan 31, Q2 = May-Jul)

### 9.3 Mixed Fiscal Calendars
- [ ] "Compare Google's Q1 2026 with Walmart's Q1 2026" (should note calendar mismatch in comparison_span_details)

---

## 10. Answer Quality and Coherence Tests

Test that generated answers properly handle multiple quarters/filing types.

### 10.1 Multi-Quarter Answers
- [ ] "Compare Q1, Q2, Q3 2026 Google revenue" → should show clear quarter-by-quarter breakdown
- [ ] "Q1 2025 vs Q1 2026 Meta earnings" → should clearly label which is prior year, which is current

### 10.2 Multi-Filing-Type Answers
- [ ] "Google 10-K 2025 and 10-Q Q2 2026 revenue" → should clearly distinguish annual vs quarterly
- [ ] "Apple's quarterly and annual net income for 2026" → should explain they're different metrics

### 10.3 Comparison Span Details
- [ ] Multi-quarter queries should show `comparison_span_details` indicating quarters searched
- [ ] Multi-filing-type queries should show `comparison_span_details` indicating filing types searched
- [ ] Mixed queries should clearly document both dimensions

---

## 11. Web Search Integration Tests

Test web-search fallback when vectordb has insufficient data.

### 11.1 Recent Quarter Data (May Require Web Search)
- [ ] "Google's Q3 2026 earnings report" (may need web search for most recent quarters)
- [ ] "Meta's latest quarterly earnings announcement"

### 11.2 Company Name in Web Search
- [ ] "Alphabet Q1 2026 earnings" (should correctly search for "Alphabet", not "Google Alphabet")
- [ ] "Facebook Q2 2026 revenue" (should search for "Meta", not "Facebook")

### 11.3 Multi-Query Web Search
- [ ] "Google Q1 2026 and Q2 2026 earnings" (should include "earnings report" signal)
- [ ] "Apple Q3 2025 vs Q3 2026 revenue" (should append "earnings report" to queries)

---

## 12. Comparison Mode Tests (if using /compare endpoint)

### 12.1 Single-Dimension Comparisons
- [ ] "Compare Google and Meta Q2 2026 revenue"
- [ ] "Tesla and Ford Q1 2026 net income"

### 12.2 Multi-Dimension Comparisons
- [ ] "Compare Google's Q1 and Q2 2026 with Meta's Q1 and Q2 2026"
- [ ] "Apple vs Microsoft revenue for 2024, 2025, 2026"

---

## Test Execution Checklist

When running these tests, verify:

- [ ] **Query Parsing**: Logs show correct `requested_fiscal_quarters` (list) and `filing_types` (list) extraction
- [ ] **Retrieval**: Logs show all quarters and filing types were queried (not just the first)
- [ ] **Deduplication**: No duplicate chunks in final results
- [ ] **Per-Sub-Query Scoping**: Sub-query quarters don't pollute global requests
- [ ] **Company Detection**: Aliases are resolved correctly; no company-name duplication in web search
- [ ] **Answer Quality**: Generated text correctly references all requested dimensions
- [ ] **Comparison Span**: Multi-dimensional queries include clear `comparison_span_details`
- [ ] **No Regression**: Single-dimension queries (sections 1.1–1.3) return identical results to pre-change behavior
- [ ] **Fiscal Calendar**: Non-calendar-year companies show appropriate warnings

---

## Logging Locations to Monitor

When debugging test failures:

1. **backend/rag/graph/nodes.py:preprocess_and_analyze_query()** → logs `requested_fiscal_quarters`, `filing_types`, `optimized_query`
2. **backend/rag/graph/nodes.py:retrieve()** → logs each search pass (year × filing_type × quarter combinations)
3. **backend/rag/graph/nodes.py:_build_type_quarter_passes()** → logs the passes generated
4. **backend/rag/graph/nodes.py:_is_direct_vectordb_mode()** → logs whether direct-DB or sub-query mode is active
5. **backend/rag/graph/nodes.py:fiscal-quarter-misalignment warning** → logs quarter alignment issues per company
6. **backend/app/utils/company_mapping.py:detect_tickers_in_query()** → logs alias resolution
7. **backend/rag/graph/nodes.py:web_search()** → logs optimized web queries and domain filtering

---

## Example Log Outputs to Expect

### Multi-Quarter Query
```
Extract quarters from "Google Q1 vs Q3 2026":
  requested_fiscal_quarters = [1, 3]
Retrieve passes:
  for year in [2026]:
    for ft, q in [(None, 1), (None, 3)]:
      hybrid_search(..., fiscal_quarter=1)
      hybrid_search(..., fiscal_quarter=3)
```

### Multi-Filing-Type Query
```
Extract filing types from "combine 10-K and 10-Q":
  filing_types = ["10-K", "10-Q"]
Retrieve passes:
  for year in [2026]:
    for ft, q in [("10-K", None), ("10-Q", 1)]:  # Only 10-Q gets quarter filter
      hybrid_search(..., filing_type="10-K")
      hybrid_search(..., filing_type="10-Q", fiscal_quarter=1)
```

### Company Alias Resolution
```
detect_tickers_in_query("Alphabet Q1 2026"):
  Aliases for GOOGL: ['google', 'alphabet']
  Found ticker: GOOGL
```

---

## Notes

- Tests should be run against **live Qdrant, OpenAI, and Postgres infrastructure**
- Record response times for regression analysis
- Capture `comparison_span_details` for each multi-dimensional query
- Note any grading "insufficient" outcomes (may indicate retrieval gaps)
- Compare answer quality between single- and multi-dimensional versions
