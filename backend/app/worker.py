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

from app.core.config import get_settings, validate_on_startup
from app.core.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger("app.worker")

from app.database.connection import init_db  # noqa: E402  (after logging is configured)
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
    validate_on_startup()
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
    max_jobs = get_settings().worker_interactive_max_jobs  # reserved concurrency — never queued behind batch work
    job_timeout = get_settings().worker_interactive_job_timeout  # arq's default of 5 min cuts off long answers
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
    max_jobs = get_settings().worker_batch_max_jobs
    job_timeout = get_settings().worker_batch_job_timeout  # multi-ticker alpha runs can take well over 5 min
    health_check_interval = 30


WORKERS = {"interactive": InteractiveWorkerSettings, "batch": BatchWorkerSettings}

if __name__ == "__main__":
    # Same on every OS (no shell needed):  python -m app.worker interactive   |   python -m app.worker batch
    import sys
    from arq.worker import run_worker

    name = sys.argv[1] if len(sys.argv) > 1 else "interactive"
    if name not in WORKERS:
        raise SystemExit(f"usage: python -m app.worker [{' | '.join(WORKERS)}]")

    # Fail fast with a plain message instead of arq's five retries and a traceback when Redis is not reachable.
    import redis
    url = get_settings().redis_url
    try:
        redis.Redis.from_url(url, socket_connect_timeout=2).ping()
    except redis.exceptions.RedisError as e:
        raise SystemExit(f"Cannot reach Redis at {url} ({e.__class__.__name__}). Start it with "
                         f"`python scripts/dev.py services up` (or `docker compose up -d redis`), or fix REDIS_URL in .env.")
    run_worker(WORKERS[name])
