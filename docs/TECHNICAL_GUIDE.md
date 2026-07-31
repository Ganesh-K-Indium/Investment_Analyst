# Investment Analyst — Technical Guide

Complete reference for every subsystem in the backend. Written to be read top-to-bottom for onboarding, or jumped into via the table of contents for a specific area.

## Table of contents

1. [System overview](#1-system-overview)
2. [Backend foundations (app/main.py)](#2-backend-foundations)
3. [Database schema and migrations](#3-database-schema-and-migrations)
4. [RAG pipeline](#4-rag-pipeline)
5. [Ingestion pipeline](#5-ingestion-pipeline)
6. [Quant subsystem](#6-quant-subsystem)
7. [Form 4 insider-trading pipeline](#7-form-4-insider-trading-pipeline)
8. [Macro data pipeline](#8-macro-data-pipeline)
9. [API reference](#9-api-reference)
10. [Services layer](#10-services-layer)
11. [Integration connectors](#11-integration-connectors)
12. [Deployment](#12-deployment)
13. [Known limitations and architectural notes](#13-known-limitations-and-architectural-notes)

---

## 1. System overview

Investment Analyst is one FastAPI backend fronting three largely-independent subsystems that share the same Postgres database and, in the RAG case, a Qdrant vector store:

- **RAG subsystem** — document-grounded Q&A over SEC filings (10-K/10-Q/8-K), built as a LangGraph state machine with filing-type and fiscal-calendar awareness layered in.
- **Quant subsystem** — a separate LangGraph *supervisor* multi-agent system for live market/technical/options/research queries, each sub-agent backed by its own standalone MCP (Model Context Protocol) server process.
- **Insider trading (Form 4) and macro data pipelines** — standalone ingestion jobs whose output is consumed by the RAG subsystem (as retrievable documents and as tool context, respectively), not by the quant supervisor.

Everything else — portfolios, chat history, analyst report authoring/publishing, data-source integrations (SharePoint/Google Drive/S3/etc.) — is plain CRUD + service-layer business logic over the same Postgres database, largely independent of which "agent" produced the content being stored.

---

## 2. Backend foundations

**File:** `app/main.py`

FastAPI app (`title="Investment Analyst API"`, `version="2.1.0"`, `redirect_slashes=False`). Middleware: permissive CORS (all origins/methods/headers) plus a custom `RequestLoggingMiddleware` logging method/path/status/elapsed-ms for every request.

### Startup sequence (`@app.on_event("startup")`)

1. `init_db()` — see [§3](#3-database-schema-and-migrations).
2. Creates a single shared `AsyncPostgresSaver` LangGraph checkpointer (`langgraph.checkpoint.postgres.aio`), connected via the same `DATABASE_URL` as everything else (translated to a plain `postgresql://` psycopg3 connection string via `_to_sync_url()`), with `.setup()` called once to create/upgrade the `checkpoints`/`checkpoint_blobs`/`checkpoint_writes`/`checkpoint_migrations` tables. This is shared between the RAG graph and the Quant supervisor.
   - **Cross-agent isolation**: a checkpointer partitions stored state purely by `thread_id` — it has no concept of "which graph" wrote a checkpoint. Since this app's session model allows one portfolio session's ID to be attached to either a `rag` or `quant` `ChatSession`, RAG and Quant invocations always prefix the LangGraph `thread_id` they pass to the checkpointer (`f"rag:{thread_id}"` / `f"quant:{session_id}"`) so the two can never collide in the shared checkpoint tables, even if the same underlying session/thread ID were ever reused across both. Verified directly: invoking a compiled graph twice with the same underlying ID but different prefixes produces fully isolated state. Everywhere else (DB records, portfolio-session mapping, API responses) still uses the original unprefixed ID — only the checkpoint storage key is prefixed.
3. Builds the RAG graph (`rag.graph.builder.BuildingGraph().get_graph(checkpointer=...)`) and injects the compiled agent into `app/api/rag.py`'s module state via `set_agent()`.
4. Initializes the quant multi-agent system (`app.services.stock_agent.initialize_stock_agents(checkpointer=checkpointer)` — passed the same shared checkpointer instance), injecting the resulting supervisor/status into `app/api/quant.py` via `set_stock_supervisor()`/`set_agents_status()`. Failures here are caught and logged as warnings — non-fatal; quant endpoints then return 503 rather than crashing the whole app.
5. Checks `data/macro/metadata.json`; if missing, schedules a one-off background `run_ingestion()` task.
6. Starts a permanent background task, `macro_sync_loop()` — sleeps 24h, then re-runs `run_ingestion()`, forever, wrapped in try/except so one failure doesn't kill the loop.

### Shutdown sequence

Calls `cleanup_stock_agents()`, exits the RAG checkpointer's async context manager, calls `graph_obj.cleanup()`.

### Routers registered

`auth` (`/auth`) · `reports` (`/reports`) · `portfolios` (`/portfolios`) · `rag` (no prefix — `/ask`, `/compare`, `/alpha`) · `integrations` (`/integrations`) · `quant` (`/quant`) · `chats` (`/chats`) · `form4` (`/form4`) · `edgar` (`/edgar`)

Plus root routes: `GET /` (service directory), `GET /health` (aggregated RAG agent / stock supervisor / DB status).

**Auth is enforced on every route.** Every endpoint across all 8 routers (portfolios, chats, reports, integrations, rag, quant, edgar, form4) requires a valid JWT (`get_current_user` dependency) — resolved via `app/auth/deps.py`'s two verification helpers: `verify_user_id_matches(user_id, current_user)` for routes carrying an explicit `user_id` field (403 on mismatch), and `verify_owner(owner_user_id, current_user)` for resource-id-only routes, checked immediately after fetching the resource and before any mutation (404 on mismatch — deliberately, so another user's resource existence isn't leaked). No request/response schemas changed to add this. Verified live against a running server: unauthenticated requests → 401, authenticated-but-mismatched `user_id` → 403, another user's portfolio by ID → 404, the actual owner → 200.

---

## 3. Database schema and migrations

**Files:** `app/database/models.py`, `app/database/connection.py`, `alembic/versions/`

### Connection layer

- `DATABASE_URL` (default `postgresql://investment_analyst:investment_analyst@localhost:5432/investment_analyst`), rewritten internally: `_to_async_url()` → `postgresql+asyncpg://` for the runtime engine, `_to_sync_url()` → plain `postgresql://` (psycopg2) for Alembic.
- Runtime: `engine = create_async_engine(_to_async_url(DATABASE_URL), pool_pre_ping=True)`, `SessionLocal = async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)` — every route/service uses this async engine exclusively.
- `init_db()` prefers running Alembic to head; falls back to a one-off sync `Base.metadata.create_all()` if `alembic.ini` is missing. Handles the edge case of an existing DB with a `portfolios` table but no `alembic_version` table by stamping `head` first (so Alembic doesn't try to replay history against an already-populated schema).
- `get_db()` — async context manager, commits on success / rolls back on exception (used for manual/internal sessions).
- `get_db_session()` — async generator, the FastAPI DI version; does **not** auto-commit — the route/service is responsible for committing.

### Tables

| Table | Model | Purpose |
|---|---|---|
| `users` | `User` | Analyst/fund-manager accounts. `role` string default `"analyst"` (values: analyst/fund_manager/admin). **No enforced FK from other tables** — `user_id` elsewhere is a loose string, not referentially tied to `users.id`. |
| `portfolios` | `Portfolio` | Named ticker collections for RAG scoping. `company_names` JSON list. Cascades to `sessions` and `chat_sessions`. |
| `sessions` | `Session` | Legacy portfolio-session tracker (thread_id PK), kept for backwards compatibility. |
| `chat_sessions` | `ChatSession` | One row per conversation thread (RAG or Quant). `agent_type` enum (rag/quant), `session_metadata` JSON, `summary`/`summary_updated_at` for cached LLM summaries. Cascades to `chat_messages`. |
| `chat_messages` | `ChatMessage` | Individual turns. `role` enum (user/assistant/system), `message_metadata` JSON (renamed from `metadata` to avoid clashing with SQLAlchemy's reserved attribute). |
| `consolidated_summaries` | `ConsolidatedSummary` | Cross-session AI-generated rollup summaries, `session_ids` JSON list, `detected_type` (rag/compare/quant). |
| `analyst_reports` | `AnalystReport` | Published/draft report records: `content_markdown` + `content_html` (dual storage — markdown is *derived* from HTML, not authoritative), `image_urls` JSON, `status` enum (draft/published, stored as plain string via `native_enum=False`), `search_vector` (Postgres `TSVECTOR`, added in migration 014). |
| `report_draft_items` | `ReportDraftItem` | Server-side "clipboard" staging area for building a report before it's assembled (replaces what used to be browser localStorage). `item_type` (text/image/summary), `html` (sanitized), `sort_order` for drag-reorder. |
| `integrations` | `Integration` | External connector configs — `vendor` (sharepoint/google_drive/onedrive/confluence/azure_blob/aws_s3/sftp), `credentials` JSON, `status`. |
| `form4_transactions` | `Form4Transaction` | SEC Form 4 insider-trading transaction records — see [§7](#7-form-4-insider-trading-pipeline). Composite index on `(issuer_symbol, transaction_date)`. |

### Enums

- `AgentType`: `rag` | `quant`
- `MessageRole`: `user` | `assistant` | `system`
- `ReportStatus`: `draft` | `published` (stored as plain string, `native_enum=False`)

`AgentType`/`MessageRole` use `SQLEnum(..., values_callable=lambda enum_cls: [e.value for e in enum_cls])` so Postgres stores the lowercase value, not the Python member name — this was a real bug fixed during the async/Postgres migration (SQLAlchemy defaults to serializing by `.name`, which SQLite silently tolerated but Postgres's native enum type rejected).

### Migration chain (17 revisions, in dependency order)

```
001_initial → 002_chat_history → aca5bd3b31cf (no-op stub) → 1578dc4794bb (no-op stub)
  → 20b80069323a (adds chat_sessions.summary) → 003_add_session_metadata
  → 004_add_form4_transactions → 005_add_consolidated_summaries
  → 006_add_form4_document_type → 007_add_form4_has_common_stock
  → 008_add_users → 009_add_report_draft_items → 010_add_analyst_reports
  → 011_simplify_analyst_reports → 012_draft_items_portfolio_id
  → 013_add_content_html → 014_postgres_fulltext_search
```

Notes: `aca5bd3b31cf` and `1578dc4794bb` are empty autogenerated stubs from an early `alembic revision --autogenerate` run, left in the chain as no-ops. Migration `010` originally created `analyst_reports` with a SQLite FTS5 virtual table + triggers (SQLite-only path); `011` simplified the model (dropped `recommendation` and related columns); `014` is the Postgres equivalent — adds a `TSVECTOR` column + trigger function (`analyst_reports_search_vector_update()`, weighted: company_name='A', content_markdown='B') + GIN index, and backfills existing rows.

---

## 4. RAG pipeline

**Core files:** `rag/graph/{builder,nodes,edges,state}.py`, `rag/vectordb/client.py`, `rag/prompts/prompts.py`, `rag/graph/semantic_cache.py`, `app/services/vectordb_manager.py`

### Graph shape

The RAG pipeline is a LangGraph state machine (state defined in `rag/graph/state.py`'s `GraphState` TypedDict). Rough flow for a standard `/ask` query:

```
preprocess_and_analyze_query → retrieve → grade_documents
  → (sufficient) → generate
  → (insufficient) → integrate_web_search → generate
```

Comparison/segment/geographic queries take a **direct-vectordb shortcut** (`_is_direct_vectordb_mode()` in `edges.py`): they skip grading and web search entirely and go `retrieve → generate`, on the rationale that pre-optimized template queries against SEC filings are authoritative enough not to need a relevance check — this bypass is a known risk area if 10-Q/8-K data returns thin results (see [§13](#13-known-limitations-and-architectural-notes)).

Two other "framework" modes are layered on the same graph: **ALPHA** (`alpha_dimension_retrieve` → `alpha_generate_report`, a 5-dimension equity research report) and **Scenario** (Bull/Bear/Base case analysis). A **Macro** mode also exists as a step-by-step pipeline (query understanding → deterministic calculation → LLM formatting) for FRED-data questions.

### Query preprocessing (`preprocess_and_analyze_query`)

Three paths, in priority order:

1. **Comparison mode** — pre-built templates (`generate_comparison_subqueries`), no LLM call. Used for 2-3 company comparison requests.
2. **Segment/geographic mode** — keyword-detected (`detect_segment_or_geographic_query`), pre-built templates (`generate_segment_subqueries`/`generate_geographic_subqueries`), no LLM call.
3. **Universal LLM fallback** — a single structured-output call (`get_universal_sub_query_analyzer`, gpt-4o-mini) extracts companies, query type, requested years, sub-queries, an `optimized_query` retrieval rewrite (direct-mode only), and `filing_type_hints` — all in one call, no extra round-trips.

All three paths also resolve **`filing_types`** (a list — union of the LLM's `filing_type_hints` and `detect_filing_types_in_query()`'s pure keyword heuristic; empty list if the query doesn't imply a specific type — search all types) and **`requested_fiscal_quarters`** (a list, via `extract_fiscal_quarters_from_question()` — parses every "Q1"/"Q3"/"first quarter" mention in the question into 1-4 integers, no calendar interpretation yet since the target ticker isn't resolved at this point). Both are plural specifically so a question like *"compare Q1 vs Q3"* or *"combine the 10-K and 10-Q figures"* resolves to every value mentioned instead of silently keeping only the first match — each still degenerates to a single-element list (identical behavior to the old singular fields) for the common one-quarter/one-type case.

### Retrieval (`retrieve`)

Resolves target ticker(s) from `company_filter` (portfolio scope) → explicit `ticker` override → LLM-detected companies, then queries each ticker's own Qdrant collection (`ticker_{ticker}`) via `hybrid_search()`.

**Filing-type-aware routing**: `hybrid_search()` accepts optional `filing_type`, `period_end_date`, and `fiscal_quarter` filters (still single-valued at the Qdrant-filter level) — all additive, all default to `None` (search everything), so a collection or query with no filing-type signal behaves exactly as it did before this was added.

**Multi-quarter / multi-filing-type search passes** (`_build_type_quarter_passes(filing_types, requested_fiscal_quarters)`): builds the list of `(filing_type, fiscal_quarter)` combinations to query — one search pass per combination, mirroring the pre-existing "one query per requested year" pattern (`for year_filter in requested_years`) so an explicitly requested quarter or filing type can't get starved out by another one scoring higher in a single combined query. The quarter filter only ever applies on a `"10-Q"` pass (it's meaningless, and untagged, on 10-K/8-K chunks). Degenerates to one pass in the common single-type/no-quarter case — zero added query volume. In **sub-query mode**, each sub-query is additionally scoped to *its own* quarter (`extract_fiscal_quarters_from_question(sq)`, falling back to the full requested list only if the sub-query text doesn't name one) — otherwise a "Q2" sub-query would also pull in Q1 chunks and dilute the grader's view of what's actually available for Q2.

**The 3-year comparative-window collapse** (segment/geographic queries spanning multiple years) is evaluated independently per entry in `filing_types` (looping `filing_types or [None]`) and the resulting year sets are unioned:
- `None`/`"10-K"` → unchanged legacy behavior: span=2 collapses to the last year only (one 10-K covers all 3), span>2 collapses to first+last year.
- `"10-Q"` → no collapse (a 10-Q is a single quarter, no 3-year comparative structure) — queries each year individually, and flags `comparison_spans_multiple_filings` if more than one year is involved.
- `"8-K"` → no collapse (single event, no multi-year structure at all).

When more than one filing type is combined (e.g. `["10-K", "10-Q"]`), the union may issue a few extra queries for years outside one type's own window — harmless no-result queries, not incorrect ones.

**Fiscal-quarter filtering** uses a safety-net wrapper (`_hybrid_search_with_quarter_fallback`): tries the exact `fiscal_quarter` filter first, and if that returns zero results (e.g. older data ingested before fiscal_quarter tagging existed), transparently retries without the filter rather than silently returning nothing.

**Cross-company fiscal-quarter misalignment detection**: when a query targets multiple tickers and one or more specific fiscal quarters, if those tickers have different fiscal-year-end months (`app/utils/company_mapping.py`'s `TICKER_TO_FISCAL_YEAR_END_MONTH` table, ~130 explicitly-enumerated large-cap tickers, default 12 for unlisted ones), `comparison_spans_multiple_filings` is set with one concrete note per misaligned quarter (e.g. *"AAPL (October-December), AMZN (January-March)"*) — this threads into the generation prompt's anti-hallucination clause so the LLM explicitly warns the user rather than presenting a clean comparison.

**Per-sub-query company scoping** (`detect_tickers_in_query`) checks *every* known alias for a ticker (`get_company_aliases()`, e.g. `googl` → `["google", "alphabet"]`), not just the single canonical `TICKER_TO_COMPANY` name — otherwise a sub-query worded "Alphabet total revenues..." wouldn't match ticker `googl` and would fall back to querying every company in the request for every sub-query.

### Fiscal calendar utilities (`app/utils/company_mapping.py`)

- `get_fiscal_year_end_month(ticker)` — lookup, default 12 (calendar year).
- `get_most_recent_filed_fiscal_year(ticker, as_of=None)` — replaces the old blanket `current_year - 1` assumption; accounts for non-calendar filers and a ~4-month filing-lag buffer.
- `get_fiscal_quarter(period_end_date, ticker)` — ground-truth derivation (not a guess) from a *real* period-end date to a fiscal quarter number (1-4), by matching the date's month against the four expected quarter-end months for that ticker's fiscal calendar.
- `get_fiscal_quarter_calendar_span(ticker, fiscal_quarter)` — human-readable month range (e.g. "October-December") for prompt explanations.
- `get_company_aliases(ticker)` — every known company-name alias for a ticker (e.g. `googl` → `["google", "alphabet"]`), for callers matching free text against a company name. `TICKER_TO_COMPANY`/`get_company_name()` only store one canonical name per ticker; `COMPANY_TO_TICKER` (its reverse) gets extra manual aliases added where a company has more than one commonly-used name. A prior duplicate dict key (`'googl'` defined twice in `TICKER_TO_COMPANY`) silently dropped the `'alphabet'` mapping entirely — Python keeps only the last literal key with no warning — so "Alphabet" as a company name resolved to no ticker at all until fixed.

### Vector search (`rag/vectordb/client.py`)

- One Qdrant collection per ticker (`ticker_{ticker}`), **not** fragmented further by filing type — filtering happens via payload indexes, keeping cross-filing-type queries ("reconcile Q3 actuals against the last 10-K guidance") in a single search.
- Hybrid search: dense (OpenAI `text-embedding-3-large`, 3072-dim) + sparse (BM25 via FastEmbed) prefetch, fused with Qdrant's RRF (Reciprocal Rank Fusion).
- Payload indexes: `source_file`, `company`, `content_type`, `content_hash`, `image_content_hash`, `page_num` (INTEGER), `year` (INTEGER), `ingestion_timestamp`, `filing_type` (KEYWORD), `period_end_date` (KEYWORD), `fiscal_quarter` (INTEGER) — new collections get all of these at creation; existing collections get missing ones backfilled automatically (`ensure_collection_exists()`).
- `generate_embeddings_for_ingestion()` batches embedding calls concurrently for ingestion throughput.

### Semantic cache (`rag/graph/semantic_cache.py`)

A `SemanticCache` class exists (embedding-similarity cache keyed on query text) but **is not currently wired into any live code path** — it's dead code today. It was fixed for correctness anyway (folding `ticker`/`requested_years`/`filing_type` into both the embedded cache key and a stored `filter_signature` checked on lookup) so that if it's wired in later, it won't serve a stale answer across different resolved filters.

### Prompts (`rag/prompts/prompts.py`)

- Base RAG generation prompt explicitly distinguishes 10-K (annual, audited, 3-year comparative) / 10-Q (quarterly, unaudited, no comparative table) / 8-K (single event, not a financial-statement source), plus a conditional anti-hallucination clause that only appears when `comparison_spans_multiple_filings` is set (to avoid diluting normal single-period answers with unnecessary caveats).
- The universal sub-query analyzer's `optimized_query` rewrite (used only in direct-mode retrieval) preserves filing-type-implying language ("latest quarter", "recent announcement") rather than stripping it during query expansion, and stays consistent with whatever `filing_type_hints` it set.
- `get_alpha_performance_chain()` computes its fiscal-year anchor via `get_most_recent_filed_fiscal_year(ticker)` rather than a blanket `current_year - 1`.
- Web-search fallback query (in `nodes.py`'s `web_search()`) varies its SEC filing-type literal by detected/inferred filing type instead of always assuming "10-K".
- `integrate_web_search()`'s query construction avoids duplicating the company name when it's already embedded in the grader's `missing_data_summary` (previously produced queries like "Google Google Q2 2026 net income..."), appends "earnings report" to bias toward an actual reporting article, and excludes `investopedia.com` specifically for this fallback (its content skews toward "what is net income"-style definitional explainers rather than a company's actual reported figures — a risk other `TRUSTED_FINANCIAL_DOMAINS` uses don't share, so it stays in the list everywhere else).
- **`is_comparison_mode` checkpointer leak (fixed)**: the graph's Postgres checkpointer persists state per `thread_id` across turns, merging each turn's input dict on top of the last checkpoint rather than replacing it. `/compare` (`app/api/rag.py`) sets `is_comparison_mode: True` plus `comparison_company1/2/3` and `year_start`/`year_end`; `/ask`'s input dict never reset these, so if a thread was ever used for `/compare`, every later `/ask` question on that same thread silently routed through the annual-only 10-K comparison templates instead of the real analyzer. `/ask` now explicitly resets all of these fields (and `/compare` resets `ticker`) on every call.

---

## 5. Ingestion pipeline

**Core files:** `ingestion/pdf_processor1.py`, `ingestion/table_extractor.py`, `ingestion/image_data_prep.py`, `ingestion/ingest_pdf.py`, `ingestion/edgar_fetcher.py`, `ingestion/batch_ingest.py`, `scripts/ingest_ticker.py`

### Resolution priority for filing metadata

For every filing (whether EDGAR-fetched or user-uploaded), three fields are resolved with a strict, non-silent priority order:

| Field | Priority order | Fallback behavior |
|---|---|---|
| `filing_type` | explicit param → document cover-page text (`FORM 10-K`/`10-Q`/`8-K` regex) → filename token → `"10-K"` | Only defaults to 10-K as an absolute last resort, with a **loud warning** in the ingestion messages — never silent. |
| `period_end_date` | explicit param (e.g. EDGAR's `reportDate` — SEC ground truth) → cover-page "for the [fiscal year/quarterly period] ended..." phrase (parsed via `dateutil`) → *left unset* | Never guessed from a filename — an unresolved date stays `None` rather than being wrong. |
| `fiscal_quarter` | derived from `period_end_date` + ticker's fiscal calendar, **only for 10-Q** filings | `None` for 10-K (spans the whole year) and 8-K (single event) — tagging either would be misleading. |

`year` (the Qdrant filter field) is derived from `period_end_date`'s calendar year, **not** the EDGAR filename's filing date — this was a real bug found and fixed: EDGAR filenames embed the *filing* date, and a 10-K covering fiscal year 2025 is typically filed in *2026*, so tagging it `year=2026` made it invisible to any query about "2025" data.

### Text extraction (`pdf_processor1.py`)

- Plain `page.get_text("text")` per page, chunked via `RecursiveCharacterTextSplitter.from_tiktoken_encoder(chunk_size=1024, chunk_overlap=300)`.
- **Table extraction** (`table_extractor.py`, adapted from a prior internal parser): `page.find_tables()` (PyMuPDF) locates table areas; vertical ruling lines from the PDF's own drawing paths determine true column boundaries (more accurate than `find_tables()` alone, which can miss a column whose divider only spans part of the table height); cell text is pulled from each `[col_x0..col_x1] × [row_y0..row_y1]` rectangle. Falls back to `find_tables()`'s own cell extraction for borderless/text-only tables. Detected tables are rendered as a GitHub-flavored markdown block and appended to the page's chunk text (`[TABLES]\n| Metric | 2024 | 2023 |...`), preserving column alignment that plain text extraction would otherwise flatten and lose.
- Deduplication: whole-document dedup by SHA-256 content hash (`calculate_content_hash`), checked before re-processing text.

### Image extraction and captioning (`image_data_prep.py`)

- Images extracted per page via PyMuPDF, enhanced (contrast/sharpness/brightness, upscaled if <512px, downscaled if >2048px), saved as `financial_img_{xref}_page{page_num}_{hash}.png`.
- OCR (Tesseract, via `pytesseract`) + GPT-4o vision analysis combined: OCR text is fed into the vision prompt so the model cross-references extracted numbers rather than guessing. The vision prompt is **filing-type-aware** (parameterized by `filing_type` passed into the `ImageDescription` constructor) rather than hardcoded to assume every image is from a 10-K.
- **Per-image dedup** (`find_new_image_hashes()`): checks each image's content hash individually and concurrently — a single shared image (e.g. a repeated company logo across filings) is excluded on its own without causing every *other* genuinely-new chart/table in the same document to be skipped. (A prior version short-circuited on the first hash match and dropped the whole document's images — fixed.)
- Guards against an empty-upsert crash: if every candidate image gets classified decorative/invalid by the vision model, ingestion logs that and moves on instead of sending an empty point list to Qdrant (which errors).

### EDGAR fetcher (`edgar_fetcher.py`)

`SecEdgarFetcher` (async context manager) fetches a ticker's filing list from `data.sec.gov/submissions/CIK{cik}.json`, renders each to PDF via a two-step process that sidesteps SEC's anti-bot WAF: fetch the HTML via `httpx` (proven to work), then hand that content to Playwright's `page.set_content()` for local rendering only — **the browser itself never makes a request to sec.gov**, since `page.goto()` gets blocked as an "undeclared automated tool" even with a legitimate User-Agent.

- `list_filings()` — metadata-only listing (form, filing date, `reportDate`/period_end_date, accession, URL, would-be pdf_path) with no network-heavy download/render/ingest work — used by the interactive CLI.
- `fetch_filings()` — the full pipeline: download/render/ingest, with an optional `accession_filter` set to process only a hand-picked subset (used by the CLI after the user selects specific filings) instead of everything matching a date range.
- Captures EDGAR's `reportDate` field (previously unused — only `filingDate` was captured) and passes it to `ingest_pdf()` as the authoritative `period_end_date`.

### Interactive ingestion CLI (`scripts/ingest_ticker.py`)

```
python scripts/ingest_ticker.py AAPL
python scripts/ingest_ticker.py AAPL --form-types 10-Q --limit 8
python scripts/ingest_ticker.py AAPL --all --no-ingest
```

Lists available filings with a **Period** column (`FY2024` for 10-Ks, `2025Q3` for 10-Qs — the latter using the ticker's *own* fiscal calendar, not a naive calendar-quarter assumption) and an availability summary line. Selection accepts row numbers (`3`), ranges (`1-4`), period labels (`FY2024`, `2025Q1`) — mixed freely, comma-separated — plus `all`/`q`.

### Batch ingestion (`ingestion/batch_ingest.py`)

Resumable CLI for many-PDF ingestion with a persistent JSON progress file (`--manifest`/`--dir` modes); re-running the same command skips already-succeeded files and (optionally, via `--retry-failed`) retries only the failed ones.

---

## 6. Quant subsystem

**Core files:** `quant/stock_agent/{main_agent,api_server}.py`, `quant/stock_agent/stock_exchange_agent/subagents/*/langgraph_agent.py`, `quant/{options_mcp,Stock_Analysis,research_mcp,yahoo-finance-mcp}/`

A separate LangGraph **supervisor** (`langgraph_supervisor.create_supervisor`, `app/services/stock_agent.py`) routes user queries to 5 specialized sub-agents, each a LangGraph react-agent (`create_agent`) connecting to its own standalone MCP server over `streamable-http`.

| Sub-agent | MCP server / port | Backing tech |
|---|---|---|
| `ticker_finder_tool` | none — Tavily search scoped to finance.yahoo.com | Converts company names → ticker symbols |
| `stock_information` | `yahoo-finance-mcp` :8565 | 14 tools: price/historicals/news/actions/financial statements/holder info/options/recommendations/target price/sentiment+prediction/5yr projection/P-E ratios |
| `technical_analysis_agent` | `Stock_Analysis/server_mcp.py` :8566 | 14 tools: SMA/RSI/MACD/Bollinger/volume/support-resistance (single + multi-stock comparison variants), composite chart, GPT-4o/Gemini chart-summary |
| `research_agent` | `research_mcp/server_mcp.py` :8567 | 9 tools: web search, analyst-rating aggregation, sentiment (TextBlob), MD&A sentiment, scenario generation (Bull/Bear/Base), in-memory TTL cache |
| `options_agent` | `options_mcp/server_mcp.py` :8568 | 3 tools: `analyze_options_chain` (deterministic analytics, no LLM reasoning over raw data), `get_oi_chart`, `get_options_expiration_dates` |

All 4 MCP servers are built on **FastMCP**, run as independent processes (`transport="streamable-http"`), and are started together by `docker-entrypoint.sh` in a container deployment, or manually per the README quick-start.

### Options intelligence deep-dive (`quant/options_mcp/analytics.py`)

Pure deterministic Python analytics (explicitly documented as "the LLM's role is ONLY to convert pre-computed structured analytics into natural language" — no LLM reasoning over raw option-chain tables): pulls Yahoo Finance chains (~15min delay) across up to 6 expirations; computes call/put notional, put/call ratio with sentiment bands, biggest trades by notional, support/resistance from strike concentration, IV skew, ATM concentration (pinning vs. breakout signal), unusual-volume detection (volume > 3× OI and > 500 contracts — OI itself is unreliable/often-zero from Yahoo, so volume is the effective signal), and "smart money" long-dated (>90 DTE) positioning classification. Documented limitations: no real intraday OI, no max pain, no Greeks, no dark-pool data.

### Supervisor initialization (`app/services/stock_agent.py`)

`initialize_stock_agents()` is idempotent, waits (5s timeout each) for the 4 MCP servers with per-server readiness tracking (not fail-hard), dynamically imports each sub-agent factory with its own try/except (one down MCP server doesn't prevent the others from initializing), and builds the supervisor only from whichever sub-agents actually came up. Requires a `checkpointer` to be passed in (raises if `None`) — uses the same shared `AsyncPostgresSaver` instance as the RAG graph (see [§2](#2-backend-foundations)), with `thread_id`s prefixed `quant:` at every invocation site to keep its checkpoint history fully isolated from RAG's.

---

## 7. Form 4 insider-trading pipeline

**Files:** `ingestion/Form4_Ingestion/{fetch,parse,save_xml,ingest}.py`, `rag/utils/Insights_Form4/{database,advisory_hub,advisory_analyst}.py`

A standalone pipeline (not part of the LangGraph supervisor) consumed from the RAG side, sharing the main app's async Postgres engine (`rag/utils/Insights_Form4/database.py` is a thin re-export shim over `app.database.connection`/`models`, not a separate database).

**Ingestion**: `SecEdgarFetcher` (Form4-specific, distinct from the filing-ingestion `edgar_fetcher.py`) paginates SEC EDGAR's Form 4 ATOM feed, resolves each filing to its XML document, parses it (`Form4Parser`) into issuer/reporting-owner/transaction records, filters to **common-stock-only** transactions (excludes preferred/warrant/option/note/convertible), dedups by SEC accession number, and handles **4/A amendments** by deleting prior rows for the same reporter+period before inserting the amendment's rows. A "dummy" placeholder row is inserted for filings with zero qualifying transactions, to avoid reprocessing them every run.

**Advisory analysis** (`advisory_hub.get_advisory_report(ticker, start_date, end_date)`): a raw-SQL grouped dedup query against `form4_transactions`, further merged in Python by normalized reporter name (handles spelling variants like "HENNESSY JOHN L" vs "Hennessy John L."), then `advisory_analyst.analyze_transactions()` computes a role-weighted (Officer ×2, Director ×1.5, 10% Owner ×1.2), dollar-weighted buy/sell signal score by transaction code (P=purchase strong positive, S=sale strong negative), and an LLM turns the aggregated signal into a natural-language recommendation/reasoning — surfaced in the ALPHA framework's "Alignment" dimension and via `POST /form4/ingest`.

---

## 8. Macro data pipeline

**Files:** `ingestion/ingest_macro_data.py`, `rag/utils/macro_tool.py`, `app/utils/macro_utils.py`

Fetches ~17 FRED series concurrently (`asyncio.Semaphore(5)`) — GDP (quarterly + annual), CPI, PCE, PPI, ECI, Fed Funds rate, and the full Treasury yield curve (GS1M through GS30). Each series is written atomically (`.tmp` + rename) to `data/macro/{indicator}.csv`, with `data/macro/metadata.json` tracking `last_sync`. An `fcntl`-based file lock prevents concurrent-process races (skipped gracefully on Windows).

`rag/utils/macro_tool.py` exposes this as a plain LangChain `@tool` (not MCP, not agent-wrapped) that a RAG-side chat agent can call directly — supporting flexible period strings ("Q2 2025", "2025-01"), staleness checks with auto-reingestion triggers, quarterly aggregation of monthly series, and yield-curve spread calculations, all with source attribution back to FRED.

**Requires `FRED_API_KEY`** — not present in `.env.example`; must be added manually or ingestion raises on startup.

---

## 9. API reference

### `/auth` — JWT authentication (the only router that actually enforces auth)

| Method | Path | Purpose | Auth |
|---|---|---|---|
| POST | `/auth/signup` | Register, returns access+refresh tokens | No |
| POST | `/auth/login` | Verify credentials, returns tokens | No |
| POST | `/auth/refresh` | Exchange refresh token for a new pair | No (refresh token is the credential) |
| GET | `/auth/me` | Current user profile | **Yes** |
| POST | `/auth/logout` | No-op confirmation (JWT is stateless) | **Yes** |
| PUT | `/auth/me` | Update name/password | **Yes** |

### `/portfolios`

| Method | Path | Purpose |
|---|---|---|
| POST | `/portfolios` | Create portfolio |
| GET | `/portfolios/{id}` | Fetch one |
| GET | `/portfolios/user/{user_id}` | List a user's portfolios |
| PUT | `/portfolios/{id}` | Update (name/tickers/description) |
| DELETE | `/portfolios/{id}` | Delete |
| POST | `/portfolios/sessions` | Create a portfolio session (thread_id) + linked chat session |
| GET | `/portfolios/sessions/{thread_id}` | Get session + its portfolio |

### `/ask`, `/compare`, `/alpha` — RAG (no path prefix)

| Method | Path | Purpose |
|---|---|---|
| POST | `/ask` | Portfolio-scoped document Q&A |
| POST | `/compare` | Multi-company (2-3) comparison |
| POST | `/alpha` | 5-dimension ALPHA report, per ticker in the portfolio |
| GET | `/health` | RAG agent initialized? |
| GET | `/capabilities` | Static capability listing |
| GET | `/sessions/{session_id}` | Raw LangGraph state dump for a thread |
| GET | `/portfolio/{portfolio_id}/sessions` | RAG sessions linked to a portfolio |

### `/quant`

| Method | Path | Purpose |
|---|---|---|
| POST | `/quant/query` | Route a query to the stock-analysis supervisor |
| GET | `/quant/health` | TCP connectivity check for 3 of the 4 MCP servers (Options Intelligence :8568 is not checked here) |
| GET | `/quant/capabilities` | Static capability listing |
| GET | `/quant/sessions/{id}` | Raw LangGraph state dump |
| GET | `/quant/portfolio/{id}/sessions` | Portfolio info stub (dedicated quant-session query not yet implemented) |

### `/edgar`

| Method | Path | Purpose |
|---|---|---|
| POST | `/edgar/ingest` | Fetch 10-K/10-Q/8-K filings for a ticker directly from SEC EDGAR, render + ingest |

### `/form4`

| Method | Path | Purpose |
|---|---|---|
| POST | `/form4/ingest` | Single-ticker Form 4 ingestion |
| POST | `/form4/ingest/batch` | Multi-ticker (sequential, one failure doesn't stop the rest) |

### `/chats`

Session CRUD, message history, LLM summaries (single + consolidated multi-session), export (JSON/TXT), stats — ~20 endpoints spanning both RAG and Quant agent types. Key ones: `GET/POST /chats/session/{id}/summary`, `POST /chats/sessions/consolidated-summary`, `GET /chats/user/{user_id}/sessions`, `GET /chats/session/{id}/export`, `DELETE /chats/session/{id}`.

### `/reports`

**Draft clipboard**: `POST/GET /reports/draft/items`, `PUT/DELETE /reports/draft/items/{id}`, `POST /reports/draft/items/reorder`, `DELETE /reports/draft/items/user/{user_id}`.

**Report CRUD + publishing + search**: `POST /reports/from-draft/{user_id}` (assembles clipboard into a report), `POST /reports`, `GET /reports/repository/stats` (Fund Manager dashboard), `GET /reports/user/{user_id}`, `GET /reports` (published, filterable), `GET /reports/search` (Postgres full-text via `tsvector`/`ts_rank`), `GET/PUT/DELETE /reports/{id}`, `POST /reports/{id}/publish`/`unpublish`, `GET /reports/{id}/export/pdf`.

### `/integrations`

`POST /integrations/` (create), `GET /integrations/{id}` / `/user/{user_id}` (list, credentials masked), `PUT`/`DELETE /integrations/{id}`, `POST /integrations/{id}/disconnect`, `POST /integrations/{id}/test` (live connection test), `POST /integrations/browse` (list remote files, optionally with available tickers), `POST /integrations/import` (download + ingest PDFs).

---

## 10. Services layer

| Service | File | Purpose |
|---|---|---|
| `PortfolioService` | `app/services/portfolio.py` | CRUD over `Portfolio`/`Session`. Tickers normalized to lowercase on write. |
| `ChatService` | `app/services/chat.py` | CRUD + analytics over chat models. **OpenAI-backed** (`ChatOpenAI`) for single-session and cross-session (`ConsolidatedSummary`, auto-detects dominant session type by majority vote) LLM summaries. |
| `report.py` (module functions) | `app/services/report.py` | Draft-clipboard CRUD + Analyst Report CRUD/search/publish. All rich text passed through `html_sanitize.sanitize_report_html` (nh3 allowlist + inline-style property filter) before persistence. `content_markdown` is *derived* from `content_html`, not authoritative. |
| `IntegrationService` | `app/services/integration.py` | CRUD over `Integration`. `mask_credentials()` redacts any key containing `client_secret`/`password`/`secret_key`/`access_token`/`refresh_token` before returning to the API layer. |
| `FileImportService` | `app/services/file_import.py` | Downloads + ingests files from a connector. Bounded concurrency (`asyncio.Semaphore(3)` — explicitly sized to overlap I/O without hammering OpenAI embedding rate limits, not "as parallel as possible"). |
| `stock_agent` module | `app/services/stock_agent.py` | Lifecycle management for the quant supervisor — see [§6](#6-quant-subsystem). |
| `VectorDBManager` | `app/services/vectordb_manager.py` | Per-ticker Qdrant instance cache (`get_instance(ticker)`). **Several methods are now no-op/legacy stubs** (`initialize_for_portfolio`, `cleanup_portfolio`, `get_for_session`, `create_temporary`) — see [§13](#13-known-limitations-and-architectural-notes). |
| `pdf_render.py` | `app/services/pdf_render.py` | Renders sanitized report HTML to PDF via `fpdf2`, walking the DOM manually (chosen over fpdf2's built-in `write_html()` because that ignores inline color/background outside heading tags and crashes on TipTap-style pixel-width image attributes). |

---

## 11. Integration connectors

**File:** `app/services/connectors/base.py` + one file per vendor

`BaseConnector.get_connector(vendor, credentials, url)` factory dispatches to a concrete class. Every connector implements `test_connection()`, `list_files()`, `download_file()` (returns a local temp path).

| Vendor | Class | Auth mechanism |
|---|---|---|
| SharePoint Online | `SharePointConnector` | Microsoft Graph API, OAuth2 client-credentials |
| Google Drive | `GoogleDriveConnector` | Service account JSON (preferred) or OAuth2 refresh token; handles My Drive, Shared Drives, shortcuts; exports Workspace files to PDF/XLSX |
| OneDrive | `OneDriveConnector` | Microsoft Graph API (same pattern as SharePoint) |
| Confluence | `ConfluenceConnector` | Basic auth (email + API token) against Atlassian Cloud REST |
| Azure Blob Storage | `AzureBlobConnector` | `azure.storage.blob.BlobServiceClient` |
| AWS S3 | `AWSS3Connector` | `boto3` |
| SFTP | `SFTPConnector` | `paramiko.Transport` |

All SDK clients are constructed lazily on first use, and each connector raises a clear error if its optional SDK package isn't installed.

---

## 12. Deployment

### Docker Compose (`docker-compose.yml`)

- `postgres` — `postgres:16-alpine`, host port **5433** → container 5432 (avoids clashing with a local Postgres install), healthcheck via `pg_isready`.
- `qdrant` — `qdrant/qdrant:latest`, ports 6333 (REST)/6334 (gRPC).
- `api` — built from the local `Dockerfile`, `depends_on` postgres (`service_healthy`) and qdrant; exposes 8000 (main API) + 8565-8568 (MCP servers); mounts `./data` → `/app/data`.

### Dockerfile

Base `python:3.11-slim`. System deps: `gcc`/`libpq-dev` (Postgres build), `libjpeg-dev`/`libpng-dev`/`zlib1g-dev` (Pillow/kaleido), `chromium`+`chromium-driver` (headless chart rendering + EDGAR PDF rendering), `tesseract-ocr` (image OCR). Creates `/app/data/macro` pre-emptively so the app starts cleanly before an EFS mount overlays it at runtime (implies AWS ECS/EFS as the target deployment).

### Entrypoint (`docker-entrypoint.sh`)

1. Starts all 4 MCP servers in the background (each in its own subdirectory).
2. `sleep 3` for MCP warm-up.
3. `alembic upgrade head`.
4. `exec uvicorn app.main:app --host 0.0.0.0 --port 8000` (replaces the shell process).

`set -e` — any failing step aborts the script.

---

## 13. Known limitations and architectural notes

A prior pass through this backend surfaced 7 architectural gaps; all 7 have since been fixed (below), verified against a live running server, not just compiled. Two smaller items remain open (still worth knowing about) and are listed at the end.

### Fixed

1. **Auth enforcement** — was opt-in per route (only `/auth/me`, `/auth/logout`, `PUT /auth/me` required a token; every other endpoint accepted `user_id` as an unverified client-supplied string). Now every route across all 8 routers requires a valid JWT and verifies the client-supplied `user_id`/fetched resource's owner matches the token — see [§2](#2-backend-foundations) for the exact mechanism. Verified live: unauthenticated → 401, mismatched `user_id` → 403, another user's resource by ID → 404 (not leaked as 403), the actual owner → 200.
2. **Checkpointer fragmentation** — RAG and Quant used two independent SQLite files despite the rest of the stack being Postgres, and `initialize_stock_agents()`'s `checkpointer` param was explicitly documented as ignored. Now both share one `AsyncPostgresSaver` on the same Postgres database. Fixing this surfaced a real follow-on risk — a shared checkpointer partitions only by `thread_id`, so if a RAG and a Quant session ever used the same underlying ID (the session model allows this), their state could collide. Fixed by prefixing every checkpoint `thread_id` (`rag:`/`quant:`) at the point of use — verified directly that identical underlying IDs stay fully isolated across the prefix boundary.
3. **`VectorDBManager` stub/comment mismatch** — `initialize_for_portfolio()`, `cleanup_portfolio()`, `get_for_session()`, `create_temporary()` are legacy no-op shims (retrieval is fully lazy per-ticker via `get_instance(ticker)`), but `app/api/portfolios.py`'s comments described them as doing real work ("CRITICAL: Initialize Vector DB ONCE..."). Docstrings now say what these methods actually do (nothing), and `portfolios.py` no longer calls the dead ones or claims they matter. The one genuinely-live piece of state (`register_session`'s in-memory `thread_id → portfolio_id` map) is untouched and still refreshed correctly on ticker changes.
4. **Unbounded disk growth from debug dumps** — `POST /ask`, `POST /compare`, `POST /quant/query` wrote full JSON response payloads to `output/json/...` on every single call, unconditionally. Now gated behind `SAVE_DEBUG_RESPONSES` (env var, default `false`) — off by default in every environment, opt-in when you actually want the dumps for debugging.
5. **`_is_direct_vectordb_mode()` bypass risk** — comparison/segment/geographic queries skipped grading and web-search fallback entirely, on a "10-K is authoritative" assumption that doesn't hold once 10-Q/8-K data (thinner per-document) flows through the same paths. Now the bypass only applies when `filing_types` is empty or contains only `"10-K"` (byte-for-byte unchanged behavior there); any query resolving to 10-Q/8-K (alone or combined with 10-K) falls through to the normal grade → web-fallback safety net instead. Verified with 9 direct test cases covering both branches.
6. **`quant/stock_agent/api_server.py` port collision** — its startup banner claimed port 8567 but actually bound 8568, colliding with the Options Intelligence MCP server if both ran at once. This file is a standalone/alternate entry point (not the one wired into `app/main.py`), now moved to port 8569 with the banner corrected to match.
7. **Bare `print()` instead of structured logging** — was pervasive in `portfolios.py`, `integrations.py`, `rag.py`, `quant.py`, and all 5 file-storage connectors. All converted to the `logging` module (matching the style already used in `main.py`/`edgar.py`/`form4.py`), with level chosen by message content (error/warning/info).
8. **`is_comparison_mode` checkpointer leak** — `/compare` set `is_comparison_mode`/`comparison_company1-3`/`year_start`/`year_end` on the graph state, and the Postgres checkpointer persists state per `thread_id` across turns by merging inputs rather than replacing state. `/ask` never reset these fields, so a thread that was ever used for `/compare` would silently route every later `/ask` question through the annual-only comparison templates. Fixed by having `/ask` explicitly reset all of these every call. Reproduced and verified live against the actual Postgres-backed checkpointer (not just read from code) before and after the fix.
9. **Company-name alias gaps** — `TICKER_TO_COMPANY` had a duplicate `'googl'` dict key, silently dropping the `'alphabet'` alias (Python keeps only the last literal key); `detect_tickers_in_query()` also only checked a ticker's single canonical name, so even after the alias was restored, "Alphabet ..." sub-queries fell back to querying every company in the request instead of just Google. Fixed via `get_company_aliases()` (§ Fiscal calendar utilities above) used by both `get_ticker()`'s reverse mapping and `detect_tickers_in_query()`.
10. **`generate_comparison_chart` `datetime.now()` crash** — a local `import datetime` inside the function shadowed the module-level `from datetime import datetime`, so `datetime.now()` (written expecting the class-level import) resolved against the module instead and raised `AttributeError: module 'datetime' has no attribute 'now'` whenever chart generation fell back to it (i.e. neither `year_start` nor `year_end` was set on state). Fixed by removing the redundant local import.

### Still open

11. **Env vars used in code but missing from `.env.example`**: `FRED_API_KEY` (macro ingestion raises without it), `CLOUDINARY_CLOUD_NAME`/`CLOUDINARY_API_KEY`/`CLOUDINARY_API_SECRET` (chart upload features degrade gracefully without them), `JWT_SECRET_KEY` (if unset, `app/auth/jwt.py` refuses to start when `APP_ENV=production`; in dev it falls back to a *random secret generated fresh every process start* — safe against forgery, but means every restart invalidates all existing tokens, which reads as "sessions expire instantly" under `uvicorn --reload`. Set it explicitly in `.env` for any environment where restarts shouldn't log everyone out), `SEC_USER_AGENT` (defaults to a placeholder contact string).
12. **Semantic cache (`rag/graph/semantic_cache.py`) is not wired into any live code path** — correctness-fixed (filter-aware cache key) in case it's adopted later, but currently dead code.

### Still true, by design (not gaps)

- **Bounded concurrency appears in exactly one place** in the services layer: `FileImportService.import_files` (`asyncio.Semaphore(3)`). The ingestion pipeline itself (embeddings, image captioning, EDGAR filing fetches) uses bounded concurrency extensively, but the services/API layer otherwise doesn't need it since most work is single-document per request.
