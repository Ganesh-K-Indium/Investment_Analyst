"""
Investment Analyst API - Production-grade FastAPI backend
Unified platform for portfolio management, document analysis, and stock market analysis
"""
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from rag.graph.builder import BuildingGraph
from rag.graph.semantic_cache import SemanticCache
from app.database.connection import init_db
from app.api.portfolios import router as portfolio_router
from app.api.rag import router as rag_router
from app.api.integrations import router as integrations_router
from app.api.quant import router as quant_router
from app.api.chats import router as chats_router
import app.api.rag as rag_router_module
import app.api.quant as quant_router_module
from app.services.stock_agent import initialize_stock_agents, cleanup_stock_agents

# Initialize FastAPI
app = FastAPI(
    title="Investment Analyst API",
    description="Unified AI-powered investment analysis platform with document Q&A, stock market analysis, portfolio management, and data integrations",
    version="2.1.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure based on your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global instances
graph_obj = None
agent = None
semantic_cache = None
checkpointer_context = None
checkpointer = None
stock_supervisor = None


@app.on_event("startup")
async def startup_event():
    """Initialize database, graph, cache, and stock agents on startup"""
    global graph_obj, agent, semantic_cache, checkpointer_context, checkpointer, stock_supervisor
    
    print("\n" + "="*70)
    print("Starting Investment Analyst API v2.1...")
    print("="*70)
    
    # Initialize database
    print("Initializing database...")
    init_db()
    
    # Initialize LangGraph checkpointer (shared for RAG and stock agents)
    print("Initializing checkpointer...")
    checkpointer_context = AsyncSqliteSaver.from_conn_string("checkpoints.sqlite")
    checkpointer = await checkpointer_context.__aenter__()
    
    # Initialize RAG graph
    print("Building RAG graph...")
    graph_obj = BuildingGraph()
    agent = await graph_obj.get_graph(checkpointer=checkpointer)
    
    # Initialize semantic cache
    print("Initializing semantic cache...")
    semantic_cache = SemanticCache()
    
    # Set global instances in RAG router
    rag_router_module.set_agent(agent)
    rag_router_module.set_semantic_cache(semantic_cache)
    
    # Initialize Stock Analysis Agents (with separate checkpointer)
    print("\nInitializing Stock Analysis System...")
    try:
        # IMPORTANT: Pass None to force separate checkpointer creation
        stock_supervisor, agents_ready = await initialize_stock_agents(checkpointer=None)
        
        # Set global instances in quant router
        quant_router_module.set_stock_supervisor(stock_supervisor)
        quant_router_module.set_agents_status(agents_ready)
        
        if agents_ready and stock_supervisor:
            print("Stock Analysis System ready!")
        else:
            print("WARNING: Stock Analysis System not available")
            print("   Start MCP servers and restart to enable stock analysis")
    except Exception as e:
        print(f"WARNING: Failed to initialize Stock Analysis System: {e}")
        print("   The API will run without stock analysis capabilities")
        # Set to None so API knows it's unavailable
        quant_router_module.set_stock_supervisor(None)
        quant_router_module.set_agents_status(False)
    
    print("\n" + "="*70)
    print("Investment Analyst API v2.1 Ready!")
    print()
    print("Server URL: http://localhost:8000")
    print("API Docs: http://localhost:8000/docs")
    print()
    print("Available Services:")
    print("   - Portfolio Management: /portfolios")
    print("   - Document Analysis: /ask, /compare")
    print("   - Stock Market Analysis: /quant/query")
    print("   - Chat History: /chats")
    print("   - Data Integrations: /integrations")
    print()
    print("TIP: Open static/index.html in browser for web interface")
    print("="*70 + "\n")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources on shutdown"""
    global graph_obj, checkpointer_context
    
    print("Shutting down...")
    
    # Cleanup stock agents
    await cleanup_stock_agents()
    
    if checkpointer_context:
        await checkpointer_context.__aexit__(None, None, None)
        print("Checkpointer connection closed")
    
    if graph_obj:
        await graph_obj.cleanup()
        print("Graph cleaned up")
    
    print("Shutdown complete")


# Include routers FIRST before defining other routes
app.include_router(portfolio_router)
app.include_router(rag_router)
app.include_router(integrations_router)
app.include_router(quant_router)
app.include_router(chats_router)


@app.get("/")
async def root():
    """API root endpoint - returns API information"""
    return {
        "name": "Investment Analyst API",
        "version": "2.1.0",
        "description": "Unified AI-powered investment analysis platform",
        "services": {
            "document_analysis": {
                "description": "AI-powered document Q&A from financial reports, 10-Ks, earnings calls",
                "endpoints": ["/ask", "/compare"]
            },
            "stock_analysis": {
                "description": "Real-time stock market data, technical analysis, and research",
                "endpoints": ["/quant/query", "/quant/health", "/quant/capabilities"]
            },
            "portfolio_management": {
                "description": "Create and manage investment portfolios with session tracking",
                "endpoints": ["/portfolios", "/portfolios/sessions"]
            },
            "chat_history": {
                "description": "Manage chat history across all agents, export conversations, and clear history",
                "endpoints": ["/chats/user/{user_id}/sessions", "/chats/session/{session_id}", "/chats/session/{session_id}/export"]
            },
            "data_integrations": {
                "description": "Connect to external data sources (S3, SharePoint, Google Drive, etc.)",
                "endpoints": ["/integrations"]
            }
        },
        "quick_start": {
            "api_docs": "http://localhost:8000/docs",
            "health_check": "http://localhost:8000/health",
            "web_interface": "static/index.html"
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint for all services"""
    return {
        "status": "healthy",
        "api": "Investment Analyst API",
        "version": "2.1.0",
        "services": {
            "document_analysis": {
                "status": "operational" if agent is not None else "unavailable",
                "graph_initialized": agent is not None,
                "cache_initialized": semantic_cache is not None
            },
            "stock_analysis": {
                "status": "operational" if stock_supervisor is not None else "unavailable",
                "supervisor_initialized": stock_supervisor is not None,
                "details": "See /quant/health for detailed MCP server status"
            },
            "portfolio_management": {
                "status": "operational",
                "database": "connected"
            },
            "chat_history": {
                "status": "operational",
                "database": "connected",
                "supports_rag": True,
                "supports_quant": True
            },
            "data_integrations": {
                "status": "operational"
            }
        },
        "infrastructure": {
            "database": "connected",
            "checkpointer": "active" if checkpointer is not None else "unavailable"
        }
    }
