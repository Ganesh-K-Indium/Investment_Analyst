# Architecture Documentation - Agentic RAG API v2.0

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend Application                     │
│                    (React/Vue/Angular/Mobile)                    │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP/REST
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FastAPI Application                         │
│                         (app_v2.py)                              │
│                                                                  │
│  ┌────────────────────┐  ┌────────────────────┐                │
│  │  Portfolio Router  │  │    RAG Router      │                │
│  │  (/portfolios/*)   │  │  (/ask, /compare)  │                │
│  └─────────┬──────────┘  └─────────┬──────────┘                │
│            │                        │                            │
│            ▼                        ▼                            │
│  ┌────────────────────────────────────────────┐                │
│  │         Portfolio Service Layer            │                │
│  │     (Business Logic & Validation)          │                │
│  └────────────────────┬───────────────────────┘                │
│                       │                                          │
└───────────────────────┼──────────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│   SQLite     │ │   LangGraph  │ │   Qdrant     │
│  (Portfolio  │ │ (Checkpointer│ │  (Vector DB) │
│   + Session) │ │  + Memory)   │ │              │
└──────────────┘ └──────────────┘ └──────────────┘
```

## Component Architecture

### 1. Application Layer (`app_v2.py`)

**Responsibilities:**
- FastAPI app initialization
- Middleware configuration (CORS)
- Router registration
- Startup/shutdown lifecycle management
- Global instance management (agent, cache, checkpointer)

**Key Components:**
```python
├── FastAPI app
├── Middleware (CORS)
├── LangGraph agent
├── Semantic cache
├── AsyncSqliteSaver checkpointer
└── Router inclusions
```

### 2. Router Layer (`routers/`)

#### Portfolio Router (`portfolio_router.py`)
```
Endpoints:
├── POST   /portfolios/              → Create portfolio
├── GET    /portfolios/{id}          → Get portfolio
├── GET    /portfolios/user/{id}     → List user portfolios
├── PUT    /portfolios/{id}          → Update portfolio
├── DELETE /portfolios/{id}          → Delete portfolio
├── POST   /portfolios/sessions      → Create session
└── GET    /portfolios/sessions/{id} → Get session
```

**Responsibilities:**
- Handle portfolio CRUD operations
- Validate request payloads
- Call service layer
- Return formatted responses

#### RAG Router (`rag_router.py`)
```
Endpoints:
├── POST /ask     → Portfolio-based RAG query
└── POST /compare → Ad-hoc company comparison
```

**Responsibilities:**
- Handle RAG queries
- Manage conversation context
- Interact with LangGraph agent
- Cache management
- Response logging

### 3. Service Layer (`services/`)

#### Portfolio Service (`portfolio_service.py`)

**Business Logic:**
- Portfolio management operations
- Session management
- Company name normalization
- Data validation
- State management

**Key Methods:**
```python
├── create_portfolio()       → Create new portfolio
├── get_portfolio()          → Retrieve portfolio
├── get_user_portfolios()    → List user's portfolios
├── update_portfolio()       → Update portfolio
├── delete_portfolio()       → Delete portfolio (cascade sessions)
├── create_session()         → Create/update session
├── get_session()            → Get session with timestamp update
└── get_session_portfolio()  → Get portfolio from session
```

### 4. Database Layer (`database/`)

#### Models (`models.py`)

**Portfolio Model:**
```python
Portfolio:
├── id: Integer (PK)
├── user_id: String (indexed)
├── name: String
├── company_names: JSON (List[str])
├── description: Text
├── created_at: DateTime
├── updated_at: DateTime
└── sessions: Relationship (1-to-many)
```

**Session Model:**
```python
Session:
├── id: String (PK, thread_id)
├── portfolio_id: Integer (FK)
├── user_id: String (indexed)
├── created_at: DateTime
├── last_accessed: DateTime
└── portfolio: Relationship (many-to-1)
```

#### Connection (`connection.py`)
- SQLAlchemy engine setup
- Session management
- Database initialization
- Connection pooling

### 5. Graph Layer (`Graph/`)

**Existing Components (Reused):**
- `invoke_graph.py` - LangGraph workflow
- `nodes.py` - RAG nodes (retrieve, generate, etc.)
- `edges.py` - Decision logic
- `graph_state.py` - State management
- `semantic_cache.py` - Query caching

## Data Flow

### Flow 1: Portfolio Creation & Session Start

```
Frontend                API                Service              Database
   │                     │                   │                    │
   │──Create Portfolio──>│                   │                    │
   │                     │──Validate────────>│                    │
   │                     │                   │──Normalize────────>│
   │                     │                   │    Companies       │
   │                     │                   │──Insert Portfolio─>│
   │                     │<──Portfolio───────│<──Return ID───────│
   │<──Response──────────│                   │                    │
   │                     │                   │                    │
   │──Create Session────>│                   │                    │
   │                     │──Get Portfolio───>│                    │
   │                     │<──Portfolio───────│                    │
   │                     │──Create Session──>│                    │
   │                     │                   │──Insert Session───>│
   │                     │<──Session─────────│<──Return──────────│
   │<──thread_id─────────│                   │                    │
```

### Flow 2: Ask Query (Portfolio-based)

```
Frontend                API                Service        LangGraph      Qdrant
   │                     │                   │               │            │
   │──Ask Question──────>│                   │               │            │
   │  + thread_id        │                   │               │            │
   │                     │──Get Session─────>│               │            │
   │                     │<──Portfolio───────│               │            │
   │                     │    + Companies    │               │            │
   │                     │                   │               │            │
   │                     │──Check Cache──────────────────────>│            │
   │                     │<──Cache Miss──────────────────────┘            │
   │                     │                   │               │            │
   │                     │──Invoke Agent─────────────────────>│            │
   │                     │  + company_filter │               │            │
   │                     │                   │               │──Retrieve─>│
   │                     │                   │               │  (filtered)│
   │                     │                   │               │<──Results──│
   │                     │                   │               │            │
   │                     │                   │               │──Generate──│
   │                     │<──Answer──────────────────────────│   Answer   │
   │                     │──Update Cache─────────────────────>│            │
   │<──Response──────────│                   │               │            │
   │  + answer           │                   │               │            │
   │  + portfolio_name   │                   │               │            │
   │  + company_filter   │                   │               │            │
```

### Flow 3: Compare (Ad-hoc)

```
Frontend                API                LangGraph        Qdrant
   │                     │                   │               │
   │──Compare───────────>│                   │               │
   │  company1, 2, 3     │                   │               │
   │                     │──Invoke Agent────>│               │
   │                     │  (no portfolio)   │               │
   │                     │  + ad-hoc filter  │               │
   │                     │                   │──Retrieve────>│
   │                     │                   │  (companies)  │
   │                     │                   │<──Results─────│
   │                     │                   │               │
   │                     │                   │──Generate─────│
   │                     │                   │  Comparison   │
   │                     │                   │──Create Chart─│
   │                     │<──Answer + Chart──│               │
   │<──Response──────────│                   │               │
   │  + answer           │                   │               │
   │  + chart_url        │                   │               │
```

## State Management

### State Isolation

```
┌────────────────────────────────────────────────────────────┐
│                    User Session                            │
│                                                            │
│  ┌─────────────────────┐      ┌──────────────────────┐   │
│  │  Portfolio Context  │      │  Compare Context     │   │
│  │  (Persistent)       │      │  (Temporary)         │   │
│  │                     │      │                      │   │
│  │  thread_id: "p_1.."│      │  thread_id: "c_123" │   │
│  │  companies: [A,B,C] │      │  companies: [X,Y,Z] │   │
│  │  ↓                  │      │  ↓                   │   │
│  │  Ask: "Revenue?"    │      │  Compare: X vs Y vs Z│   │
│  │  Uses: A, B, C      │      │  Uses: X, Y, Z       │   │
│  │  ↓                  │      │  ↓                   │   │
│  │  Ask: "Growth?"     │      │  [Independent]       │   │
│  │  Uses: A, B, C      │      │                      │   │
│  └─────────────────────┘      └──────────────────────┘   │
│                                                            │
│  No interference between contexts                          │
└────────────────────────────────────────────────────────────┘
```

### Session Lifecycle

```
1. Portfolio Creation (One-time)
   ↓
2. Session Creation (Per portfolio/device)
   ↓
3. Multiple Queries (Same thread_id)
   ↓
4. Session Persists (Resume anytime)
   ↓
5. Session Cleanup (Optional, based on last_accessed)
```

## Security Considerations

### Current Implementation
- No authentication (add JWT/OAuth)
- No authorization (add user ownership checks)
- No rate limiting (add middleware)
- CORS: Allow all (restrict in production)

### Production Additions Needed

```python
# 1. Authentication Middleware
@app.middleware("http")
async def verify_token(request: Request, call_next):
    token = request.headers.get("Authorization")
    # Verify JWT token
    # Add user_id to request.state
    return await call_next(request)

# 2. Authorization Checks
def verify_portfolio_ownership(user_id: str, portfolio_id: int):
    portfolio = get_portfolio(portfolio_id)
    if portfolio.user_id != user_id:
        raise HTTPException(403, "Forbidden")

# 3. Rate Limiting
from slowapi import Limiter
limiter = Limiter(key_func=get_remote_address)
@app.post("/ask")
@limiter.limit("10/minute")
async def ask_agent(...):
    ...
```

## Scaling Strategy

### Horizontal Scaling

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│ FastAPI  │     │ FastAPI  │     │ FastAPI  │
│ Worker 1 │     │ Worker 2 │     │ Worker N │
└────┬─────┘     └────┬─────┘     └────┬─────┘
     │                │                │
     └────────────────┼────────────────┘
                      │
         ┌────────────▼────────────┐
         │    Load Balancer        │
         │      (Nginx/AWS)        │
         └────────────┬────────────┘
                      │
         ┌────────────▼────────────┐
         │  Shared Database Layer  │
         │  (PostgreSQL + Redis)   │
         └─────────────────────────┘
```

### Database Scaling

**Current (SQLite):**
- Good for: Development, single-server deployment
- Limitations: No concurrent writes, single server

**Production (PostgreSQL):**
```python
DATABASE_URL = "postgresql://user:pass@host:5432/dbname"
engine = create_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=40
)
```

### Caching Strategy

**Current (In-Memory Semantic Cache):**
- Good for: Single worker
- Limitations: Not shared across workers

**Production (Redis):**
```python
import redis
cache_client = redis.Redis(host='localhost', port=6379)

# Cache RAG responses
cache_client.setex(
    f"rag:{query_hash}",
    3600,  # 1 hour TTL
    json.dumps(response)
)
```

## Monitoring & Observability

### Metrics to Track

1. **Request Metrics**
   - Request rate (requests/sec)
   - Response time (p50, p95, p99)
   - Error rate

2. **Business Metrics**
   - Portfolios created
   - Sessions active
   - Queries per session
   - Cache hit rate

3. **Resource Metrics**
   - CPU usage
   - Memory usage
   - Database connections
   - Qdrant query latency

### Logging Strategy

```python
import logging
from pythonjsonlogger import jsonlogger

logger = logging.getLogger()
handler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter()
handler.setFormatter(formatter)
logger.addHandler(handler)

# Log with context
logger.info("Query processed", extra={
    "user_id": user_id,
    "portfolio_id": portfolio_id,
    "query_length": len(query),
    "response_time_ms": elapsed,
    "cache_hit": cache_hit
})
```

## Technology Stack

### Backend
- **FastAPI** - Web framework
- **SQLAlchemy** - ORM
- **Pydantic** - Data validation
- **LangGraph** - Agent orchestration
- **LangChain** - LLM framework

### Storage
- **SQLite** (dev) / **PostgreSQL** (prod) - Relational data
- **Qdrant** - Vector database
- **Redis** (optional) - Caching

### AI/ML
- **OpenAI** - Embeddings + LLM
- **Groq** (optional) - Alternative LLM
- **Tavily** - Web search

## Performance Characteristics

### Latency Breakdown

```
Portfolio Query (/ask):
├── Session lookup:        ~5ms
├── Cache check:          ~10ms
├── LangGraph invoke:    ~2-5s
│   ├── Qdrant search:   ~100-300ms
│   ├── LLM generation:  ~1-4s
│   └── Post-processing: ~50-100ms
└── Cache update:         ~10ms
Total:                    ~2-5s (cache miss)
                          ~15ms (cache hit)

Comparison Query (/compare):
├── LangGraph invoke:    ~5-10s
│   ├── Multi-company:   ~300-500ms
│   ├── Chart generation:~1-2s
│   └── LLM generation:  ~3-7s
└── Response:             ~5-10s
```

### Optimization Strategies

1. **Cache warming** - Pre-populate common queries
2. **Parallel retrieval** - Query Qdrant for multiple companies simultaneously
3. **Streaming responses** - Return partial results as available
4. **Connection pooling** - Reuse database connections
5. **Async operations** - Non-blocking I/O throughout

## Future Enhancements

### Phase 1: Core Features
- ✅ Portfolio management
- ✅ Session persistence
- ✅ State isolation

### Phase 2: Enhanced Security
- [ ] JWT authentication
- [ ] User authorization
- [ ] Rate limiting
- [ ] API keys

### Phase 3: Collaboration
- [ ] Shared portfolios
- [ ] Team workspaces
- [ ] Portfolio permissions

### Phase 4: Analytics
- [ ] Query analytics
- [ ] Portfolio insights
- [ ] Usage dashboards

### Phase 5: Advanced Features
- [ ] Portfolio recommendations
- [ ] Automatic portfolio updates
- [ ] Alert notifications
- [ ] Export/import portfolios

---

**For detailed implementation guides, see:**
- [QUICKSTART.md](QUICKSTART.md) - Getting started
- [README_V2.md](README_V2.md) - Full documentation
- [MIGRATION_V1_TO_V2.md](MIGRATION_V1_TO_V2.md) - Migration guide
