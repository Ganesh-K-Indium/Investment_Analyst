"""
Background analysis-run endpoints: enqueue an agent invocation, list/inspect
runs for a portfolio, and stream live status updates over SSE.

Every run — a quick chat ask or a multi-ticker overnight alpha job — creates
one AnalysisTask row and goes through the same Arq queue infrastructure.
The *queue* differs:
  - "interactive": /rag/ask, /rag/compare, /quant/query — reserved worker
    concurrency so these never wait behind batch work.
  - "batch": /rag/alpha — a separate worker pool.

All four enqueue endpoints accept an optional `scheduled_at` to defer
execution (e.g. "run overnight") — Arq's `_defer_until` handles this
regardless of which queue the job lands in.
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_db_session
from app.database.models import AgentType, TaskStatus, User
from app.auth.deps import get_current_user, verify_user_id_matches
from app.services.analysis_tasks import AnalysisTaskService, channel_for_portfolio
from app.services.portfolio import PortfolioService
from app.api.rag import AskInput, CompareInput, AlphaInput
from app.api.quant import StockQueryRequest
from app.jobs.queue import QUEUE_INTERACTIVE, QUEUE_BATCH

logger = logging.getLogger("api.analysis_tasks")
router = APIRouter(prefix="/analysis-tasks", tags=["Analysis Tasks"])

_arq_pool = None


def set_arq_pool(pool):
    global _arq_pool
    _arq_pool = pool


def _require_pool():
    if _arq_pool is None:
        raise HTTPException(status_code=503, detail="Job queue not connected")
    return _arq_pool


async def _enqueue(
    *,
    function: str,
    payload_dict: dict,
    user: User,
    agent_type: AgentType,
    task_type: str,
    portfolio_id: Optional[int],
    queue: str,
    db: AsyncSession,
    scheduled_at: Optional[datetime] = None,
) -> dict:
    pool = _require_pool()
    task = await AnalysisTaskService.create(
        db, user_id=str(user.id), agent_type=agent_type, task_type=task_type,
        portfolio_id=portfolio_id, queue=queue,
    )
    await pool.enqueue_job(
        function, task.id, payload_dict,
        _queue_name=queue,
        _job_id=task.id,
        _defer_until=scheduled_at,
    )
    return AnalysisTaskService.to_dict(task)


# ── Enqueue endpoints ────────────────────────────────────────────────────────

class AskEnqueueInput(AskInput):
    scheduled_at: Optional[datetime] = None


@router.post("/rag/ask")
async def enqueue_rag_ask(
    payload: AskEnqueueInput,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    session = await PortfolioService.get_session(db, payload.thread_id)
    portfolio_id = session.portfolio.id if session else None
    scheduled_at = payload.scheduled_at
    payload_dict = payload.model_dump(exclude={"scheduled_at"})
    return await _enqueue(
        function="run_rag_ask", payload_dict=payload_dict, user=current_user,
        agent_type=AgentType.RAG, task_type="ask", portfolio_id=portfolio_id,
        queue=QUEUE_INTERACTIVE, db=db, scheduled_at=scheduled_at,
    )


class CompareEnqueueInput(CompareInput):
    scheduled_at: Optional[datetime] = None


@router.post("/rag/compare")
async def enqueue_rag_compare(
    payload: CompareEnqueueInput,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    verify_user_id_matches(payload.user_id, current_user)
    scheduled_at = payload.scheduled_at
    payload_dict = payload.model_dump(exclude={"scheduled_at"})
    return await _enqueue(
        function="run_rag_compare", payload_dict=payload_dict, user=current_user,
        agent_type=AgentType.RAG, task_type="compare", portfolio_id=None,
        queue=QUEUE_INTERACTIVE, db=db, scheduled_at=scheduled_at,
    )


class AlphaEnqueueInput(AlphaInput):
    scheduled_at: Optional[datetime] = None


@router.post("/rag/alpha")
async def enqueue_rag_alpha(
    payload: AlphaEnqueueInput,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    verify_user_id_matches(payload.user_id, current_user)
    session = await PortfolioService.get_session(db, payload.thread_id)
    portfolio_id = session.portfolio.id if session else None
    scheduled_at = payload.scheduled_at
    payload_dict = payload.model_dump(exclude={"scheduled_at"})
    return await _enqueue(
        function="run_rag_alpha", payload_dict=payload_dict, user=current_user,
        agent_type=AgentType.RAG, task_type="alpha", portfolio_id=portfolio_id,
        queue=QUEUE_BATCH, db=db, scheduled_at=scheduled_at,
    )


class QuantQueryEnqueueInput(StockQueryRequest):
    scheduled_at: Optional[datetime] = None


@router.post("/quant/query")
async def enqueue_quant_query(
    payload: QuantQueryEnqueueInput,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    verify_user_id_matches(payload.user_id, current_user)
    scheduled_at = payload.scheduled_at
    payload_dict = payload.model_dump(exclude={"scheduled_at"})
    return await _enqueue(
        function="run_quant_query", payload_dict=payload_dict, user=current_user,
        agent_type=AgentType.QUANT, task_type="query", portfolio_id=payload.portfolio_id,
        queue=QUEUE_INTERACTIVE, db=db, scheduled_at=scheduled_at,
    )


# ── Read endpoints ───────────────────────────────────────────────────────────

@router.get("")
async def list_tasks(
    portfolio_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    portfolio = await PortfolioService.get_portfolio(db, portfolio_id)
    if not portfolio or str(portfolio.user_id) != str(current_user.id):
        raise HTTPException(status_code=404, detail="Portfolio not found")
    tasks = await AnalysisTaskService.list_for_portfolio(db, portfolio_id)
    return [AnalysisTaskService.to_dict(t) for t in tasks]


@router.get("/{task_id}")
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    task = await AnalysisTaskService.get(db, task_id)
    if task is None or str(task.user_id) != str(current_user.id):
        raise HTTPException(status_code=404, detail="Task not found")
    return AnalysisTaskService.to_dict(task)


@router.get("/stream/{portfolio_id}")
async def stream_tasks(
    portfolio_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Server-Sent Events stream of AnalysisTask updates for one portfolio.
    One connection per open portfolio page — the frontend keeps this open
    while any run is in flight (or indefinitely while the page is open) and
    updates its dashboard as events arrive, instead of polling.
    """
    portfolio = await PortfolioService.get_portfolio(db, portfolio_id)
    if not portfolio or str(portfolio.user_id) != str(current_user.id):
        raise HTTPException(status_code=404, detail="Portfolio not found")

    pool = _require_pool()

    async def event_generator():
        pubsub = pool.pubsub()
        await pubsub.subscribe(channel_for_portfolio(portfolio_id))
        try:
            yield "event: ping\ndata: connected\n\n"
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
                if message is not None:
                    yield f"event: task_update\ndata: {message['data'].decode() if isinstance(message['data'], bytes) else message['data']}\n\n"
                else:
                    yield "event: ping\ndata: keepalive\n\n"
        finally:
            await pubsub.unsubscribe(channel_for_portfolio(portfolio_id))
            await pubsub.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream")
