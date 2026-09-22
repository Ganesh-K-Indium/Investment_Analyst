# Investment Analyst

An AI-native investment research platform combining **document-grounded RAG over SEC filings** (10-K/10-Q/8-K), a **multi-agent quant system** for live market/technical/options analysis, an **insider-trading (Form 4) advisory pipeline**, **macroeconomic data (FRED + BLS)**, **external document/file integrations** (S3, SharePoint, Google Drive, Azure Blob, OneDrive, SFTP, Confluence), a **Redis/Arq background job system** for long-running analysis runs, and full **portfolio, chat-history, and analyst-report** management — all behind one FastAPI backend.

For the full architectural deep-dive (every subsystem, every file, known gaps), see **[docs/TECHNICAL_GUIDE.md](docs/TECHNICAL_GUIDE.md)**. See **[BACKGROUND_ANALYSIS_GUIDE.md](BACKGROUND_ANALYSIS_GUIDE.md)** for the interactive-vs-batch job queue specifically. This README is the quick-start entry point.

---

## What it does

- **RAG over SEC filings** — Ask questions across a portfolio's 10-K/10-Q/8-K filings, with filing-type-aware retrieval (a "latest quarter" question routes to 10-Qs, not 10-Ks), fiscal-calendar-aware period resolution (Apple's fiscal Q1 ≠ a calendar-year filer's Q1), hybrid dense+BM25 Qdrant search, and cross-period anti-hallucination guardrails when a comparison spans mismatched fiscal calendars or filing types.
- **SEC EDGAR ingestion** — Pull any ticker's filings directly from EDGAR, render to PDF, and ingest — either programmatically or via an interactive CLI (`scripts/ingest_ticker.py`) that lists available filings by fiscal year/quarter and lets you pick exactly which ones to bring in.
- **ALPHA framework** — A 5-dimension equity research report per ticker (Alignment/insider activity, Liquidity, Performance, Horizon, Action) combining vector search, web research, and Form 4 data.
- **Scenario framework (Bull/Bear/Base)** — A separate graph branch layered on the same RAG pipeline that generates bull-case/bear-case/base-case scenario analysis per query.
- **Macro framework** — FRED- and BLS-sourced GDP/CPI/PCE/PPI/Fed Funds/yield-curve/employment series, auto-refreshed daily, exposed both as RAG tool context and as a dedicated macro-analysis graph branch (detect → analyze → calculate → format).
- **Quant multi-agent system** — A LangGraph supervisor routes to specialized sub-agents (ticker lookup, stock fundamentals, technical analysis, research/ratings, options intelligence, and — when a Postgres integration is connected — a Postgres query agent), each backed by its own MCP server.
- **Options intelligence** — Deterministic (non-LLM) options-chain analytics: put/call ratio, support/resistance from strike concentration, IV skew, unusual-volume detection, "smart money" long-dated positioning.
- **Insider trading advisory** — SEC Form 4 filings ingested and scored (role-weighted, dollar-weighted buy/sell signal) into a natural-language recommendation.
- **External integrations** — Connect and import files from S3, SharePoint, Google Drive, Azure Blob, OneDrive, SFTP, and Confluence, plus query a connected Postgres database directly from the quant agent.
- **Background analysis jobs** — Long-running RAG/ALPHA/quant analysis runs execute on Redis/Arq-backed job queues (separate "interactive" and "batch" workers) instead of blocking an HTTP request.
- **Portfolios, chat history, analyst reports** — Portfolio-scoped conversations, per-session and cross-session AI summaries, a server-side report-drafting clipboard, publishing to a searchable Fund-Manager repository, and PDF export.

---

## Architecture at a glance

```
FastAPI app (app/main.py)
├── Auth (JWT, per-route — most endpoints require it; login/signup/refresh
│         and a handful of health/capabilities routes are intentionally public)
├── Async SQLAlchemy + Postgres (all routes; Alembic-managed schema)
│
├── RAG subsystem (rag/, ingestion/)
│   ├── LangGraph graph (rag/graph/builder.py): START routes through
│   │   detect_alpha → detect_scenario → detect_macro, then branches into
│   │   one of: ALPHA (alpha_retrieve → alpha_generate), Scenario
│   │   (Bull/Bear/Base), Macro (macro_analyze → macro_calculate →
│   │   macro_format), or the standard path — retrieve → grade_documents →
│   │   web_search (fallback) → generate → verify_grounding → decide_chart
│   │   → (generate_chart) → show_result
│   ├── Qdrant: one hybrid collection per ticker (dense + BM25 + RRF,
│   │   `ticker_{ticker}`), filtered further by filing-type/period
│   │   metadata; falls back to a shared `unified_rag_db_hybrid`
│   │   collection when no ticker can be resolved
│   ├── Filing-type + fiscal-quarter aware retrieval (company_mapping.py fiscal calendars)
│   └── Ingestion: PDF text + table extraction + OCR/GPT-4o vision for
│       images, EDGAR fetcher (cover-page detection, period_end_date
│       ground truth) — note: this GPT-4o vision path is separate from the
│       Gemini vision used for quant chart-summary generation
│
├── Quant subsystem (quant/)
│   ├── LangGraph supervisor (app/services/stock_agent.py) + sub-agents
│   └── MCP servers: Stock Info :8565, Technical :8566, Research :8567,
│       Options :8568, and Postgres :8570 (only active when a Postgres
│       integration is connected)
│
├── Form4 insider-trading pipeline (ingestion/Form4_Ingestion/, rag/utils/Insights_Form4/)
├── Macro data pipeline (ingestion/ingest_macro_data.py — FRED + BLS)
├── Integrations (app/api/integrations.py, app/services/connectors/) —
│   S3, Azure Blob, Google Drive, OneDrive, SharePoint, SFTP, Confluence
├── Background jobs (app/worker.py, app/jobs/) — Redis/Arq queues backing
│   long-running RAG/ALPHA/quant runs; interactive (short) vs batch (long)
│   worker pools — see BACKGROUND_ANALYSIS_GUIDE.md
└── Portfolio / Chat / Report / Integration services (app/services/)
```

See [docs/TECHNICAL_GUIDE.md](docs/TECHNICAL_GUIDE.md) for the full breakdown of every node, service, and endpoint.

---

## Quick start

### Prerequisites
- Python 3.11+
- Docker (for Postgres + Qdrant + Redis via `docker-compose`) — or your own instances
- API keys: OpenAI (required), Google (required — Gemini chart-summary vision), Tavily (optional — web search), Groq (optional — alt LLM), FRED (required for macro data), BLS (optional — additional macro series)

### 1. Clone and set up the environment

```bash
git clone <repo-url>
cd Investment_Analyst

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium     # needed for EDGAR filing → PDF rendering

cp .env.example .env
# Edit .env — see Environment Variables below for the full set
```

### 2. Start Postgres + Qdrant + Redis

```bash
docker compose up -d postgres qdrant redis
```

This publishes Postgres on host port **5433** (not 5432 — avoids clashing with a locally-installed Postgres), Qdrant on **6333**, and Redis on **6379**. If you're running the app itself outside Docker, point `DATABASE_URL`/`REDIS_URL` at `localhost`; if you're running the whole stack via `docker compose up`, the `api`/worker containers talk to `postgres`/`redis`/`qdrant` by service name internally instead.

### 3. Run migrations

```bash
alembic upgrade head
```

### 4. Start the API

```bash
python -m uvicorn app.main:app --reload --port 8000
```

On startup, the app also creates the LangGraph RAG agent, initializes the quant supervisor (waiting briefly for MCP servers — non-fatal if some aren't up yet), connects to the Redis/Arq job queue, checks/kicks off macro data ingestion if stale, and starts a 24h macro re-sync loop.

### 5. Start the background job workers

Required for background/batch analysis runs (ALPHA portfolio runs, long RAG queries) to actually process — without them, jobs queue in Redis but never execute:

```bash
arq app.worker.InteractiveWorkerSettings &   # short-lived / interactive queue
arq app.worker.BatchWorkerSettings &          # long-running / batch queue
```

### 6. (Optional) Start the quant MCP servers

```bash
cd quant/yahoo-finance-mcp && python server.py &      # Stock Information — :8565
cd quant/Stock_Analysis && python server_mcp.py &     # Technical Analysis — :8566
cd quant/research_mcp && python server_mcp.py &        # Research/Ratings — :8567
cd quant/options_mcp && python server_mcp.py &         # Options Intelligence — :8568
cd quant/postgres_mcp && python server_mcp.py &        # Postgres query agent — :8570 (only needed if using a Postgres integration)
```

Or run everything (Postgres + Qdrant + Redis + API + both workers + all MCP servers) via Docker:

```bash
docker compose up --build
```

### 7. Ingest some filings to test with

```bash
python scripts/ingest_ticker.py AAPL
# Lists available 10-K/10-Q/8-K filings with fiscal year/quarter labels
# (e.g. "FY2024", "2025Q3"), lets you pick which to pull in and ingest.
```

### 8. Access

| URL | Purpose |
|---|---|
| http://localhost:8000 | API root / service directory |
| http://localhost:8000/docs | Interactive Swagger UI |
| http://localhost:8000/health | Aggregated health check |
| `static/index.html` | Minimal web UI |

---

## Environment variables

| Variable | Required? | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | Required | Primary LLM (RAG generation, ingestion GPT-4o vision, quant supervisor, chat summaries) |
| `QDRANT_URL`, `QDRANT_API_KEY` | Required | Vector database (Qdrant Cloud or self-hosted) |
| `GOOGLE_API_KEY` | Required | Gemini vision, used for quant chart-summary generation (distinct from the GPT-4o vision used in filing ingestion) |
| `DATABASE_URL` | Required | Postgres connection string (one URL, translated internally to asyncpg at runtime / psycopg2 for Alembic) |
| `REDIS_URL` | Required | Redis/Arq background job queue — backs interactive + batch analysis workers |
| `FRED_API_KEY` | Required for macro data | Federal Reserve Economic Data API |
| `BLS_API_KEY` | Optional | Bureau of Labor Statistics API — supplements FRED for macro series |
| `GROQ_API_KEY` | Optional | Alternative/faster LLM provider |
| `TAVILY_API_KEY` | Optional | Web-search augmentation (research agent, RAG web fallback, ALPHA horizon/action dimensions) |
| `POSTGRES_USER`/`PASSWORD`/`DB` | Required (docker-compose) | Initializes the `postgres` container's default role/db |
| `SEC_USER_AGENT` | Optional | Sent on every SEC EDGAR request per SEC's fair-use policy; defaults to a placeholder if unset — **set this to your own contact info** |
| `JWT_SECRET_KEY` | Required in production | Signs auth tokens. In dev, if unset, a random secret is generated per process start (warning logged, tokens invalidate on restart). In production (`APP_ENV=production`), the app **refuses to start** if unset. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_DAYS` | Optional | JWT lifetimes (default 30 min / 7 days) |
| `INTEGRATION_SECRET_KEY` | Required for Integrations | Encrypts stored connector credentials (S3/SharePoint/Google Drive/etc.) — the app raises an error the first time an integration endpoint touches credentials if unset |
| `CLOUDINARY_CLOUD_NAME`/`API_KEY`/`API_SECRET` | Optional | Chart image hosting (technical analysis, options charts) — features degrade gracefully if unset |
| `CORS_ALLOWED_ORIGINS` | Optional | Comma-separated list of allowed frontend origins (must exactly match the deployed frontend for credentialed requests) |
| `TESSERACT_CMD` | Optional | Explicit path to the Tesseract OCR binary (needed in Docker/ECS environments); falls back to vision-only extraction if unset/missing |
| `USE_HYBRID_SEARCH` | Optional | Toggles dense+BM25 hybrid vs. plain dense search at ingestion time (default: on) |
| `SAVE_DEBUG_RESPONSES` | Optional | Dumps full RAG/quant response payloads to `output/json/` for debugging (default: off) |
| `APP_ENV`, `LOG_LEVEL` | Optional | General app config |

Generate a JWT secret: `python -c "import secrets; print(secrets.token_hex(32))"`

Generate an integration secret: `python -c "import secrets; print(secrets.token_hex(32))"`

---

## Repository layout

```
app/                   FastAPI app: routers (api/), services (services/),
                        auth (auth/), DB models + connection (database/),
                        background job workers (worker.py, jobs/),
                        external connectors (services/connectors/)
rag/                    LangGraph RAG pipeline, Qdrant client, prompts, semantic cache
ingestion/              PDF/table/image processing, EDGAR fetcher, Form4 pipeline,
                        macro data ingestion, table_extractor.py
quant/                  Stock-agent supervisor + sub-agents, 5 MCP servers
                        (yahoo-finance-mcp, Stock_Analysis, research_mcp,
                        options_mcp, postgres_mcp)
schemas/                Pydantic models (LLM structured outputs, request/response shapes)
alembic/versions/       Database migrations (Postgres-target)
scripts/                ingest_ticker.py (interactive EDGAR ingestion CLI), utilities
static/                 Minimal web UI (index.html, integrations.html, test.html)
tests/                  API tests
docs/                   TECHNICAL_GUIDE.md — the full architectural reference
```

---

## Known limitations (see the technical guide for detail)

- Native-PDF tables now extract with real column structure (`ingestion/table_extractor.py`); image-embedded charts/tables go through OCR + GPT-4o vision. Flattened prose extraction is still the fallback when neither applies.
- The semantic cache (`rag/graph/semantic_cache.py`) exists and is correctness-fixed but isn't wired into any live code path yet.
- Auth is enforced **per-route** (`Depends(get_current_user)` on individual endpoints), not via a global middleware — by design, since login/signup/token-refresh and a handful of health/capabilities endpoints must stay public. Nearly every other endpoint requires a valid JWT; see `docs/TECHNICAL_GUIDE.md` for the exact per-router breakdown.
- `alembic/versions/` contains a few differently-named migrations that appear to add the same "chat summary fields" — worth a history sanity check before assuming a clean linear chain.

See [docs/TECHNICAL_GUIDE.md §13](docs/TECHNICAL_GUIDE.md#13-known-limitations-and-architectural-notes) for the full list of what was audited and fixed.

---

**Built with:** FastAPI · LangGraph · LangChain · Qdrant · Postgres (async SQLAlchemy + Alembic) · Redis + Arq (background jobs) · SEC EDGAR · Playwright (filing → PDF rendering) · Tesseract OCR + GPT-4o vision (ingestion) · Google Gemini vision (chart summaries) · FastMCP · Cloudinary (chart hosting)
