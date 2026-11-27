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
import os

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
from app.jobs.chat_stream import run_chat_stream
from app.core.concurrency import configure_default_executor
import app.api.rag as rag_router_module
import app.api.quant as quant_router_module


async def startup(ctx):
    logger.info("Worker starting up — initializing database + agents...")
    configure_default_executor()
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


_FUNCTIONS = [run_rag_ask, run_rag_compare, run_rag_alpha, run_quant_query, run_chat_stream]


class InteractiveWorkerSettings:
    functions = _FUNCTIONS
    queue_name = QUEUE_INTERACTIVE
    redis_settings = redis_settings()
    on_startup = startup
    on_shutdown = shutdown
    # Concurrent jobs per worker process — includes streamed chats (app/jobs/chat_stream.py). Run more worker processes
    # (docker compose up --scale worker-interactive=N) to go beyond what one process handles comfortably.
    max_jobs = int(os.getenv("WORKER_INTERACTIVE_MAX_JOBS", "30"))  # reserved concurrency — never queued behind batch work
    job_timeout = int(os.getenv("WORKER_INTERACTIVE_JOB_TIMEOUT", "1800"))  # arq's default of 5 min cuts off long answers
    # The API checks this key to decide whether to hand streamed chats to a worker; refreshed every few seconds so it
    # disappears quickly when no worker is running (the API then runs chats in-process).
    health_check_interval = 5
    allow_abort_jobs = True  # a streamed chat is stopped when its browser disconnects


class BatchWorkerSettings:
    functions = _FUNCTIONS
    queue_name = QUEUE_BATCH
    redis_settings = redis_settings()
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = int(os.getenv("WORKER_BATCH_MAX_JOBS", "5"))
    job_timeout = int(os.getenv("WORKER_BATCH_JOB_TIMEOUT", "7200"))  # multi-ticker alpha runs can take well over 5 min
    health_check_interval = 30
