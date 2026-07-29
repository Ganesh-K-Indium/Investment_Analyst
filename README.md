# Investment Analyst

An AI-native investment research platform combining **document-grounded RAG over SEC filings** (10-K/10-Q/8-K), a **multi-agent quant system** for live market/technical/options analysis, an **insider-trading (Form 4) advisory pipeline**, **macroeconomic data (FRED)**, and full **portfolio, chat-history, and analyst-report** management — all behind one FastAPI backend.

For the full architectural deep-dive (every subsystem, every file, known gaps), see **[docs/TECHNICAL_GUIDE.md](docs/TECHNICAL_GUIDE.md)**. This README is the quick-start entry point.

---

## What it does

- **RAG over SEC filings** — Ask questions across a portfolio's 10-K/10-Q/8-K filings, with filing-type-aware retrieval (a "latest quarter" question routes to 10-Qs, not 10-Ks), fiscal-calendar-aware period resolution (Apple's fiscal Q1 ≠ a calendar-year filer's Q1), hybrid dense+BM25 Qdrant search, and cross-period anti-hallucination guardrails when a comparison spans mismatched fiscal calendars or filing types.
- **SEC EDGAR ingestion** — Pull any ticker's filings directly from EDGAR, render to PDF, and ingest — either programmatically or via an interactive CLI (`scripts/ingest_ticker.py`) that lists available filings by fiscal year/quarter and lets you pick exactly which ones to bring in.
- **ALPHA framework** — A 5-dimension equity research report per ticker (Alignment/insider activity, Liquidity, Performance, Horizon, Action) combining vector search, web research, and Form 4 data.
- **Quant multi-agent system** — A LangGraph supervisor routes to 5 specialized sub-agents (ticker lookup, stock fundamentals, technical analysis, research/ratings, options intelligence), each backed by its own MCP server.
- **Options intelligence** — Deterministic (non-LLM) options-chain analytics: put/call ratio, support/resistance from strike concentration, IV skew, unusual-volume detection, "smart money" long-dated positioning.
- **Insider trading advisory** — SEC Form 4 filings ingested and scored (role-weighted, dollar-weighted buy/sell signal) into a natural-language recommendation.
- **Macro data** — FRED-sourced GDP/CPI/PCE/PPI/Fed Funds/yield-curve series, auto-refreshed daily, available as RAG tool context.
- **Portfolios, chat history, analyst reports** — Portfolio-scoped conversations, per-session and cross-session AI summaries, a server-side report-drafting clipboard, publishing to a searchable Fund-Manager repository, and PDF export.

---

## Architecture at a glance

```
FastAPI app (app/main.py)
├── Auth (JWT, opt-in per route — see Known Limitations)
├── Async SQLAlchemy + Postgres (all routes; Alembic-managed schema)
│
├── RAG subsystem (rag/, ingestion/)
│   ├── LangGraph graph: preprocess → retrieve → grade → web-fallback → generate
│   ├── Qdrant: one collection per ticker (hybrid dense + BM25 + RRF)
│   ├── Filing-type + fiscal-quarter aware retrieval (company_mapping.py fiscal calendars)
│   ├── ALPHA / Scenario / Macro "frameworks" layered on the same graph
│   └── Ingestion: PDF text + table extraction + GPT-4o vision for images,
│       EDGAR fetcher (cover-page detection, period_end_date ground truth)
│
├── Quant subsystem (quant/)
│   ├── LangGraph supervisor + 5 sub-agents
│   └── 4 independent MCP servers (Stock Info :8565, Technical :8566,
│       Research :8567, Options :8568)
│
├── Form4 insider-trading pipeline (ingestion/Form4_Ingestion/, rag/utils/Insights_Form4/)
├── Macro data pipeline (ingestion/ingest_macro_data.py)
└── Portfolio / Chat / Report / Integration services (app/services/)
```

See [docs/TECHNICAL_GUIDE.md](docs/TECHNICAL_GUIDE.md) for the full breakdown of every node, service, and endpoint.

---

## Quick start

### Prerequisites
- Python 3.11+
- Docker (for Postgres + Qdrant via `docker-compose`) — or your own instances
- API keys: OpenAI (required), Google (required — chart-summary vision), Tavily (optional — web search), Groq (optional — alt LLM), FRED (required for macro data)

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

### 2. Start Postgres + Qdrant

```bash
docker compose up -d postgres qdrant
```

This publishes Postgres on host port **5433** (not 5432 — avoids clashing with a locally-installed Postgres) and Qdrant on **6333**. If you're running the app itself outside Docker, point `DATABASE_URL` at `localhost:5433`; if you're running the whole stack via `docker compose up`, the `api` service talks to `postgres:5432` internally instead.

### 3. Run migrations

```bash
alembic upgrade head
```

### 4. Start the API

```bash
python -m uvicorn app.main:app --reload --port 8000
```

On startup, the app also creates the LangGraph RAG agent, initializes the quant supervisor (waiting briefly for MCP servers — non-fatal if some aren't up yet), checks/kicks off macro data ingestion if stale, and starts a 24h macro re-sync loop.

### 5. (Optional) Start the 4 quant MCP servers

```bash
cd quant/yahoo-finance-mcp && python server.py &      # Stock Information — :8565
cd quant/Stock_Analysis && python server_mcp.py &     # Technical Analysis — :8566
cd quant/research_mcp && python server_mcp.py &        # Research/Ratings — :8567
cd quant/options_mcp && python server_mcp.py &         # Options Intelligence — :8568
```

Or run everything (Postgres + Qdrant + API + all 4 MCP servers) via Docker:

```bash
docker compose up --build
```

### 6. Ingest some filings to test with

```bash
python scripts/ingest_ticker.py AAPL
# Lists available 10-K/10-Q/8-K filings with fiscal year/quarter labels
# (e.g. "FY2024", "2025Q3"), lets you pick which to pull in and ingest.
```

### 7. Access

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
| `OPENAI_API_KEY` | Required | Primary LLM (RAG generation, quant supervisor, chat summaries) |
| `QDRANT_URL`, `QDRANT_API_KEY` | Required | Vector database (Qdrant Cloud or self-hosted) |
| `GOOGLE_API_KEY` | Required | Gemini vision, used for chart-summary generation |
| `FRED_API_KEY` | Required for macro data | Federal Reserve Economic Data API — **not in `.env.example`, must be added manually** |
| `GROQ_API_KEY` | Optional | Alternative/faster LLM provider |
| `TAVILY_API_KEY` | Optional | Web-search augmentation (research agent, RAG web fallback) |
| `DATABASE_URL` | Required | Postgres connection string (one URL, translated internally to asyncpg at runtime / psycopg2 for Alembic) |
| `POSTGRES_USER`/`PASSWORD`/`DB` | Required (docker-compose) | Initializes the `postgres` container's default role/db |
| `SEC_USER_AGENT` | Optional | Sent on every SEC EDGAR request per SEC's fair-use policy; defaults to a placeholder if unset — **set this to your own contact info** |
| `JWT_SECRET_KEY` | Required in production | Signs auth tokens — **hardcoded insecure default in code if unset; must override before deploying** |
| `ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_DAYS` | Optional | JWT lifetimes (default 30 min / 7 days) |
| `CLOUDINARY_CLOUD_NAME`/`API_KEY`/`API_SECRET` | Optional | Chart image hosting (technical analysis, options charts) — features degrade gracefully if unset |
| `APP_ENV`, `LOG_LEVEL` | Optional | General app config |

Generate a JWT secret: `python -c "import secrets; print(secrets.token_hex(32))"`

---

## Repository layout

```
app/                  FastAPI app: routers (api/), services (services/),
                       auth (auth/), DB models + connection (database/)
rag/                  LangGraph RAG pipeline, Qdrant client, prompts, semantic cache
ingestion/             PDF/table/image processing, EDGAR fetcher, Form4 pipeline,
                       macro data ingestion, table_extractor.py
quant/                 Stock-agent supervisor + sub-agents, 4 MCP servers
                       (yahoo-finance-mcp, Stock_Analysis, research_mcp, options_mcp)
schemas/               Pydantic models (LLM structured outputs, request/response shapes)
alembic/versions/       Database migrations (17 revisions, Postgres-target)
scripts/               ingest_ticker.py (interactive EDGAR ingestion CLI), utilities
docs/                  TECHNICAL_GUIDE.md — the full architectural reference
```

---

## Known limitations (see the technical guide for detail)

- **Auth is opt-in, not global.** Only `/auth/me`, `/auth/logout`, and `PUT /auth/me` require a JWT. Every other router (portfolios, chats, reports, integrations, rag, quant, edgar, form4) currently accepts `user_id` as a plain client-supplied field with no token verification.
- **`FRED_API_KEY` and Cloudinary/JWT vars aren't in `.env.example`** despite being required/used in code — add them manually per the table above.
- Native-PDF tables now extract with real column structure (`ingestion/table_extractor.py`); image-embedded charts/tables go through OCR + GPT-4o vision. Flattened prose extraction is still the fallback when neither applies.

---

**Built with:** FastAPI · LangGraph · LangChain · Qdrant · Postgres (async SQLAlchemy) · SEC EDGAR · FastMCP
