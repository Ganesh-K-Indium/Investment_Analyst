"""
Streaming chat (POST /ask/stream, POST /quant/query/stream) run on the interactive worker instead of inside the API process.

Why: the agent run is the heavy part of a chat (graph steps, model calls, retrieval). Running it in the single API process
means every user's chat competes for that one process. Handing it to the worker lets the API stay responsive and lets chat
capacity grow by adding worker processes.

How: the API subscribes to a per-request Redis channel, enqueues `run_chat_stream` on the interactive queue, then relays
each message on that channel to the browser as one SSE frame. The worker publishes exactly the event dicts the in-process
path produces ({"type":"step"}, {"type":"token"}, then {"type":"done","result"} or {"type":"error"}), serialised the same way,
so the browser sees the identical stream either way. If the browser disconnects, the job is aborted, like the in-process task
is cancelled.

When no interactive worker is running (e.g. a local dev run of the API alone), the routes keep running the chat in-process,
exactly as before. CHAT_STREAM_MODE = auto (default) | worker | inline overrides that choice (app/core/config.py).
"""
import asyncio
import json
import logging
import uuid
from typing import Optional

from arq import func
from arq.constants import abort_jobs_ss, health_check_key_suffix
from arq.jobs import Job, JobStatus
from arq.utils import timestamp_ms
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.jobs.queue import QUEUE_INTERACTIVE, get_pool_if_ready
from app.core.config import get_settings

logger = logging.getLogger("app.jobs.chat_stream")

# How long the relay waits without any event before sending a keepalive and checking the job is still alive.
_IDLE_SECONDS = 15
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

KIND_ASK = "ask"
KIND_QUANT = "quant"


def _channel(stream_id: str) -> str:
    return f"chat-stream:{stream_id}"


def _frame(data: str) -> str:
    return f"data: {data}\n\n"


# ── Worker side ──────────────────────────────────────────────────────────────

async def _run_chat_stream(ctx, stream_id: str, kind: str, payload_dict: dict, user_id: str):
    """Run one streamed chat and publish its events. Same error mapping as the in-process routes."""
    from app.database.connection import SessionLocal
    from app.jobs.rag_jobs import _load_user

    redis = ctx["redis"]
    channel = _channel(stream_id)

    async def publish(event: dict):
        await redis.publish(channel, json.dumps(event, default=str))

    try:
        async with SessionLocal() as db:
            user = await _load_user(db, user_id)
            if kind == KIND_ASK:
                import app.api.rag as rag_api
                result = await rag_api.ask_core(rag_api.AskInput(**payload_dict), db, user, emit=publish)
                await publish({"type": "done", "result": result})
            elif kind == KIND_QUANT:
                import app.api.quant as quant_api
                result = await quant_api.query_core(quant_api.StockQueryRequest(**payload_dict), None, db, user, emit=publish)
                await publish({"type": "done", "result": result.model_dump()})
            else:
                raise ValueError(f"Unknown chat stream kind {kind!r}")
    except HTTPException as e:
        await publish({"type": "error", "status": e.status_code, "detail": e.detail})
    except asyncio.CancelledError:
        raise                                   # aborted because the browser went away; nobody is listening
    except Exception as e:  # noqa: BLE001
        logger.exception("Streaming %s failed (stream %s)", kind, stream_id)
        await publish({"type": "error", "status": 500, "detail": str(e)})


# Registered on the workers: never retried (a retry would save the user's message twice), result not kept (it was streamed).
run_chat_stream = func(_run_chat_stream, name="run_chat_stream", timeout=get_settings().chat_stream_timeout_seconds, max_tries=1, keep_result=0)


# ── API side ─────────────────────────────────────────────────────────────────

async def _worker_pool():
    """The Redis pool to hand the chat to, or None to run it in-process."""
    mode = get_settings().chat_stream_mode
    if mode == "inline":
        return None
    pool = get_pool_if_ready()
    if pool is None:
        return None
    if mode == "worker":
        return pool
    try:
        # Workers refresh this key every few seconds (WorkerSettings.health_check_interval); it expires if none is running.
        alive = await pool.exists(QUEUE_INTERACTIVE + health_check_key_suffix)
    except Exception:  # noqa: BLE001
        logger.warning("Could not reach Redis to check for a worker; running the chat in-process", exc_info=True)
        return None
    return pool if alive else None


async def stream_via_worker(kind: str, payload_dict: dict, user_id) -> Optional[StreamingResponse]:
    """Start the chat on the interactive worker and return the SSE response relaying it, or None when it should run in-process."""
    pool = await _worker_pool()
    if pool is None:
        return None

    stream_id = uuid.uuid4().hex
    channel = _channel(stream_id)
    pubsub = pool.pubsub()
    await pubsub.subscribe(channel)             # subscribe before enqueueing, so no early event can be missed
    try:
        job = await pool.enqueue_job(
            "run_chat_stream", stream_id, kind, payload_dict, str(user_id),
            _queue_name=QUEUE_INTERACTIVE, _job_id=f"chat-stream:{stream_id}",
        )
    except Exception:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        raise
    if job is None:                             # only possible on a job-id collision, which a fresh uuid rules out
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        return None

    return StreamingResponse(_relay(pool, pubsub, channel, job), media_type="text/event-stream", headers=_SSE_HEADERS)


async def _relay(pool, pubsub, channel: str, job: Job):
    finished = False
    loop = asyncio.get_running_loop()
    last_activity = loop.time()
    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=_IDLE_SECONDS)
            if message is None and loop.time() - last_activity < _IDLE_SECONDS - 0.5:
                # Not a quiet spell: get_message also returns None after reading a filtered-out message (the subscribe
                # confirmation), so only a real idle period triggers the keepalive and the job check below.
                continue
            last_activity = loop.time()
            if message is None:
                status = await job.status()
                if status in (JobStatus.complete, JobStatus.not_found):
                    # The job is over; pick up a final event that may have landed just now before declaring it lost.
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if message is None:
                        logger.error("Chat stream %s ended without a result (worker stopped or timed out)", channel)
                        yield _frame(json.dumps({"type": "error", "status": 500,
                                                 "detail": "The analysis stopped unexpectedly. Please try again."}))
                        finished = True
                        return
                else:
                    yield ": keepalive\n\n"     # SSE comment: keeps proxies from closing a quiet stream; the client ignores it
                    continue
            data = message["data"]
            data = data.decode() if isinstance(data, bytes) else data
            yield _frame(data)
            try:
                kind = json.loads(data).get("type")
            except Exception:  # noqa: BLE001
                kind = None
            if kind in ("done", "error"):
                finished = True
                return
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
        except Exception:  # noqa: BLE001
            pass
        if not finished:
            # The browser went away mid-answer: stop the run, as the in-process path cancels its task.
            try:
                await pool.zadd(abort_jobs_ss, {job.job_id: timestamp_ms()})
            except Exception:  # noqa: BLE001
                logger.warning("Could not abort chat stream job %s", job.job_id, exc_info=True)
