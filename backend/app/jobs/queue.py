"""
Arq/Redis connection helpers shared by the API process (enqueueing) and the
worker processes (consuming). Two logical queues share one Redis instance:

- "interactive": chat-style single-turn asks — small reserved worker pool so
  these are never stuck behind long-running batch jobs.
- "batch": multi-ticker /alpha runs, deep quant dives, anything a user would
  kick off and walk away from. Larger pool, no latency guarantee.
"""
import os
import logging
from arq import create_pool
from arq.connections import RedisSettings, ArqRedis

logger = logging.getLogger("app.jobs.queue")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

QUEUE_INTERACTIVE = "interactive"
QUEUE_BATCH = "batch"

_pool: ArqRedis | None = None


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(REDIS_URL)


async def get_arq_pool() -> ArqRedis:
    """Create (once) the Redis connection pool the API process uses to enqueue jobs."""
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
        logger.info("Arq pool connected: %s", REDIS_URL)
    return _pool


async def close_arq_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
