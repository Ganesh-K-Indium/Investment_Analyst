"""
Arq worker process(es) for background analysis runs.

Run two separate worker processes against this same module, one per queue,
so a long batch job (e.g. a multi-ticker /alpha run) can never starve an
interactive chat job:

    arq app.worker.InteractiveWorkerSettings
    arq app.worker.BatchWorkerSettings

Both share the same startup/shutdown (building the RAG graph + quant
supervisor once per worker process, via the same AgentBundle the API process
uses) and the same job functions — only queue_name and max_jobs differ.
"""
import logging

from dotenv import load_dotenv
load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("app.worker")

from app.database.connection import init_db
from app.core.agents_init import AgentBundle
from app.jobs.queue import redis_settings, QUEUE_INTERACTIVE, QUEUE_BATCH
from app.jobs.rag_jobs import run_rag_ask, run_rag_compare, run_rag_alpha
from app.jobs.quant_jobs import run_quant_query
import app.api.rag as rag_router_module
import app.api.quant as quant_router_module


async def startup(ctx):
    logger.info("Worker starting up — initializing database + agents...")
    init_db()
    bundle = await AgentBundle().build()
    rag_router_module.set_agent(bundle.rag_agent)
    quant_router_module.set_stock_supervisor(bundle.stock_supervisor)
    quant_router_module.set_agents_status(bundle.stock_agents_ready)
    ctx["agent_bundle"] = bundle
    logger.info("Worker ready.")


async def shutdown(ctx):
    bundle: AgentBundle = ctx.get("agent_bundle")
    if bundle:
        await bundle.close()
    logger.info("Worker shut down.")


_FUNCTIONS = [run_rag_ask, run_rag_compare, run_rag_alpha, run_quant_query]


class InteractiveWorkerSettings:
    functions = _FUNCTIONS
    queue_name = QUEUE_INTERACTIVE
    redis_settings = redis_settings()
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 20  # reserved concurrency — never queued behind batch work


class BatchWorkerSettings:
    functions = _FUNCTIONS
    queue_name = QUEUE_BATCH
    redis_settings = redis_settings()
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 5
