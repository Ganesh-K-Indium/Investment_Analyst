"""
Investment Analyst API: the FastAPI application.

Process layout (see README "Architecture"):
    API (this module)        HTTP routes, auth, server-sent events; hands long work to the workers
    Arq workers              app/worker.py: interactive (chats, quick runs) and batch (multi-ticker ALPHA) queues
    MCP servers              backend/quant/*_mcp: market-data tools the quant agents call
    Postgres, Redis, Qdrant  application data and checkpoints, job queues and live updates, vectors

Configuration comes from app/core/config.py (environment, .env, config.yaml); product settings from the Settings screens.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from app.core.config import APP_VERSION, get_settings, validate_on_startup
from app.core.logging_setup import configure_logging
from app.utils.log_capture import SSELogHandler

configure_logging()
logging.getLogger().addHandler(SSELogHandler())
logger = logging.getLogger("api")

from fastapi import FastAPI  # noqa: E402  (logging is configured before the app modules load)
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

import app.api.analysis_tasks as analysis_tasks_module  # noqa: E402
import app.api.quant as quant_router_module  # noqa: E402
import app.api.rag as rag_router_module  # noqa: E402
from app.api.analysis_tasks import router as analysis_tasks_router  # noqa: E402
from app.api.auth import router as auth_router  # noqa: E402
from app.api.chats import router as chats_router  # noqa: E402
from app.api.data_sources import router as data_sources_router  # noqa: E402
from app.api.documents import router as documents_router  # noqa: E402
from app.api.edgar import router as edgar_router  # noqa: E402
from app.api.form4 import router as form4_router  # noqa: E402
from app.api.integrations import router as integrations_router  # noqa: E402
from app.api.mcp_servers import router as mcp_servers_router  # noqa: E402
from app.api.notes import router as notes_router  # noqa: E402
from app.api.portfolios import router as portfolio_router  # noqa: E402
from app.api.providers import router as providers_router  # noqa: E402
from app.api.reading_engines import router as reading_engines_router  # noqa: E402
from app.api.reports import router as reports_router  # noqa: E402
from app.api.settings import router as settings_router  # noqa: E402
from app.api.user_documents import router as user_documents_router  # noqa: E402
from app.core.agents_init import AgentBundle  # noqa: E402
from app.core.concurrency import configure_default_executor  # noqa: E402
from app.core.middleware import RequestLoggingMiddleware  # noqa: E402
from app.core.paths import MACRO_DIR  # noqa: E402
from app.database.connection import init_db  # noqa: E402
from app.jobs.queue import close_arq_pool, get_arq_pool  # noqa: E402
from app.services.stock_agent import cleanup_stock_agents  # noqa: E402
from ingestion.ingest_macro_data import run_ingestion  # noqa: E402

ROUTERS = [
    auth_router, reports_router, portfolio_router, rag_router_module.router, integrations_router, quant_router_module.router,
    chats_router, form4_router, edgar_router, analysis_tasks_router, settings_router, user_documents_router, providers_router,
    mcp_servers_router, data_sources_router, reading_engines_router, documents_router, notes_router,
]

MACRO_SYNC_SECONDS = 24 * 3600


class _State:
    """Objects built at startup, kept for the health check and shutdown."""
    agent_bundle: AgentBundle | None = None
    background: list = []


state = _State()


async def _macro_sync_loop():
    while True:
        await asyncio.sleep(MACRO_SYNC_SECONDS)
        try:
            logger.info("Running scheduled macro data sync...")
            await run_ingestion()
        except Exception as e:  # noqa: BLE001 - a failed sync must not stop the loop
            logger.error("Scheduled macro sync failed: %s", e)


async def _startup() -> None:
    cfg = validate_on_startup()
    logger.info("Starting Investment Analyst API %s (APP_ENV=%s)", APP_VERSION, cfg.app_env)
    configure_default_executor()

    logger.info("Initializing database...")
    init_db()
    try:
        from app.database.connection import SessionLocal
        from app.services.user_docs import manager as user_docs_manager
        async with SessionLocal() as db:
            await user_docs_manager.recover_interrupted(db)
    except Exception:
        logger.exception("Could not check for interrupted document reads")

    bundle = await AgentBundle().build()
    state.agent_bundle = bundle
    rag_router_module.set_agent(bundle.rag_agent)
    quant_router_module.set_stock_supervisor(bundle.stock_supervisor)
    quant_router_module.set_agents_status(bundle.stock_agents_ready)
    if bundle.stock_agents_ready and bundle.stock_supervisor:
        logger.info("Stock Analysis System ready!")

    logger.info("Connecting to job queue (Redis/Arq)...")
    analysis_tasks_module.set_arq_pool(await get_arq_pool())

    if not (MACRO_DIR / "metadata.json").exists():
        logger.info("Macro data missing. Scheduling background ingestion (non-blocking)...")
        state.background.append(asyncio.create_task(run_ingestion()))
    state.background.append(asyncio.create_task(_macro_sync_loop()))
    logger.info("Investment Analyst API %s ready. Docs at /docs", APP_VERSION)


async def _shutdown() -> None:
    logger.info("Shutting down...")
    for task in state.background:
        task.cancel()
    await cleanup_stock_agents()
    await close_arq_pool()
    if state.agent_bundle:
        await state.agent_bundle.close()
    logger.info("Shutdown complete")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await _startup()
    try:
        yield
    finally:
        await _shutdown()


def create_app() -> FastAPI:
    cfg = get_settings()
    application = FastAPI(
        title="Investment Analyst API",
        description="AI research for investment teams: SEC filings and your documents, quant agents, traceable answers.",
        version=APP_VERSION,
        redirect_slashes=False,
        lifespan=lifespan,
    )
    # Explicit origins (required for allow_credentials: the refresh token is a credentialed cookie). The regex adds localhost
    # on any port for development; set CORS_ALLOWED_ORIGIN_REGEX empty in production.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_origin_regex=cfg.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(RequestLoggingMiddleware)
    for router in ROUTERS:
        application.include_router(router)
    application.add_api_route("/", root, methods=["GET"], tags=["Meta"])
    application.add_api_route("/health", health_check, methods=["GET"], tags=["Meta"])
    return application


async def root():
    """API name, version and where to look next."""
    return {
        "name": "Investment Analyst API",
        "version": APP_VERSION,
        "docs": "/docs",
        "health": "/health",
        "services": {
            "document_analysis": ["/ask", "/ask/stream", "/compare", "/alpha"],
            "stock_analysis": ["/quant/query", "/quant/query/stream", "/quant/health", "/quant/capabilities"],
            "background_runs": ["/analysis-tasks"],
            "portfolios": ["/portfolios"],
            "chat_history": ["/chats"],
            "your_documents": ["/user-documents", "/notes", "/documents"],
            "integrations": ["/integrations", "/data-sources", "/mcp-servers", "/providers"],
            "settings": ["/settings"],
        },
    }


async def _check(coro, timeout: float = 2.0) -> str:
    try:
        await asyncio.wait_for(coro, timeout)
        return "connected"
    except Exception as e:  # noqa: BLE001 - reported, not raised
        return f"unavailable: {type(e).__name__}"


async def _ping_db():
    from sqlalchemy import text
    from app.database.connection import engine
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _ping_redis():
    pool = analysis_tasks_module.get_arq_pool_if_ready()
    if pool is None:
        raise RuntimeError("not connected")
    await pool.ping()


async def health_check():
    """Liveness and dependencies: the database and Redis are actually contacted. `status` is "healthy" only when everything is up."""
    bundle = state.agent_bundle
    agent = bundle.rag_agent if bundle else None
    supervisor = bundle.stock_supervisor if bundle else None
    database, redis = await asyncio.gather(_check(_ping_db()), _check(_ping_redis()))
    healthy = database == "connected" and redis == "connected" and agent is not None
    return {
        "status": "healthy" if healthy else "degraded",
        "api": "Investment Analyst API",
        "version": APP_VERSION,
        "services": {
            "document_analysis": {"status": "operational" if agent is not None else "unavailable", "graph_initialized": agent is not None},
            "stock_analysis": {
                "status": "operational" if supervisor is not None else "unavailable",
                "supervisor_initialized": supervisor is not None,
                "details": "See /quant/health for detailed MCP server status",
            },
        },
        "infrastructure": {
            "database": database,
            "redis": redis,
            "checkpointer": "active" if bundle and bundle.checkpointer is not None else "unavailable",
        },
    }


app = create_app()
