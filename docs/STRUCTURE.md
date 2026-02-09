# 📁 Project Structure - Simplified & Organized

## 🎯 Design Philosophy

1. **Application Layer** (`app/`) - All app-level code (API, services, database)
2. **AI Services Layer** (`rag/`, future: `nlp/`, `vision/`, etc.) - Modular AI services
3. **Supporting Files** - Schemas, static files, tests, docs

---

## 📂 Complete Structure

```
Agentic-RAG/
│
├── 📱 app/                          # APPLICATION LAYER
│   ├── main.py                     # FastAPI application entry point
│   ├── cloudinary.py               # Image upload utility
│   ├── logger.py                   # Logging configuration
│   │
│   ├── api/                        # 🌐 API Endpoints
│   │   ├── __init__.py
│   │   ├── portfolios.py           # Portfolio CRUD endpoints
│   │   └── rag.py                  # RAG endpoints (ask, compare)
│   │
│   ├── services/                   # 💼 Business Logic
│   │   ├── __init__.py
│   │   ├── portfolio.py            # Portfolio service layer
│   │   └── vectordb_manager.py     # Vector DB instance manager
│   │
│   └── database/                   # 🗄️ Data Access Layer
│       ├── __init__.py
│       ├── connection.py           # SQLite connection & setup
│       └── models.py               # SQLAlchemy ORM models
│
├── 🤖 rag/                          # RAG AI SERVICE
│   ├── __init__.py
│   │
│   ├── graph/                      # LangGraph Workflow
│   │   ├── __init__.py
│   │   ├── state.py                # Graph state definition
│   │   ├── nodes.py                # Graph nodes (retrieve, generate, etc.)
│   │   ├── edges.py                # Routing logic
│   │   ├── builder.py              # Graph builder
│   │   ├── semantic_cache.py       # Semantic caching
│   │   └── benchmark.py            # Performance benchmarking
│   │
│   └── vectordb/                   # Vector Database
│       ├── __init__.py
│       ├── client.py               # Qdrant client & hybrid search
│       └── chains.py               # LLM chains & prompts
│
├── 📋 schemas/                      # PYDANTIC MODELS
│   ├── __init__.py
│   └── models.py                   # API request/response schemas
│
├── 🎨 static/                       # FRONTEND
│   └── index.html                  # Web UI for testing
│
├── 🧪 tests/                        # TESTS
│   ├── __init__.py
│   └── test_api.py                 # API integration tests
│
├── 📚 docs/                         # DOCUMENTATION
│   ├── ARCHITECTURE.md             # System architecture
│   ├── IMPLEMENTATION_COMPLETE.md  # Implementation details
│   ├── RAG_ARCHITECTURE_FINAL.md   # RAG architecture
│   └── ...
│
├── 🛠️ scripts/                     # UTILITY SCRIPTS
│   ├── start_server.sh             # Server startup script
│   └── generate_diagram.py         # Diagram generator
│
├── 📤 output/                       # GENERATED OUTPUTS
│   ├── responses/                  # Text responses
│   ├── json/                       # JSON responses
│   └── images/                     # Generated images
│
├── 🗄️ legacy/                      # ARCHIVED FILES
│   ├── app.py                      # Old v1 app
│   └── old_docs/                   # Old documentation
│
├── 📄 CONFIG & DEPENDENCIES
├── .env                            # Environment variables (not in git)
├── .env.example                    # Example environment file
├── .gitignore                      # Git ignore rules
├── requirements.txt                # Python dependencies
├── Dockerfile                      # Docker configuration
├── README.md                       # Main documentation
└── STRUCTURE.md                    # This file
```

---

## 🎨 Visual Hierarchy

```
┌─────────────────────────────────────────────────────────────┐
│                      AGENTIC RAG API                         │
└─────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         │                    │                    │
         ▼                    ▼                    ▼
    ┌────────┐          ┌─────────┐         ┌──────────┐
    │  APP   │          │   RAG   │         │ SUPPORT  │
    │ LAYER  │          │ SERVICE │         │  FILES   │
    └────────┘          └─────────┘         └──────────┘
         │                    │                    │
    ┌────┼─────┐         ┌────┼─────┐        ┌────┼─────┐
    │    │     │         │    │     │        │    │     │
    ▼    ▼     ▼         ▼    ▼     ▼        ▼    ▼     ▼
   API Service DB     Graph Vector  Schemas Static Tests
                              DB
```

---

## 🔄 Import Patterns

### From Application Layer:
```python
# FastAPI app
from app.main import app

# API endpoints
from app.api.portfolios import router as portfolio_router
from app.api.rag import router as rag_router

# Services
from app.services.portfolio import PortfolioService
from app.services.vectordb_manager import VectorDBManager

# Database
from app.database.connection import init_db, get_db_session
from app.database.models import Portfolio, Session
```

### From RAG Service:
```python
# Graph
from rag.graph.builder import BuildingGraph
from rag.graph.state import GraphState

# Vector DB
from rag.vectordb.client import load_vector_database
from rag.vectordb.chains import get_rag_chain
```

---

## 🎯 Why This Structure?

### 1. **Clear Separation**
```
app/     → Application logic (API, business, data)
rag/     → AI service (graph, vectordb)
```

### 2. **Easy to Navigate**
- Need API endpoints? → `app/api/`
- Need business logic? → `app/services/`
- Need RAG logic? → `rag/graph/`
- Need vector DB? → `rag/vectordb/`

### 3. **Scalable**
```
Current:
  ├── app/    (Application)
  └── rag/    (RAG AI Service)

Future:
  ├── app/    (Application)
  ├── rag/    (RAG AI Service)
  ├── nlp/    (NLP AI Service) ← Easy to add!
  ├── vision/ (Vision AI Service) ← Easy to add!
  └── speech/ (Speech AI Service) ← Easy to add!
```

### 4. **Professional**
- Follows industry best practices
- Clear module boundaries
- Easy for new developers
- Maintainable and testable

---

## 📊 File Count by Category

| Category | Count | Purpose |
|----------|-------|---------|
| **API Endpoints** | 2 | Portfolio & RAG endpoints |
| **Services** | 2 | Business logic layer |
| **Database** | 2 | Data access & models |
| **RAG Graph** | 6 | LangGraph workflow |
| **RAG VectorDB** | 2 | Vector database client |
| **Tests** | 1+ | API & integration tests |
| **Docs** | 5+ | Architecture & guides |
| **Config** | 3 | Environment & dependencies |

**Total Core Files**: ~25 (clean and manageable!)

---

## 🚀 How to Add New AI Services

### Example: Adding NLP Service

```bash
# 1. Create structure
mkdir -p nlp/sentiment nlp/entities

# 2. Create files
touch nlp/__init__.py
touch nlp/sentiment/analyzer.py
touch nlp/entities/extractor.py

# 3. Add API endpoint
touch app/api/nlp.py

# 4. Add service
touch app/services/nlp.py

# 5. Import in main.py
# from app.api.nlp import router as nlp_router
# app.include_router(nlp_router)
```

### Result:
```
Agentic-RAG/
├── app/
│   ├── api/
│   │   ├── portfolios.py
│   │   ├── rag.py
│   │   └── nlp.py          ← New!
│   └── services/
│       ├── portfolio.py
│       ├── vectordb_manager.py
│       └── nlp.py          ← New!
├── rag/                    ← RAG service
└── nlp/                    ← NLP service (new!)
    ├── sentiment/
    └── entities/
```

---

## 🎓 Best Practices

### 1. **Keep Application Logic in `app/`**
   - API endpoints
   - Business logic
   - Database access

### 2. **Keep AI Logic in Service Folders** (`rag/`, `nlp/`, etc.)
   - Model inference
   - AI workflows
   - Specialized utilities

### 3. **Share Common Code via `schemas/`**
   - Pydantic models
   - Data structures
   - Validators

### 4. **Document in `docs/`**
   - Architecture decisions
   - API documentation
   - Implementation guides

---

## 📝 Summary

| Aspect | Solution |
|--------|----------|
| **Structure** | Clean, hierarchical, modular |
| **Navigation** | Easy - clear folder purposes |
| **Scalability** | Simple to add new AI services |
| **Maintenance** | Clear boundaries, easy to update |
| **Onboarding** | New developers understand quickly |
| **Production** | Ready for deployment |

---

**✅ Project is now properly organized and ready for development!**

*Simple. Clean. Professional.* 🚀
