# Investment Analyst

An AI-native investment research platform combining **document-grounded RAG over SEC filings** (10-K/10-Q/8-K, plus 20-F/6-K for foreign private issuers), a **multi-agent quant system** for live market/technical/options analysis, an **insider-trading (Form 4) advisory pipeline**, **macroeconomic data (FRED + BLS)**, **external document/file integrations** (S3, SharePoint, Google Drive, Azure Blob, OneDrive, SFTP, Confluence), a **Redis/Arq background job system** for long-running analysis runs, and full **portfolio, chat-history, and analyst-report** management — all behind one FastAPI backend.

For the full architectural deep-dive (every subsystem, every file, known gaps), see **[docs/TECHNICAL_GUIDE.md](docs/TECHNICAL_GUIDE.md)**. See **[BACKGROUND_ANALYSIS_GUIDE.md](BACKGROUND_ANALYSIS_GUIDE.md)** for the interactive-vs-batch job queue specifically. This README is the quick-start entry point.

---

## What it does

- **RAG over SEC filings** — Ask questions across a portfolio's 10-K/10-Q/8-K filings (and 20-F/6-K for foreign private issuers), with filing-type-aware retrieval (a "latest quarter" question routes to 10-Qs, not 10-Ks; a generic "annual report" question searches 10-K and 20-F together), fiscal-calendar-aware period resolution (Apple's fiscal Q1 ≠ a calendar-year filer's Q1), hybrid dense+BM25 Qdrant search, and cross-period anti-hallucination guardrails when a comparison spans mismatched fiscal calendars or filing types.
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
│   ├── LangGraph graph (rag/graph/builder.py): START → route_query (one
│   │   node: ALPHA | Scenario | Macro | one of the user's own frameworks |
│   │   standard), then standard = clarify (optional pause) → preprocess →
│   │   retrieve (hybrid search → cross-encoder re-rank, plus the user's
│   │   notes and uploaded documents) → grade_documents → rewrite_query
│   │   (corrective retry) or web_search (fallback) → generate →
│   │   verify_answer (figures vs evidence) → (generate_chart) → show_result
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
├── Settings + model registry (app/services/app_settings/, app/api/settings.py) — every
│   option lives in one schema (defaults → admin → user → portfolio → per-request);
│   every model call goes through get_llm(role)
├── Bring-your-own LLM providers (app/services/llm_providers/, app/api/providers.py) —
│   OpenAI, Anthropic, Gemini, Ollama, any OpenAI-compatible server; per-user
│   encrypted keys; UI: Settings → AI providers
├── User documents (app/services/user_docs/, app/docproc/, app/api/user_documents.py) —
│   PDF/scans/images, Excel/CSV, Word, PowerPoint, HTML, text → read, checked (finance
│   arithmetic), reviewed by a person where doubtful, indexed in the `user_documents`
│   Qdrant collection. app/docproc/ is the folded-in extraction engine
│   (local OCR first, vision model only where doubtful)
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
- **Tesseract OCR** (the default local OCR engine that reads scanned documents): `brew install tesseract` (macOS) · `sudo apt install tesseract-ocr` (Debian/Ubuntu) · Windows: the UB-Mannheim installer, then set `TESSERACT_CMD`. Check with `tesseract --version`. Without it, scans cannot be read locally (the Docker image already includes it).
- Docker (for Postgres + Qdrant + Redis via `docker-compose`) — or your own instances
- Node 18+ for the web UI (a separate repository, `investment-analyst-ui`)
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

`requirements.txt` covers everything the app needs, including PyMuPDF **2.x** (`pymupdf>=2.1`, with `pymupdf-layout`; PyMuPDF 1.x finds tables differently and the extraction tests will fail) and the LLM provider libraries (`langchain-openai`, `langchain-anthropic`, `langchain-google-genai`, `langchain-ollama`). If you install by hand instead:

```bash
pip install "pymupdf>=2.1" "pymupdf-layout>=2.1" openpyxl python-docx python-pptx beautifulsoup4 lxml \
            langchain-anthropic langchain-google-genai langchain-ollama arq fastembed
```

**Optional OCR engines** (Tesseract is enough to start; add any of these for better results on difficult scans, then restart the API):

| Engine | Install | Size |
|---|---|---|
| RapidOCR | `pip install rapidocr-onnxruntime` | ~100 MB, models included |
| PaddleOCR | `pip install paddlepaddle paddleocr` | ~430 MB + ~130 MB models |
| docTR | `pip install "python-doctr[torch]"` | ~630 MB + ~125 MB models |

Once installed, an administrator opens **Settings → Your documents → Reading engines** to download models, test an engine on a sample page, and choose it. From a terminal: `python -m app.docproc.engines.warmup --status` (what is on disk) and `python -m app.docproc.engines.warmup` (download models now). Installing the Python packages is deliberately not offered from the web UI: a web request should not change the server's own software.

### 2. Start Postgres + Qdrant + Redis

```bash
docker compose up -d postgres qdrant redis
```

This publishes Postgres on host port **5433** (not 5432 — avoids clashing with a locally-installed Postgres), Qdrant on **6333**, and Redis on **6379**. If you're running the app itself outside Docker, point `DATABASE_URL`/`REDIS_URL` at `localhost`; if you're running the whole stack via `docker compose up`, the `api`/worker containers talk to `postgres`/`redis`/`qdrant` by service name internally instead.

### 3. Run migrations

```bash
alembic upgrade head
```

(The API also runs `alembic upgrade head` on startup.) The schema is at revision `023_provider_credentials`. **Switching to an older branch?** An older branch cannot start on a database already migrated past its own newest revision. Keep a separate database per branch, or `alembic downgrade <that branch's revision>` first. This branch's `docker-compose.yml` uses its own Compose project name (`investment_analyst_new`, override with `COMPOSE_PROJECT_NAME`), so its Postgres/Redis volumes are separate from the older branch's.

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

Easiest: `./scripts/start_mcp_servers.sh` (start | stop | status; it sets `PYTHONPATH` so the servers can import the app). By hand:

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

**Market-data providers.** Data Sources > Add data source connects Alpha Vantage, Polygon.io, Finnhub, Twelve Data, Financial Modeling Prep or a custom REST API (for example your own gateway in front of Bloomberg or Refinitiv; they have no open internet API). The key is tested when you connect it, stored encrypted, and used server-side by the quant agent's "Data Sources" sub-agent, which can fetch quotes, daily prices, profiles, statements, news and symbol search where the provider offers them. PostgreSQL databases are connected the same way as before. Run `alembic upgrade head` to create the table. Adding another provider is a short entry in `app/services/data_sources.py`.

**Your own MCP servers.** Under Settings > Quant agent > Your tools a user can connect remote MCP servers (an `https://…/mcp` address, optional sign-in header). The quant agent then has a "Your Tools" sub-agent that can list and call their tools. Only remote servers are accepted (nothing is started on this machine), tools that are not read-only stay blocked until the user allows actions for that server, and servers on this machine or a private network are refused unless `MCP_ALLOW_PRIVATE_URLS=1` (turn it on for a single-user install). Run `alembic upgrade head` once to create the table.

### 7. Start the web UI

```bash
git clone <ui-repo-url> investment-analyst-ui && cd investment-analyst-ui
npm install
echo "VITE_API_BASE_URL=http://localhost:8000" > .env      # where the API is
npm run dev                                                # http://localhost:5173
```

The API allows `localhost`/`127.0.0.1` on any port by default (Vite moves to 5174 if 5173 is busy); in production set `CORS_ALLOWED_ORIGIN_REGEX=` (empty) and list origins in `CORS_ALLOWED_ORIGINS`.

### 8. Ingest some filings to test with

```bash
python scripts/ingest_ticker.py AAPL
# Lists available 10-K/10-Q/8-K/20-F/6-K filings with fiscal year/quarter labels
# (e.g. "FY2024", "2025Q3"), lets you pick which to pull in and ingest.
```

### 9. Access

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
| `OPENAI_API_KEY` | Required for search | **Embeddings** (`text-embedding-3-large`, shared by every user's search), SEC ingestion captions and handwritten notes, and the default chat models. Users can bring other providers for chat/agents (below), but search still needs this key. |
| `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OLLAMA_BASE_URL`, `LLM_CUSTOM_BASE_URL` / `LLM_CUSTOM_API_KEY` / `OPENAI_BASE_URL` | Optional | Server-wide fallback for providers (used by anyone who saves no key of their own). Users add their own under **Settings → AI providers**. |
| `APP_SECRET_KEY` | Recommended (required for shared deployments) | Encrypts the provider keys users save. Without it a random key is created once in `data/docproc/secret.key`. Changing it makes saved keys unreadable (users re-enter them). |
| `MCP_SHARED_SECRET` | Required in production | Signs the per-request token the Postgres MCP tool verifies (so a model cannot query another user's database). Falls back to `JWT_SECRET_KEY`; in development a random one is kept in `data/.mcp_secret`. |
| `LOCAL_ONLY` | Optional | `1` = nothing leaves the machine: cloud models and model downloads are switched off; local OCR and Ollama/self-hosted still work. |
| `LOCK_SETTINGS` | Optional | `1` = the provider screen is read-only (configuration managed through the environment). |
| `LLM_ALLOW_PRIVATE_URLS` | Optional | `0` = refuse localhost / private-network model addresses (set on a shared server; cloud-metadata addresses are always refused). |
| `COHERE_API_KEY`, `RERANK_LOCAL_MODEL` | Optional | Re-ranker: local cross-encoder by default (model downloads on first use, ~90 MB); Cohere only if a user chooses it. |
| `CONTEXTUAL_PREFIX` | Optional | `0` turns off the company/filing/period/section header added to each chunk's embedding text at ingestion (default on). |
| `DOCPROC_DATA_DIR` | Optional | Where the document-reading engine keeps OCR models and logs (default `data/docproc`). |
| `MCP_<STOCK_INFO\|TECHNICAL\|RESEARCH\|OPTIONS\|POSTGRES>_URL` / `_ENABLED` / `_BIND`, `MCP_HOST` | Optional | Where each quant MCP server lives; defaults to `127.0.0.1` and the usual ports. |
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

## Testing

```bash
pytest tests --ignore=tests/test_api_v2.py        # ~2.5 min (the extraction engine's tests run OCR); tests/docproc can be skipped for a quick run
python scripts/extraction_eval.py                 # finance extraction accuracy (local OCR, no LLM calls)
EVAL_TOKEN=… EVAL_THREAD_ID=… python scripts/rag_eval.py   # question set against a running API (uses your LLM provider)
```

`tests/test_api_v2.py` needs a running Postgres. Fill in `expected` answers in `tests/rag_eval/golden.jsonl` for the RAG evaluation to measure accuracy.

---

## Repository layout

```
app/                   FastAPI app: routers (api/), services (services/ — incl. app_settings/,
                        llm_providers/, user_docs/), docproc/ (document-reading engine: OCR,
                        layout, tables, finance checks), mcp registry/auth,
                        auth (auth/), DB models + connection (database/),
                        background job workers (worker.py, jobs/),
                        external connectors (services/connectors/)
rag/                    LangGraph RAG pipeline (graph/), Qdrant client, prompts (+ editable prompt
                        registry), retrieval/rerank.py, verify/numbers.py, eval/metrics.py
ingestion/              PDF/table/image processing, EDGAR fetcher, Form4 pipeline,
                        macro data ingestion, table_extractor.py
quant/                  Stock-agent supervisor + sub-agents, 5 MCP servers
                        (yahoo-finance-mcp, Stock_Analysis, research_mcp,
                        options_mcp, postgres_mcp)
schemas/                Pydantic models (LLM structured outputs, request/response shapes)
alembic/versions/       Database migrations (Postgres-target)
scripts/                ingest_ticker.py (interactive EDGAR ingestion CLI), utilities
static/                 Minimal web UI (index.html, integrations.html, test.html)
tests/                  pytest suite (tests/docproc/ = the extraction engine's tests)
scripts/                (above) + rag_eval.py (question set → accuracy report),
                        extraction_eval.py (finance statements × scan quality, no LLM cost),
                        start_mcp_servers.sh
docs/                   TECHNICAL_GUIDE.md — the full architectural reference
```

---

## Known limitations (see the technical guide for detail)

- Native-PDF tables now extract with real column structure (`ingestion/table_extractor.py`); image-embedded charts/tables go through OCR + GPT-4o vision. Flattened prose extraction is still the fallback when neither applies.
- Document search is built on OpenAI embeddings shared by all users, so those stay on the server's OpenAI key whatever chat provider a user picks; changing the embedding model means re-indexing every collection.
- Vision models can invent plausible text on very poor scans. Doubtful pages are held for review by default (Settings → Your documents → "Index low-confidence text"), and a second, *different* vision model should be used to cross-check; see `scripts/extraction_eval.py` for measured behaviour on financial statements.
- Chat answers are checked against their sources for figures and units only, not for claims or reasoning.
- Auth is enforced **per-route** (`Depends(get_current_user)` on individual endpoints), not via a global middleware — by design, since login/signup/token-refresh and a handful of health/capabilities endpoints must stay public. Nearly every other endpoint requires a valid JWT; see `docs/TECHNICAL_GUIDE.md` for the exact per-router breakdown.
- `alembic/versions/` contains a few differently-named migrations that appear to add the same "chat summary fields" — worth a history sanity check before assuming a clean linear chain.

See [docs/TECHNICAL_GUIDE.md §13](docs/TECHNICAL_GUIDE.md#13-known-limitations-and-architectural-notes) for the full list of what was audited and fixed.

---

**Built with:** FastAPI · LangGraph · LangChain · Qdrant · Postgres (async SQLAlchemy + Alembic) · Redis + Arq (background jobs) · SEC EDGAR · Playwright (filing → PDF rendering) · Tesseract OCR + GPT-4o vision (ingestion) · Google Gemini vision (chart summaries) · FastMCP · Cloudinary (chart hosting)
