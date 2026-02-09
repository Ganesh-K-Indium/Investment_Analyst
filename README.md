# Investment Analyst API

**Unified AI-powered investment analysis platform** combining intelligent document analysis, real-time stock market data, portfolio management, chat history persistence, and multi-source data integrations.

## Features

### Document Analysis (RAG)
- **Intelligent Q&A**: Ask questions about financial documents, 10-Ks, earnings reports
- **Company Comparisons**: Multi-company financial analysis
- **Semantic Caching**: Fast responses for similar queries
- **Conversation Memory**: Context-aware chat sessions with full persistence

### Stock Market Analysis (Quant)
- **Real-time Stock Data**: Current prices, market cap, P/E ratios
- **Technical Analysis**: RSI, SMA, MACD, Bollinger Bands, volume analysis
- **Analyst Research**: Ratings, price targets, bull/bear scenarios
- **Multi-Agent System**: Specialized agents for different analysis types

### Portfolio Management
- **User Portfolios**: Create company-specific investment portfolios
- **Session Tracking**: Persistent conversations per portfolio
- **Pre-filtered Vector DB**: Portfolio-scoped document search (85-90% faster)
- **Unified Context**: Both document and stock analysis use portfolio context

### Chat History & Persistence (NEW)
- **Automatic Persistence**: All RAG and Quant conversations saved to database
- **User-Centric Organization**: Retrieve all chats per user across agents
- **Portfolio Linking**: Organize chats by investment portfolios
- **Export Capabilities**: Download conversations in JSON or TXT format
- **Session Management**: Soft delete, archiving, and title management
- **Chat Restoration**: Resume conversations when users log back in
- **Metadata Tracking**: Sources, citations, and agent context preserved

### Data Integrations
- **Cloud Storage**: AWS S3, Azure Blob Storage, Google Drive
- **Enterprise**: SharePoint, SFTP
- **Extensible**: Easy to add new data sources

---

## Architecture

```
Investment Analyst API v2.1
├── Document Analysis (RAG System)
│   ├── LangGraph-powered agentic workflow
│   ├── Hybrid vector search (dense + BM25)
│   └── Semantic caching for performance
│
├── Stock Market Analysis (Quant System)
│   ├── Supervisor Agent (orchestration)
│   └── Sub-Agents:
│       ├── Stock Information Agent (fundamentals)
│       ├── Technical Analysis Agent (charts & indicators)
│       ├── Research Agent (analyst ratings & news)
│       └── Ticker Finder Agent (symbol lookup)
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
│   │   ├── portfolios.py          # Portfolio management
│   │   ├── rag.py                 # Document Q&A (ask, compare)
│   │   ├── quant.py               # Stock analysis queries
│   │   ├── chats.py               # Chat history management (NEW)
│   │   └── integrations.py        # Data source connectors
│   │
│   ├── services/                  # Business Logic
│   │   ├── portfolio.py           # Portfolio service
│   │   ├── chat.py                # Chat history service (NEW)
│   │   ├── vectordb_manager.py    # Vector DB instance manager
│   │   ├── stock_agent.py         # Stock agent initialization
│   │   └── connectors/            # Integration connectors
│   │
│   └── database/                  # Data Layer
│       ├── connection.py          # Database setup
│       └── models.py              # SQLAlchemy models (Portfolio, ChatSession, ChatMessage)
│
├── rag/                           # Document Analysis Service
│   ├── graph/                     # LangGraph Workflow
│   │   ├── state.py               # State management
│   │   ├── nodes.py               # Processing nodes
│   │   ├── edges.py               # Routing logic
│   │   ├── builder.py             # Graph builder
│   │   └── semantic_cache.py      # Query caching
│   │
│   └── vectordb/                  # Vector Database
│       └── client.py              # Qdrant client with hybrid search
│
├── quant/                         # Stock Analysis Service
│   └── stock_agent/               # Multi-agent system
│       ├── main_agent.py          # Supervisor agent
│       ├── api_server.py          # Standalone API (reference)
│       └── stock_exchange_agent/  # Sub-agents
│           └── subagents/
│               ├── stock_information/
│               ├── technical_analysis_agent/
│               ├── research_agent/
│               └── ticker_finder_tool/
│
├── alembic/                       # Database Migrations (NEW)
│   ├── versions/
│   │   ├── 001_initial.py         # Initial schema
│   │   └── 002_chat_history.py    # Chat tables
│   ├── env.py
│   └── script.py.mako
│
├── scripts/                       # Utility Scripts (NEW)
│   ├── start_api.sh               # Start main API
│   ├── start_mcp_servers.sh       # Manage MCP servers
│   └── README.md                  # Scripts documentation
│
├── docs/                          # Documentation
│   ├── CHAT_HISTORY.md            # Chat system documentation (NEW)
│   ├── QUANT_INTEGRATION.md       # Stock system integration guide
│   └── INTEGRATION_SYSTEM.md      # Data connectors guide
│
├── static/                        # Frontend
│   └── index.html                 # Web UI
│
├── output/                        # Generated Outputs
│   └── json/
│       └── quant/                 # Stock analysis responses
│
├── .env                           # Environment configuration
├── alembic.ini                    # Alembic configuration (NEW)
├── requirements.txt               # Python dependencies
└── README.md                      # This file
```

---

## Quick Start

### 1. Prerequisites

- **Python 3.9+**
- **Qdrant** (local or cloud) for vector database
- **API Keys**:
  - OpenAI (for embeddings & LLM)
  - Groq (optional, alternative LLM)
  - Tavily (for web search)
  - Cloudinary (for image uploads)

### 2. Installation

```bash
# Clone repository
git clone <repo-url>
cd Agentic-RAG

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Setup environment variables
cp .env.example .env
# Edit .env with your API keys
```

### 3. Initialize Database

```bash
# Run database migrations
alembic upgrade head

# This creates:
# - portfolios.db (main database)
# - checkpoints.sqlite (RAG conversation memory)
# - checkpoints_stock.sqlite (Quant conversation memory)
```

### 4. Start Qdrant (Local)

```bash
docker run -p 6333:6333 qdrant/qdrant
```

### 5. Start the API Server

**Option A: Using the startup script (Recommended)**

```bash
# Start API with optional MCP servers
./scripts/start_api.sh --with-mcp

# Or start API only
./scripts/start_api.sh
```

**Option B: Manual startup**

```bash
# Activate virtual environment
source venv/bin/activate

# Start server
python -m uvicorn app.main:app --reload --port 8000
```

### 6. (Optional) Start MCP Servers for Full Stock Analysis

```bash
# Use the management script
./scripts/start_mcp_servers.sh start

# Or start manually in separate terminals:

# Terminal 1: Stock Information Server (port 8565)
cd quant/Stock_Info
python server_mcp.py

# Terminal 2: Technical Analysis Server (port 8566)
cd quant/Stock_Analysis
python server_mcp.py

# Terminal 3: Research Server (port 8567)
cd quant/research_mcp
python server_mcp.py
```

**Note**: The API will work without MCP servers but with limited stock analysis functionality.

### 7. Access the Application

- **API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **Web UI**: Open `static/index.html` in browser
- **Health Check**: http://localhost:8000/health

---

## Usage Examples

### Document Analysis

#### 1. Create a Portfolio

```bash
curl -X POST http://localhost:8000/portfolios/ \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user123",
    "name": "Tech Portfolio",
    "company_names": ["apple", "microsoft", "google"],
    "description": "Major tech companies analysis"
  }'
```

#### 2. Create Session for Portfolio

```bash
curl -X POST http://localhost:8000/portfolios/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "portfolio_id": 1,
    "user_id": "user123"
  }'
```

**Note**: This initializes the Vector DB for fast portfolio-scoped queries and creates a chat session!

#### 3. Ask Questions About Documents

```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What is Apple'\''s revenue growth strategy?",
    "thread_id": "portfolio_1_abc123"
  }'
```

**Chat is automatically persisted to the database!**

### Stock Market Analysis

#### 1. Query Stock Information

```bash
curl -X POST http://localhost:8000/quant/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What is Apple'\''s current stock price and P/E ratio?",
    "user_id": "user123",
    "portfolio_id": 1
  }'
```

**Response and conversation are automatically saved!**

### Chat History Management

#### 1. Get All User's Chat Sessions

```bash
curl http://localhost:8000/chats/user/user123/sessions
```

**Optional filters:**
- `?agent_type=rag` - Only RAG conversations
- `?agent_type=quant` - Only stock analysis conversations
- `?portfolio_id=1` - Only sessions for specific portfolio
- `?include_inactive=true` - Include archived sessions

#### 2. Get Complete Chat History

```bash
curl http://localhost:8000/chats/session/portfolio_1_abc123
```

**Optional pagination:**
- `?limit=50` - Limit number of messages
- `?offset=0` - Skip first N messages

#### 3. Export Chat Session

```bash
# Export as JSON
curl http://localhost:8000/chats/session/portfolio_1_abc123/export?format=json

# Export as TXT
curl http://localhost:8000/chats/session/portfolio_1_abc123/export?format=txt
```

#### 4. Update Session Title

```bash
curl -X PUT http://localhost:8000/chats/session/portfolio_1_abc123/title \
  -H "Content-Type: application/json" \
  -d '{"title": "Apple Revenue Analysis"}'
```

#### 5. Clear Session Messages

```bash
# Clear all messages but keep session
curl -X DELETE http://localhost:8000/chats/session/portfolio_1_abc123/messages
```

#### 6. Delete Session Permanently

```bash
curl -X DELETE http://localhost:8000/chats/session/portfolio_1_abc123
```

#### 7. Get Chat Statistics

```bash
# Session statistics
curl http://localhost:8000/chats/session/portfolio_1_abc123/stats

# User statistics
curl http://localhost:8000/chats/user/user123/stats
```

---

## API Endpoints

### Portfolio Management
- `POST /portfolios/` - Create portfolio
- `GET /portfolios/{id}` - Get portfolio details
- `GET /portfolios/user/{user_id}` - List user portfolios
- `PUT /portfolios/{id}` - Update portfolio
- `DELETE /portfolios/{id}` - Delete portfolio
- `POST /portfolios/sessions` - Create session
- `GET /portfolios/sessions/{thread_id}` - Get session details

### Document Analysis
- `POST /ask` - Ask questions about documents (auto-persists chat)
- `POST /compare` - Compare companies

### Stock Analysis
- `POST /quant/query` - Query stock analysis system (auto-persists chat)
- `GET /quant/health` - Check stock system health
- `GET /quant/capabilities` - List available features

### Chat History (NEW)
- `GET /chats/user/{user_id}/sessions` - Get all user's chat sessions
- `GET /chats/session/{session_id}` - Get complete chat history
- `GET /chats/session/{session_id}/export` - Export chat (JSON/TXT)
- `PUT /chats/session/{session_id}/title` - Update session title
- `DELETE /chats/session/{session_id}/messages` - Clear messages
- `DELETE /chats/session/{session_id}` - Delete session permanently
- `POST /chats/session/{session_id}/deactivate` - Archive session
- `GET /chats/session/{session_id}/stats` - Get session statistics
- `GET /chats/user/{user_id}/stats` - Get user's chat statistics
- `GET /chats/portfolio/{portfolio_id}/sessions` - Get portfolio's chats

### Data Integrations
- `GET /integrations/` - List available integrations
- `POST /integrations/import` - Import files from sources
- Various connector-specific endpoints

### System
- `GET /` - API information
- `GET /health` - Overall system health
- `GET /docs` - Interactive API documentation

---

## Configuration

### Environment Variables (`.env`)

```bash
# LLM APIs
OPENAI_API_KEY=your_openai_key
GROQ_API_KEY=your_groq_key
TAVILY_API_KEY=your_tavily_key

# Vector Database
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=your_qdrant_key  # For cloud Qdrant

# Image Upload
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_cloudinary_key
CLOUDINARY_API_SECRET=your_cloudinary_secret

# Database
DATABASE_URL=sqlite:///./portfolios.db

# Chat History (managed automatically)
# Main database: portfolios.db (includes chat_sessions, chat_messages)

# Conversation Memory (LangGraph checkpointers)
STOCK_SQLITE_DB_PATH=checkpoints_stock.sqlite
# RAG checkpointer: checkpoints.sqlite (default)
```

---

## Performance

### Portfolio-Scoped Vector DB

**Before (Old Architecture):**
- DB initialized on every query
- 60+ seconds per query (timeout)
- High connection overhead

**After (Current Architecture):**
- DB initialized once at portfolio creation
- 5-10 seconds per query (85-90% improvement!)
- Zero overhead (cached connections)
- All sessions share same portfolio DB instance

### Chat History System

- **Indexed Queries**: Fast retrieval with proper database indexing
- **Pagination Support**: Efficient handling of large chat histories
- **Metadata Storage**: Rich context without performance impact
- **Cascading Deletes**: Clean data management

---

## Key Features

| Feature | Description |
|---------|-------------|
| **Unified Platform** | Document analysis + Stock data + Chat history in one API |
| **Portfolio-Centric** | All services work with user portfolios |
| **Multi-Agent System** | Specialized AI agents for different tasks |
| **Conversation Persistence** | Full chat history with export capabilities |
| **Semantic Caching** | Fast responses for similar queries |
| **Data Integrations** | Connect to multiple data sources |
| **Production-Ready** | Error handling, logging, health checks, migrations |
| **Modular Architecture** | Easy to extend and maintain |

---

## Testing

### Test the Chat History System

```bash
# Run comprehensive chat system tests
python test_chat_system.py
```

This will test:
- Chat session creation
- Message persistence (RAG and Quant)
- History retrieval
- Export functionality
- Session management
- User statistics

### Test Other Systems

```bash
# Check overall system health
curl http://localhost:8000/health

# Check stock analysis system
curl http://localhost:8000/quant/health

# Test RAG system
python test_rag_detailed.py
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [README.md](README.md) | This file - Quick start guide |
| [CHAT_HISTORY.md](docs/CHAT_HISTORY.md) | Complete chat persistence documentation |
| [QUANT_INTEGRATION.md](docs/QUANT_INTEGRATION.md) | Stock analysis integration details |
| [INTEGRATION_SYSTEM.md](docs/INTEGRATION_SYSTEM.md) | Data connector documentation |
| [Scripts README](scripts/README.md) | Startup scripts documentation |
| [/docs](http://localhost:8000/docs) | Interactive API documentation |

---

## Database Migrations

The project uses Alembic for database schema management:

```bash
# View current migration status
alembic current

# Upgrade to latest schema
alembic upgrade head

# Rollback one migration
alembic downgrade -1

# Create new migration
alembic revision --autogenerate -m "description"
```

**Important**: The chat history tables are created by the `002_chat_history` migration.

---

## Deployment

### Docker (Recommended)

```bash
# Build image
docker build -t investment-analyst-api .

# Run container
docker run -p 8000:8000 --env-file .env investment-analyst-api
```

### Manual Deployment

1. Install dependencies: `pip install -r requirements.txt`
2. Set environment variables
3. Run migrations: `alembic upgrade head`
4. Start Qdrant database
5. Start MCP servers (optional)
6. Run: `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`

---

## Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

---

## License

This project is licensed under the MIT License.

---

## Tips & Tricks

### Starting Development

```bash
# Start all services with the convenience script
./scripts/start_api.sh --with-mcp

# Or manually:
source venv/bin/activate
python -m uvicorn app.main:app --reload
```

### Managing MCP Servers

```bash
# Start all MCP servers
./scripts/start_mcp_servers.sh start

# Check status
./scripts/start_mcp_servers.sh status

# Stop all
./scripts/start_mcp_servers.sh stop

# Restart all
./scripts/start_mcp_servers.sh restart

# View logs
./scripts/start_mcp_servers.sh logs
```

### Viewing Logs

- Console output shows detailed operation logs
- Response files saved to `output/json/`
- Stock analysis responses in `output/json/quant/`
- MCP server logs in `logs/mcp/`

### Working with Chat History

```python
# In your application code
from app.services.chat import ChatService
from app.database.models import AgentType, MessageRole

# Create/get chat session
session = ChatService.create_or_get_chat_session(
    db=db,
    session_id="session_123",
    user_id="user_456",
    agent_type=AgentType.RAG,
    portfolio_id=1
)

# Save message
ChatService.add_message(
    db=db,
    session_id="session_123",
    role=MessageRole.USER,
    content="What is Apple's revenue?",
    metadata={"source": "api"}
)

# Retrieve history
messages = ChatService.get_session_messages(
    db=db,
    session_id="session_123"
)

# Export session
export_data = ChatService.export_session(
    db=db,
    session_id="session_123"
)
```

### Testing the Web UI

1. Start the server: `./scripts/start_api.sh`
2. Open `static/index.html` in browser
3. Login with any user ID
4. Create portfolio and start analyzing!
5. View your chat history in the interface

### Adding New Features

The modular architecture makes it easy to extend:

- **New AI Service**: Create new folder (e.g., `sentiment/`)
- **New Integration**: Add connector to `app/services/connectors/`
- **New Endpoint**: Add router to `app/api/`
- **New Chat Type**: Extend `AgentType` enum in `models.py`

---

## Support

- **Issues**: Create an issue on GitHub
- **Documentation**: Check `docs/` folder
- **API Docs**: Visit http://localhost:8000/docs
- **Chat System**: See `docs/CHAT_HISTORY.md`
- **Integration Guide**: See `docs/QUANT_INTEGRATION.md`

---

## What's New in v2.1

### Chat History & Persistence
- **Automatic chat persistence** for all RAG and Quant conversations
- **Production-grade database schema** with proper indexing
- **Export capabilities** to JSON and TXT formats
- **Session management** with soft delete and archiving
- **User-centric organization** to retrieve all chats per user
- **Portfolio integration** to link chats with investment portfolios
- **Metadata tracking** for sources, citations, and context
- **Alembic migrations** for schema management

### Stock Market Analysis
- Real-time data, technical indicators, analyst research
- Multi-agent system with supervisor coordination
- MCP server integration with graceful degradation

### Infrastructure
- Separate SQLite checkpointers for RAG and Quant
- Enhanced error handling and logging
- Health monitoring for all components
- Startup scripts for easy service management

### Architecture
- Portfolio-scoped vector database optimization
- Session-based conversation tracking
- Improved performance with semantic caching
- Modular and extensible design

---

**Built with FastAPI, LangGraph, LangChain, and Qdrant**

*Unified platform. Intelligent analysis. Production-ready.*
