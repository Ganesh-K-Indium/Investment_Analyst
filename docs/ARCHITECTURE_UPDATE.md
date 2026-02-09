# Architecture Update: Portfolio-Scoped Vector DB

## 🎯 New Architecture (Implemented)

### Problem with Old Approach:
- ❌ Vector DB initialized on every query
- ❌ Company names passed through state
- ❌ Filtering happened at query time
- ❌ Slower, more complex

### New Approach (Current):
- ✅ Vector DB initialized ONCE at portfolio activation
- ✅ DB instance cached per session
- ✅ Pre-filtered at DB level
- ✅ Faster, cleaner, more efficient

---

## 📊 Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    USER WORKFLOW                             │
└─────────────────────────────────────────────────────────────┘

1. CREATE PORTFOLIO
   └─> Store: Portfolio(id=1, companies=["google"])

2. ACTIVATE PORTFOLIO (Create Session)
   ├─> Create Session(thread_id="portfolio_1_abc123")
   ├─> 🔥 Initialize Vector DB
   │   ├─> Create load_vector_database instance
   │   ├─> Pre-filter for ["google"]
   │   └─> Cache: thread_id -> (db_instance, ["google"])
   └─> ✅ Ready for queries!

3. ASK QUESTION
   User: "What's the revenue?"  (NO company mentioned!)
   ├─> Get session thread_id
   ├─> Get cached DB instance (already filtered for Google)
   ├─> Pass to graph: {"vectordb_instance": db_instance}
   ├─> Retrieve node: Uses db_instance directly
   └─> ✅ Returns Google revenue (no other companies touched!)

4. ASK FOLLOW-UP
   User: "And the profit?"  (Still no company mentioned!)
   ├─> Same thread_id
   ├─> Same cached DB instance
   └─> ✅ Returns Google profit

5. COMPARE (Ad-hoc)
   User: Compare Tesla vs Ford
   ├─> 🔥 Create TEMPORARY Vector DB instance
   ├─> Filter for ["tesla", "ford"]
   ├─> Use temporary instance (does NOT affect portfolio DB)
   └─> ✅ Returns comparison

6. BACK TO ASK
   User: "Tell me more about products"
   ├─> Same thread_id
   ├─> Gets ORIGINAL portfolio DB instance (still ["google"])
   └─> ✅ Returns Google products (not Tesla/Ford!)
```

---

## 🔧 Implementation Details

### 1. VectorDBManager (New Service)
Location: `services/vectordb_manager.py`

```python
class VectorDBManager:
    """
    Manages portfolio-scoped Vector DB instances.
    One DB instance per active session.
    """
    
    def initialize_for_portfolio(thread_id, companies):
        # Called at session creation
        # Creates and caches DB instance
        
    def get_for_session(thread_id):
        # Called in ask endpoint
        # Returns cached instance
        
    def create_temporary(companies):
        # Called in compare endpoint
        # Creates temporary instance
```

### 2. Session Creation (Portfolio Activation)
Location: `routers/portfolio_router.py`

```python
@router.post("/portfolios/sessions")
def create_session(...):
    # Create session in database
    session = PortfolioService.create_session(...)
    
    # 🔥 Initialize Vector DB for this portfolio
    vectordb_mgr.initialize_for_portfolio(
        thread_id=session.id,
        company_names=portfolio.company_names
    )
    
    # Now ready for queries!
```

### 3. Ask Endpoint
Location: `routers/rag_router.py`

```python
@router.post("/ask")
async def ask_agent(...):
    # Get session
    session = get_session(thread_id)
    
    # 🔥 Get pre-initialized DB instance
    db_instance, companies = vectordb_mgr.get_for_session(thread_id)
    
    # Pass to graph
    inputs = {
        "vectordb_instance": db_instance,  # Pre-filtered!
        "company_filter": companies,  # For display only
        ...
    }
```

### 4. Compare Endpoint
Location: `routers/rag_router.py`

```python
@router.post("/compare")
async def compare_companies(...):
    companies = [company1, company2, company3]
    
    # 🔥 Create TEMPORARY instance
    db_instance, filter = vectordb_mgr.create_temporary(companies)
    
    # Pass to graph (does NOT affect portfolio DBs)
    inputs = {
        "vectordb_instance": db_instance,  # Temporary!
        ...
    }
```

---

## 🎯 Key Benefits

### 1. Performance
- ✅ DB initialized once, not per query
- ✅ No repeated connection overhead
- ✅ Faster query execution

### 2. Simplicity
- ✅ No company names in graph state
- ✅ DB already knows what to filter
- ✅ Cleaner code

### 3. Correctness
- ✅ User doesn't mention company in query
- ✅ System already knows from portfolio
- ✅ No parsing needed

### 4. Isolation
- ✅ Compare doesn't affect portfolio DB
- ✅ Each session independent
- ✅ Clean state management

---

## 🔄 State Management

### Portfolio DB Instances
```python
# Cached in VectorDBManager
{
    "portfolio_1_abc123": (db_instance_1, ["google"]),
    "portfolio_2_xyz789": (db_instance_2, ["apple", "microsoft"]),
    ...
}
```

### When User Switches:
```
Portfolio A → Ask → Uses db_instance_1 (Google)
Compare → Uses temporary instance (Tesla, Ford)
Portfolio A → Ask → Back to db_instance_1 (Google)
```

---

## 📝 Graph State Changes

### Before (Old):
```python
state = {
    "company_name": ["google"],  # Passed through state
    ...
}
```

### After (New):
```python
state = {
    "vectordb_instance": db_instance,  # Pre-filtered instance
    "company_filter": ["google"],  # For display/logging only
    ...
}
```

---

## 🛠️ Retrieve Node Update Needed

Location: `Graph/nodes.py`

**Current:**
```python
def retrieve(state):
    # Gets company from state
    company = state.get("company_name")
    
    # Initializes DB every time
    db = load_vector_database()
    
    # Queries with filter
    results = db.hybrid_search(query, company=company)
```

**Should be:**
```python
def retrieve(state):
    # Gets pre-initialized DB instance
    db_instance = state.get("vectordb_instance")
    company_filter = state.get("company_filter")
    
    # DB is ALREADY filtered for these companies!
    # Just query directly
    results = db_instance.hybrid_search(
        query=query,
        company=company_filter  # Already scoped
    )
```

---

## ✅ What's Done

1. ✅ Created `VectorDBManager` service
2. ✅ Modified portfolio router to initialize DB at session creation
3. ✅ Modified ask endpoint to use cached DB instance
4. ✅ Modified compare endpoint to use temporary instance
5. ⏳ Need to update `Graph/nodes.py` retrieve function

---

## 🎯 Next Steps

### 1. Update Retrieve Node
Modify `Graph/nodes.py` to use `vectordb_instance` from state instead of initializing new DB.

### 2. Test Flow
```bash
# 1. Create portfolio with Google
# 2. Activate (session creation)
# 3. Ask: "What's the revenue?" (should work!)
# 4. Compare: Tesla vs Ford (temporary instance)
# 5. Ask: "Tell me more" (back to Google instance)
```

### 3. Verify Isolation
- Portfolio DB unchanged after compare
- Each session independent
- Cleanup when session expires

---

## 🚀 Expected Results

### Before Fix:
- ❌ Timeout (Qdrant connection per query)
- ❌ Slow (repeated initialization)
- ❌ Complex (company names through state)

### After Fix:
- ✅ Fast (cached DB instance)
- ✅ Simple (pre-filtered at source)
- ✅ Reliable (one connection at activation)

---

**Status:** Backend structure ready, need to update retrieve node  
**Impact:** Massive performance improvement + cleaner architecture  
**Risk:** Low (isolated changes, backward compatible)
