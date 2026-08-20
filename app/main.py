"""
Investment Analyst API - Production-grade FastAPI backend
Unified platform for portfolio management, document analysis, and stock market analysis
"""
import os
import time
import logging
from dotenv import load_dotenv
load_dotenv(override=True)
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.log_capture import SSELogHandler

# ── Logging setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
root_logger = logging.getLogger()
root_logger.addHandler(SSELogHandler())
logger = logging.getLogger("api")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request: method, path, status code, and elapsed time."""

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - start) * 1000
        logger.info(
            "%-6s %-45s %s  %.0fms",
            request.method,
            request.url.path,
            response.status_code,
            ms,
        )
        return response

from app.database.connection import init_db
from app.core.agents_init import AgentBundle
from app.api.portfolios import router as portfolio_router
from app.api.rag import router as rag_router
from app.api.integrations import router as integrations_router
from app.api.quant import router as quant_router
from app.api.chats import router as chats_router
from app.api.form4 import router as form4_router
from app.api.edgar import router as edgar_router
from app.api.auth import router as auth_router
from app.api.reports import router as reports_router
from app.api.analysis_tasks import router as analysis_tasks_router
import app.api.rag as rag_router_module
import app.api.quant as quant_router_module
import app.api.analysis_tasks as analysis_tasks_module
from app.services.stock_agent import cleanup_stock_agents
from app.jobs.queue import get_arq_pool, close_arq_pool
import asyncio
from ingestion.ingest_macro_data import run_ingestion
from pathlib import Path

async def macro_sync_loop():
    while True:
        await asyncio.sleep(86400) # Wait 24 hours
        try:
            logger.info("Running scheduled macro data sync...")
            await run_ingestion()
        except Exception as e:
            logger.error(f"Scheduled macro sync failed: {e}")

# Initialize FastAPI
app = FastAPI(
    title="Investment Analyst API",
    description="Unified AI-powered investment analysis platform with document Q&A, stock market analysis, portfolio management, and data integrations",
    version="2.1.0",
    redirect_slashes=False,
)

# CORS configuration — locked to explicit origins, required for
# allow_credentials=True to work at all (browsers reject "*" + credentials
# together, and refresh-token auth now relies on a credentialed cookie).
# Set CORS_ALLOWED_ORIGINS as a comma-separated list in production, e.g.
# "https://app.example.com,https://staging.example.com".
_default_origins = "http://localhost:5173,http://127.0.0.1:5173"
_cors_origins = [
    o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", _default_origins).split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)

# Global instances
agent_bundle: AgentBundle | None = None
agent = None
checkpointer = None
stock_supervisor = None


@app.on_event("startup")
async def startup_event():
    """Initialize database, graph, cache, and stock agents on startup"""
    global agent_bundle, agent, checkpointer, stock_supervisor

    logger.info("=" * 70)
    logger.info("Starting Investment Analyst API v2.1...")
    logger.info("=" * 70)

    logger.info("Initializing database...")
    init_db()

    agent_bundle = await AgentBundle().build()
    agent = agent_bundle.rag_agent
    checkpointer = agent_bundle.checkpointer
    stock_supervisor = agent_bundle.stock_supervisor

    rag_router_module.set_agent(agent)
    quant_router_module.set_stock_supervisor(stock_supervisor)
    quant_router_module.set_agents_status(agent_bundle.stock_agents_ready)
    if agent_bundle.stock_agents_ready and stock_supervisor:
        logger.info("Stock Analysis System ready!")

    logger.info("Connecting to job queue (Redis/Arq)...")
    arq_pool = await get_arq_pool()
    analysis_tasks_module.set_arq_pool(arq_pool)

    logger.info("Checking Macro Data Initialization...")
    macro_metadata = Path("data/macro/metadata.json")
    if not macro_metadata.exists():
        logger.info("Macro data missing. Scheduling background ingestion (non-blocking)...")
        asyncio.create_task(run_ingestion())
    else:
        logger.info("Macro data found.")
        
    logger.info("Starting background macro sync task...")
    asyncio.create_task(macro_sync_loop())

    logger.info("=" * 70)
    logger.info("Investment Analyst API v2.1 Ready!")
    logger.info("  Server : http://localhost:8000")
    logger.info("  Docs   : http://localhost:8000/docs")
    logger.info("  Routes : /portfolios  /ask  /quant/query  /chats  /integrations  /form4")
    logger.info("=" * 70)


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources on shutdown"""
    global agent_bundle

    logger.info("Shutting down...")
    await cleanup_stock_agents()
    await close_arq_pool()

    if agent_bundle:
        await agent_bundle.close()
        logger.info("Agent bundle cleaned up")

    logger.info("Shutdown complete")


# Include routers FIRST before defining other routes
app.include_router(auth_router)
app.include_router(reports_router)
app.include_router(portfolio_router)
app.include_router(rag_router)
app.include_router(integrations_router)
app.include_router(quant_router)
app.include_router(chats_router)
app.include_router(form4_router)
app.include_router(edgar_router)
app.include_router(analysis_tasks_router)


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
                "graph_initialized": agent is not None
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
