"""
Process-wide concurrency settings shared by the API and the worker processes.

Many agent steps are plain (sync) functions that call a model and wait on the network. LangGraph, LangChain and
asyncio.to_thread run those on the event loop's *default* thread pool, which Python sizes to min(32, CPUs + 4), i.e. 8 threads
on a 4-vCPU machine. With more concurrent runs than that, every further step queues for a free thread, even though the
threads would only be waiting on I/O. A larger pool lets I/O-bound steps from many users proceed side by side.
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from app.core.config import get_settings

logger = logging.getLogger("app.core.concurrency")

def configure_default_executor() -> int:
    """Install a larger default thread pool on the running event loop (call once at startup). Returns its size."""
    workers = get_settings().threadpool_workers
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=workers, thread_name_prefix="io"))
    logger.info("Default thread pool: %d workers", workers)
    return workers
