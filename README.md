# Investment Analyst API

**Unified AI-powered investment analysis platform** combining intelligent document analysis, real-time stock market data, portfolio management, chat history persistence, analyst report authoring, and a searchable report repository for Fund Managers.

---

## Features

### Document Analysis (RAG)
- **Intelligent Q&A** — Ask questions about financial documents, 10-Ks, earnings reports
- **Company Comparisons** — Multi-company financial analysis
- **Semantic Caching** — Fast responses for similar queries
- **Conversation Memory** — Context-aware chat sessions with full persistence

### Stock Market Analysis (Quant)
- **Real-time Stock Data** — Current prices, market cap, P/E ratios
- **Technical Analysis** — RSI, SMA, MACD, Bollinger Bands, volume analysis
- **Analyst Research** — Ratings, price targets, bull/bear scenarios
- **Options Intelligence** — Live options chain analysis: put/call ratio, support/resistance, smart money signals, activity chart
- **Multi-Agent System** — Specialized agents for different analysis types

### Macroeconomic Data
- **FRED Integration** — Automated ingestion of GDP, CPI, PCE, PPI, Fed Funds rate, yield curve (GS1M–GS30)
- **Auto-refresh** — Staleness check on startup; background re-ingestion when data is outdated
- **RAG-ready** — Macro indicators available as tool context in document analysis queries

### Portfolio Management
- **User Portfolios** — Create company-specific investment portfolios
- **Session Tracking** — Persistent conversations per portfolio
- **Pre-filtered Vector DB** — Portfolio-scoped document search (85–90% faster)

### Chat History & Persistence
- **Automatic Persistence** — All RAG and Quant conversations saved to database
- **User-Centric Organization** — Retrieve all chats per user across agents
- **Export Capabilities** — Download conversations in JSON or TXT format
- **Session Management** — Soft delete, archiving, title management, summaries

### Report Repository (NEW in v2.2)
- **User Authentication** — JWT-based signup/login with role support (`analyst` / `fund_manager` / `admin`)
- **Draft Clipboard** — Server-side staging for agent-generated text and chart images (replaces localStorage)
- **Report Authoring** — Assemble, edit, and publish investment reports with buy/sell/hold recommendations
- **PDF Export** — Server-generated PDF with colour-coded recommendation, markdown body, and embedded charts
- **Fund Manager Repository** — Searchable, filterable view of all published analyst reports
- **Full-text Search** — FTS5 search across title, company, description, and report body
- **Dashboard Stats** — Aggregate breakdown by recommendation, top companies, top analysts

### Data Integrations
- **Cloud Storage** — AWS S3, Azure Blob Storage, Google Drive
- **Enterprise** — SharePoint, SFTP
- **Extensible** — Easy to add new data sources

---

## Architecture

```
Investment Analyst API v2.2
├── Authentication System (NEW)
│   ├── JWT access + refresh tokens
│   ├── bcrypt password hashing
│   └── Role-based: analyst | fund_manager | admin
│
├── Report Repository (NEW)
│   ├── Draft Clipboard — server-side staging (replaces localStorage)
│   ├── Report CRUD — create, edit, publish, unpublish, delete
│   ├── PDF Export — fpdf2-based, markdown + embedded charts
│   ├── FTS5 Search — SQLite full-text across title/company/description/body
│   └── Repository Stats — FM dashboard aggregates
│
├── Document Analysis (RAG System)
│   ├── LangGraph-powered agentic workflow
│   ├── Hybrid vector search (dense + BM25)
│   └── Semantic caching for performance
│
├── Stock Market Analysis (Quant System)
│   ├── Supervisor Agent (orchestration)
│   └── Sub-Agents:
│       ├── Stock Information Agent (fundamentals)        port 8565
│       ├── Technical Analysis Agent (charts & indicators) port 8566
│       ├── Research Agent (analyst ratings & news)        port 8567
│       ├── Options Intelligence Agent (options chain)     port 8568
│       └── Ticker Finder Agent (symbol lookup)
│
├── Macroeconomic Data
│   ├── FRED API ingestion (GDP, CPI, PCE, PPI, yield curve)
│   ├── Auto-refresh on startup via staleness check
│   └── Stored at data/macro/ as CSV + metadata.json
│
├── Portfolio Management
│   ├── User-specific portfolios
│   ├── Session management
│   └── Vector DB per portfolio
│
├── Chat History System
│   ├── SQLite database with Alembic migrations
│   ├── ChatSession and ChatMessage models
│   └── Full CRUD API for history management
│
└── Data Integrations
    ├── File import & processing
    ├── Cloud storage connectors
    └── Enterprise system integrations
```

---

## Project Structure

```
Investment-Analyst-API/
│
├── app/                           # Application Layer
│   ├── main.py                    # FastAPI application & startup
│   │
│   ├── api/                       # API Endpoints
│   │   ├── auth.py                # Signup, login, JWT (NEW)
│   │   ├── reports.py             # Clipboard + report + repository (NEW)
│   │   ├── portfolios.py          # Portfolio management
│   │   ├── rag.py                 # Document Q&A (ask, compare)
│   │   ├── quant.py               # Stock analysis queries
│   │   ├── chats.py               # Chat history management
│   │   └── integrations.py        # Data source connectors
│   │
│   ├── auth/                      # Auth utilities (NEW)
│   │   ├── jwt.py                 # Token creation & verification
│   │   ├── password.py            # bcrypt hash/verify
│   │   └── deps.py                # get_current_user dependency
│   │
│   ├── services/                  # Business Logic
│   │   ├── report.py              # Draft clipboard + report CRUD + search (NEW)
│   │   ├── portfolio.py           # Portfolio service
│   │   ├── chat.py                # Chat history service
│   │   ├── vectordb_manager.py    # Vector DB instance manager
│   │   ├── stock_agent.py         # Stock agent initialization
│   │   └── connectors/            # Integration connectors
│   │
│   ├── cloudinary.py              # Cloudinary upload helpers
│   └── database/                  # Data Layer
│       ├── connection.py          # Database setup
│       └── models.py              # SQLAlchemy models (all tables)
│
├── rag/                           # Document Analysis Service
│   ├── graph/                     # LangGraph Workflow
│   │   ├── builder.py             # Graph builder
│   │   ├── nodes.py               # Processing nodes
│   │   ├── edges.py               # Routing logic
│   │   └── semantic_cache.py      # Query caching
│   └── vectordb/
│       └── client.py              # Qdrant client with hybrid search
│
├── quant/                         # Stock Analysis Service
│   ├── stock_agent/               # Multi-agent system
│   │   ├── main_agent.py
│   │   └── stock_exchange_agent/subagents/
│   │       ├── stock_information/     # port 8565
│   │       ├── technical_analysis_agent/ # port 8566
│   │       ├── research_agent/        # port 8567
│   │       ├── options_agent/         # port 8568
│   │       └── ticker_finder_tool/
│   └── options_mcp/               # Options Intelligence MCP server (port 8568)
│       ├── server_mcp.py          # FastMCP server
│       ├── analytics.py           # Pure-Python options analytics (no LLM)
│       └── visualization.py       # Plotly chart builder + Cloudinary upload
│
├── ingestion/                     # Data Ingestion Scripts
│   ├── ingest_macro_data.py       # FRED API → data/macro/ CSVs
│   ├── ingest_pdf.py              # PDF → Qdrant vector DB
│   └── Form4_Ingestion/           # SEC Form 4 insider trading pipeline
│       ├── fetch.py               # EDGAR XBRL downloader
│       ├── parse.py               # XML parser
│       ├── analytics.py           # Insider signal analytics
│       └── ingest.py              # Full pipeline runner
│
├── data/                          # Runtime data (not in image — mount via EFS on AWS)
│   └── macro/                     # FRED macro CSVs (auto-generated by ingest_macro_data.py)
│
├── alembic/versions/              # Database Migrations
│   ├── 001_initial.py
│   ├── 002_chat_history.py
│   ├── 003_add_session_metadata.py
│   ├── 004_add_form4_transactions.py
│   ├── 005_add_consolidated_summaries.py
│   ├── 006_add_form4_document_type.py
│   ├── 007_add_form4_has_common_stock.py
│   ├── 008_add_users.py           # NEW
│   ├── 009_add_report_draft_items.py  # NEW
│   └── 010_add_analyst_reports.py    # NEW (includes FTS5)
│
├── docs/                          # Documentation
│   ├── REPORT_REPOSITORY.md       # Complete auth + report system docs (NEW)
│   ├── API_DOCUMENTATION.md       # Core API reference
│   ├── CHAT_HISTORY.md
│   ├── QUANT_INTEGRATION.md
│   └── INTEGRATION_SYSTEM.md
│
├── .env                           # Environment configuration
├── requirements.txt               # Python dependencies
└── README.md                      # This file
```

---

## Quick Start

### 1. Prerequisites

- **Python 3.9+**
- **Qdrant** (local or cloud) for vector database
- **API Keys**: OpenAI, Cloudinary (for chart image storage), optionally Groq, Tavily

### 2. Installation

```bash
git clone <repo-url>
cd Investment_Analyst

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env with your keys
```

### 3. Environment Variables

```bash
# LLM APIs
OPENAI_API_KEY=your_openai_key
GROQ_API_KEY=your_groq_key
TAVILY_API_KEY=your_tavily_key

# Vector Database
QDRANT_URL=http://localhost:6333

# Image Upload (for chart images)
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_cloudinary_key
CLOUDINARY_API_SECRET=your_cloudinary_secret

# Database
DATABASE_URL=sqlite:///./data/portfolios.db

# Macro Data (FRED API — required for macroeconomic indicators)
FRED_API_KEY=your_fred_api_key

# SEC EDGAR (required for Form 4 insider trading ingestion)
SEC_USER_AGENT=your_name your_email@example.com

# Auth — REQUIRED, change in production
JWT_SECRET_KEY=use-a-long-random-string-here
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7
```

Generate a secure JWT secret:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 4. Initialize Database

```bash
alembic upgrade head
# Creates all tables including users, report_draft_items,
# analyst_reports, and FTS5 search index
```

### 5. Start Qdrant

```bash
docker run -p 6333:6333 qdrant/qdrant
```

### 6. Start the API

```bash
python -m uvicorn app.main:app --reload --port 8000
```

### 7. (Optional) Start MCP Servers for Full Stock Analysis

```bash
# Terminal 1 — Stock Information (port 8565)
cd quant/yahoo-finance-mcp && python server.py

# Terminal 2 — Technical Analysis (port 8566)
cd quant/Stock_Analysis && python server_mcp.py

# Terminal 3 — Research (port 8567)
cd quant/research_mcp && python server_mcp.py

# Terminal 4 — Options Intelligence (port 8568)
cd quant/options_mcp && python server_mcp.py
```

Or use the convenience script which starts all four:
```bash
./START_SERVER.sh
```

### 8. Access

| URL | Purpose |
|-----|---------|
| http://localhost:8000 | API root |
| http://localhost:8000/docs | Interactive Swagger UI |
| http://localhost:8000/health | Health check |
| `static/index.html` | Web UI |

---

## API Endpoints

### Authentication (NEW)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/signup` | Register — returns JWT tokens |
| POST | `/auth/login` | Login — returns JWT tokens |
| POST | `/auth/refresh` | Exchange refresh token |
| GET | `/auth/me` | Get current user profile |
| PUT | `/auth/me` | Update name or password |

### Report Clipboard (NEW)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/reports/draft/items` | Clip agent output to staging area |
| GET | `/reports/draft/items/{user_id}` | Load clipboard for creation tab |
| PUT | `/reports/draft/items/{item_id}` | Edit label / content / order |
| POST | `/reports/draft/items/reorder` | Drag-to-reorder |
| DELETE | `/reports/draft/items/{item_id}` | Remove one item |
| DELETE | `/reports/draft/items/user/{user_id}` | Clear entire clipboard |

### Report Authoring (NEW)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/reports/from-draft/{user_id}` | **Main creation endpoint** — assemble from clipboard |
| POST | `/reports` | Create with pre-assembled content |
| GET | `/reports/{id}` | Get single report |
| PUT | `/reports/{id}` | Update report |
| DELETE | `/reports/{id}` | Delete report |
| POST | `/reports/{id}/publish` | Push to FM repository |
| POST | `/reports/{id}/unpublish` | Revert to draft |
| GET | `/reports/{id}/export/pdf` | Download PDF |

### Report Repository (NEW)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/reports/repository/stats` | FM dashboard stats |
| GET | `/reports` | All published reports (with filters) |
| GET | `/reports/user/{user_id}` | Analyst's own reports |
| GET | `/reports/search?q=` | FTS5 full-text search |

**Filter params for list/search:** `company`, `ticker`, `recommendation` (`buy`/`sell`/`hold`), `author`, `portfolio_id`, `from_date`, `to_date`, `page`, `page_size`

### Document Analysis

| Method | Path | Description |
|--------|------|-------------|
| POST | `/ask` | Q&A on financial documents |
| POST | `/compare` | Multi-company comparison |

### Stock Analysis

| Method | Path | Description |
|--------|------|-------------|
| POST | `/quant/query` | Run stock analysis (routes to correct sub-agent) |
| GET | `/quant/health` | MCP server health (ports 8565–8568) |
| GET | `/quant/capabilities` | Available features |

**Example queries routed to each agent:**
- `"What is AAPL's P/E ratio?"` → Stock Information Agent
- `"Show RSI and MACD for TSLA"` → Technical Analysis Agent
- `"What do analysts say about NVDA?"` → Research Agent
- `"Analyze the options chain of AAPL"` → Options Intelligence Agent

### Chat History

| Method | Path | Description |
|--------|------|-------------|
| GET | `/chats/user/{user_id}/sessions` | All user sessions |
| GET | `/chats/session/{id}` | Full chat history |
| GET | `/chats/session/{id}/export` | Export JSON/TXT |
| PUT | `/chats/session/{id}/title` | Rename session |
| POST | `/chats/sessions/consolidated-summary` | Cross-session summary |
| DELETE | `/chats/session/{id}` | Delete session |

### Portfolio Management

| Method | Path | Description |
|--------|------|-------------|
| POST | `/portfolios/` | Create portfolio |
| GET | `/portfolios/user/{user_id}` | List portfolios |
| PUT | `/portfolios/{id}` | Update portfolio |
| DELETE | `/portfolios/{id}` | Delete portfolio |

---

## Report Repository — Complete UI Flow

### Analyst: Authoring a Report

```
1. Run RAG or Quant agent → generates text/chart
   → POST /reports/draft/items
     { user_id, item_type: "text"|"image"|"summary",
       content, image_url, source: "rag"|"quant"|"summary", label }

2. Open creation tab
   → GET /reports/draft/items/{user_id}
   → Render sections, allow reorder / label edit / remove

3. Fill metadata + click "Create Report"
   → POST /reports/from-draft/{user_id}
     { title, company_name, ticker, recommendation, description, tags }
   ← { id: 7, status: "draft", ... }

4. Download PDF
   → GET /reports/7/export/pdf

5. Publish to repository
   → POST /reports/7/publish?user_id=ganesh
```

### Fund Manager: Using the Repository

```
1. Dashboard stats
   → GET /reports/repository/stats

2. Browse published reports
   → GET /reports?recommendation=buy&from_date=2026-01-01T00:00:00

3. Search
   → GET /reports/search?q=rising+interest+rates
   → GET /reports/search?q=Apple+services&recommendation=buy

4. Download a report PDF
   → GET /reports/{id}/export/pdf
```

---

## Database Schema

| Table | Purpose |
|-------|---------|
| `portfolios` | User investment portfolios |
| `chat_sessions` | RAG + Quant conversation sessions |
| `chat_messages` | Individual messages |
| `consolidated_summaries` | Cross-session AI summaries |
| `integrations` | Data source connector configs |
| `form4_transactions` | SEC Form 4 insider trading data |
| `users` | Analyst / Fund Manager accounts (NEW) |
| `report_draft_items` | Clipboard staging (NEW) |
| `analyst_reports` | Permanent report records (NEW) |
| `analyst_reports_fts` | FTS5 full-text search index (NEW) |
| `form4_transactions` | SEC Form 4 insider trading transactions |

All schema changes managed via Alembic:
```bash
alembic upgrade head      # apply all migrations
alembic current           # check current version
alembic downgrade -1      # roll back one step
```

---

## What's New in v2.2

### User Authentication
- JWT-based signup/login with access (30 min) + refresh (7 day) tokens
- bcrypt password hashing
- Roles: `analyst`, `fund_manager`, `admin`

### Report Repository
- **Server-side clipboard** — replaces localStorage; text blocks, chart images, and summaries are persisted per user
- **One-call report creation** — `POST /reports/from-draft/{user_id}` reads clipboard, assembles markdown, clears clipboard, returns report ID
- **PDF export** — server-generated PDF with cover header, recommendation badge, markdown body, embedded Cloudinary charts
- **Fund Manager repository** — filterable list of published reports with date range support
- **FTS5 search** — full-text search indexed on title, company, description, and body; combine with metadata filters
- **Dashboard stats** — total reports, breakdown by recommendation, top companies, top analysts, recent report cards

---

## Documentation

| Document | Description |
|----------|-------------|
| [README.md](README.md) | This file |
| [docs/REPORT_REPOSITORY.md](docs/REPORT_REPOSITORY.md) | Complete auth + report system reference |
| [docs/API_DOCUMENTATION.md](docs/API_DOCUMENTATION.md) | Core API reference |
| [docs/CHAT_HISTORY.md](docs/CHAT_HISTORY.md) | Chat persistence documentation |
| [docs/QUANT_INTEGRATION.md](docs/QUANT_INTEGRATION.md) | Stock analysis integration |
| [docs/INTEGRATION_SYSTEM.md](docs/INTEGRATION_SYSTEM.md) | Data connectors guide |
| http://localhost:8000/docs | Interactive Swagger UI |

---

## Deployment

### Docker

```bash
docker build -t investment-analyst-api .
docker run -p 8000:8000 --env-file .env investment-analyst-api
```

### Manual

```bash
pip install -r requirements.txt
alembic upgrade head
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

**Built with FastAPI · LangGraph · LangChain · Qdrant · SQLite FTS5**

*Unified platform. Intelligent analysis. Production-ready.*
