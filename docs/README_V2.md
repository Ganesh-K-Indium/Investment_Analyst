# Agentic RAG API v2.0 - Production Architecture

A production-grade FastAPI backend with RAG (Retrieval-Augmented Generation), portfolio management, and persistent state management.

## 🏗️ Architecture Overview

### Core Components

1. **Portfolio Management System**
   - SQLite-based persistence for portfolio and session data
   - User-specific company collections
   - Session tracking for conversation continuity

2. **RAG System**
   - Context-aware retrieval with portfolio filtering
   - Hybrid search (dense + sparse/BM25 embeddings)
   - Semantic caching for performance optimization

3. **State Management**
   - Separate contexts for `/ask` (portfolio-based) and `/compare` (ad-hoc)
   - Session-based state isolation
   - Automatic state restoration on reconnection

## 📁 Project Structure

```
Agentic-RAG/
├── app_v2.py                    # Main FastAPI application
├── database/
│   ├── models.py                # SQLAlchemy models (Portfolio, Session)
│   └── connection.py            # Database connection and session management
├── services/
│   └── portfolio_service.py     # Business logic for portfolio operations
├── routers/
│   ├── portfolio_router.py      # Portfolio CRUD endpoints
│   └── rag_router.py            # RAG endpoints (ask, compare)
├── Graph/                       # LangGraph workflow components
├── load_vector_dbs/            # Vector database loaders
└── portfolios.db               # SQLite database (created on first run)
```

## 🚀 Getting Started

### 1. Installation

```bash
# Install dependencies
pip install -r requirements_v2.txt

# Set up environment variables
cp .env.example .env
# Edit .env with your API keys (OpenAI, Qdrant, etc.)
```

### 2. Run the Application

```bash
# Development mode
uvicorn app_v2:app --reload --port 8000

# Production mode
uvicorn app_v2:app --host 0.0.0.0 --port 8000 --workers 4
```

### 3. Access API Documentation

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## 📚 API Endpoints

### Portfolio Management

#### Create Portfolio
```http
POST /portfolios/
Content-Type: application/json

{
  "user_id": "user123",
  "name": "Tech Portfolio",
  "company_names": ["apple", "microsoft", "google"],
  "description": "My tech stock portfolio"
}
```

#### Get User Portfolios
```http
GET /portfolios/user/{user_id}
```

#### Create Session
```http
POST /portfolios/sessions
Content-Type: application/json

{
  "portfolio_id": 1,
  "user_id": "user123"
}

Response:
{
  "thread_id": "portfolio_1_abc123...",
  "portfolio_id": 1,
  "portfolio_name": "Tech Portfolio",
  "company_names": ["apple", "microsoft", "google"]
}
```

### RAG Operations

#### Ask (Portfolio-based)
```http
POST /ask
Content-Type: application/json

{
  "query": "What was Apple's revenue in 2024?",
  "thread_id": "portfolio_1_abc123..."
}
```

**Key Behavior**:
- Uses portfolio companies as filters
- Maintains conversation context per session
- Automatically retrieves only portfolio-specific data

#### Compare (Ad-hoc)
```http
POST /compare
Content-Type: application/json

{
  "company1": "Apple",
  "company2": "Microsoft",
  "company3": "Google"  // optional
}
```

**Key Behavior**:
- Uses specified companies only (ignores portfolio)
- Independent filtering context
- Generates comparison charts and tables

## 🔄 Workflow Examples

### Example 1: Portfolio-based RAG Session

```python
import requests

# 1. Create portfolio
portfolio_response = requests.post("http://localhost:8000/portfolios/", json={
    "user_id": "alice",
    "name": "Pharma Watch",
    "company_names": ["pfizer", "moderna", "johnson & johnson"]
})
portfolio_id = portfolio_response.json()["id"]

# 2. Create session
session_response = requests.post("http://localhost:8000/portfolios/sessions", json={
    "portfolio_id": portfolio_id,
    "user_id": "alice"
})
thread_id = session_response.json()["thread_id"]

# 3. Ask questions (uses portfolio companies automatically)
ask_response = requests.post("http://localhost:8000/ask", json={
    "query": "Compare R&D spending across my portfolio",
    "thread_id": thread_id
})
print(ask_response.json()["answer"])

# 4. Continue conversation (context preserved)
followup_response = requests.post("http://localhost:8000/ask", json={
    "query": "Which company has the highest revenue growth?",
    "thread_id": thread_id  # Same thread_id
})
```

### Example 2: Ad-hoc Comparison (No Portfolio)

```python
# Compare specific companies without portfolio
compare_response = requests.post("http://localhost:8000/compare", json={
    "company1": "Tesla",
    "company2": "Ford",
    "company3": "GM"
})
print(compare_response.json()["answer"])
print(compare_response.json()["chart_url"])
```

### Example 3: Switch Between Ask and Compare

```python
# User starts with portfolio session
ask_response = requests.post("http://localhost:8000/ask", json={
    "query": "What are Pfizer's key products?",
    "thread_id": "portfolio_1_xyz"  # Uses Pfizer + Moderna + J&J (portfolio)
})

# User switches to ad-hoc comparison
compare_response = requests.post("http://localhost:8000/compare", json={
    "company1": "AstraZeneca",
    "company2": "Novartis"
    # No thread_id - independent context
})

# User returns to portfolio session
ask_response2 = requests.post("http://localhost:8000/ask", json={
    "query": "How does Moderna's pipeline compare to Pfizer?",
    "thread_id": "portfolio_1_xyz"  # Back to portfolio context
})
```

## 🔑 Key Features

### 1. State Isolation
- **Ask endpoint**: Always uses portfolio-defined companies
- **Compare endpoint**: Uses user-specified companies
- No interference between contexts

### 2. Session Persistence
- Sessions stored in SQLite
- Users can resume conversations from any device
- Last accessed timestamp for session cleanup

### 3. Conversation Memory
- LangGraph checkpointer maintains conversation history
- Context-aware responses based on previous interactions
- Automatic clarification requests (HITL)

### 4. Performance Optimization
- Semantic caching for repeated queries
- Hybrid search (dense + BM25) for better retrieval
- Async operations throughout

## 🛠️ Database Schema

### Portfolio Table
```sql
CREATE TABLE portfolios (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    company_names JSON NOT NULL,  -- ["company1", "company2", ...]
    description TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

### Session Table
```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,  -- thread_id
    portfolio_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    created_at TIMESTAMP,
    last_accessed TIMESTAMP,
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id)
);
```

## 🔒 Production Considerations

### Security
- Add authentication middleware (JWT, OAuth)
- Implement rate limiting
- Validate user ownership of portfolios
- Sanitize user inputs

### Scaling
- Use PostgreSQL instead of SQLite for production
- Implement connection pooling
- Add Redis for caching
- Deploy with Kubernetes for horizontal scaling

### Monitoring
- Add logging middleware
- Implement health checks for dependencies
- Track query performance metrics
- Set up alerting for errors

## 📝 Environment Variables

```bash
# Required
OPENAI_API_KEY=your_key_here
QDRANT_URL=your_qdrant_url
QDRANT_API_KEY=your_qdrant_key

# Optional
DATABASE_URL=sqlite:///./portfolios.db  # or postgresql://...
GROQ_API_KEY=your_groq_key
TAVILY_API_KEY=your_tavily_key
```

## 🧪 Testing

```bash
# Run tests
pytest tests/

# Test specific endpoint
pytest tests/test_portfolio.py -v

# Load testing
locust -f tests/load_test.py
```

## 📊 Migration from v1 to v2

### Changes
1. `/ask` now requires `thread_id` (no standalone queries)
2. Add portfolio creation step before asking questions
3. `company_name` parameter removed from `/ask` (uses portfolio)
4. New `/portfolios/*` endpoints for portfolio management

### Migration Script
```python
# Convert existing queries to portfolio-based approach
# See: scripts/migrate_v1_to_v2.py
```

## 🤝 Contributing

1. Follow the existing code structure
2. Add tests for new features
3. Update documentation
4. Use type hints throughout
5. Follow PEP 8 style guidelines

## 📄 License

MIT License - See LICENSE file for details
