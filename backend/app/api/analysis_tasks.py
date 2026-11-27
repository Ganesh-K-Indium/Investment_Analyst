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
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_db_session
from app.database.models import AgentType, AnalysisTask, TaskStatus, User
from app.auth.deps import get_current_user, verify_user_id_matches
from app.services.analysis_tasks import AnalysisTaskService, channel_for_portfolio, channel_for_user
from app.services.portfolio import PortfolioService
from app.services.chat import ChatService
from app.services import analysis_runs, run_insights
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


def get_arq_pool_if_ready():
    """Non-raising variant of _require_pool — for callers (e.g. session
    deletion) where publishing a live-update event is a nice-to-have, not
    something that should fail the request if the queue isn't connected."""
    return _arq_pool


def _sse_frame(raw_data) -> str:
    """
    Every message on a task channel is one of AnalysisTaskService's two
    publish shapes: a full task dict (status update) or {"type":
    "task_deleted", ...}. Pick the SSE event name from that so the frontend
    can tell the two apart without guessing from field shape.
    """
    data = raw_data.decode() if isinstance(raw_data, bytes) else raw_data
    event_name = "task_update"
    try:
        if json.loads(data).get("type") == "task_deleted":
            event_name = "task_deleted"
    except Exception:
        pass
    return f"event: {event_name}\ndata: {data}\n\n"


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
        session_ref=payload_dict.get("thread_id") or payload_dict.get("session_id"),
        request=payload_dict,
    )
    await pool.enqueue_job(
        function, task.id, payload_dict,
        _queue_name=queue,
        _job_id=task.id,
        _defer_until=scheduled_at,
    )
    return AnalysisTaskService.to_summary(task)


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
    # Compare isn't restricted to a portfolio's own tickers, but when it's
    # run from within a portfolio's page (thread_id points at that
    # portfolio's session), tag the task with that portfolio_id anyway —
    # same as ask/alpha — so it shows up in that portfolio's history.
    session = await PortfolioService.get_session(db, payload.thread_id) if payload.thread_id else None
    portfolio_id = session.portfolio.id if session else None
    scheduled_at = payload.scheduled_at
    payload_dict = payload.model_dump(exclude={"scheduled_at"})
    return await _enqueue(
        function="run_rag_compare", payload_dict=payload_dict, user=current_user,
        agent_type=AgentType.RAG, task_type="compare", portfolio_id=portfolio_id,
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
    return [AnalysisTaskService.to_summary(t) for t in tasks]


@router.get("/history")
async def task_history(
    portfolio_id: Optional[int] = None,
    adhoc: bool = False,
    status: Optional[str] = Query(None, description="Comma-separated: PENDING,RUNNING,COMPLETED,FAILED,CANCELLED"),
    agent: Optional[str] = None,
    task_type: Optional[str] = None,
    ticker: Optional[str] = None,
    stance: Optional[str] = None,
    q: Optional[str] = None,
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    pinned: Optional[bool] = None,
    unreviewed: bool = False,
    cursor: Optional[str] = None,
    limit: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """One page of the user's run history (newest first) with filters, and the cursor of the next page."""
    if portfolio_id is not None:
        await _own_portfolio(db, portfolio_id, current_user)
    statuses = [s.strip().upper() for s in (status or "").split(",") if s.strip()]
    if "RUNNING" in statuses and "PENDING" not in statuses:
        statuses.append("PENDING")
    items, next_cursor = await AnalysisTaskService.search(
        db, str(current_user.id), portfolio_id=portfolio_id, adhoc=adhoc, statuses=statuses, agent_type=agent,
        task_type=task_type, ticker=ticker, stance=stance, q=q, date_from=date_from, date_to=date_to, pinned=pinned,
        unreviewed=unreviewed, cursor=cursor, limit=limit,
    )
    return {"items": [AnalysisTaskService.to_summary(t) for t in items], "next_cursor": next_cursor}


class TaskPatch(BaseModel):
    pinned: Optional[bool] = None
    tags: Optional[List[str]] = None
    reviewed: Optional[bool] = None


class ReviewedInput(BaseModel):
    task_ids: Optional[List[str]] = None       # None = every unreviewed run (of the portfolio, when given)
    portfolio_id: Optional[int] = None


class RerunAlphaInput(BaseModel):
    portfolio_id: int
    tickers: Optional[List[str]] = None        # None = every holding


async def _own_portfolio(db: AsyncSession, portfolio_id: int, user: User):
    portfolio = await PortfolioService.get_portfolio(db, portfolio_id)
    if not portfolio or str(portfolio.user_id) != str(user.id):
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return portfolio


async def _own_task(db: AsyncSession, task_id: str, user: User):
    task = await AnalysisTaskService.get(db, task_id)
    if task is None or str(task.user_id) != str(user.id):
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def _alpha_report(task, ticker: str) -> Optional[str]:
    response = (task.result_metadata or {}).get("response") or {}
    for item in response.get("results") or []:
        if isinstance(item, dict) and str(item.get("ticker", "")).upper() == ticker:
            return item.get("report") or ""
    return None


@router.get("/compare")
async def compare_runs(
    a: str, b: str, ticker: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """What changed for one ticker between two ALPHA runs (a = earlier, b = later)."""
    ticker = ticker.upper()
    first, second = await _own_task(db, a, current_user), await _own_task(db, b, current_user)
    old, new = _alpha_report(first, ticker), _alpha_report(second, ticker)
    if old is None or new is None:
        raise HTTPException(status_code=404, detail=f"Both runs need an ALPHA report for {ticker}")
    if first.created_at > second.created_at:
        first, second, old, new = second, first, new, old
    return {"ticker": ticker, "a": AnalysisTaskService.to_summary(first), "b": AnalysisTaskService.to_summary(second),
            **run_insights.diff_reports(old, new)}


@router.post("/reviewed")
async def mark_reviewed(
    payload: ReviewedInput,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    conds = [AnalysisTask.user_id == str(current_user.id), AnalysisTask.reviewed_at.is_(None),
             AnalysisTask.status == TaskStatus.COMPLETED]
    if payload.task_ids is not None:
        conds.append(AnalysisTask.id.in_(payload.task_ids))
    if payload.portfolio_id is not None:
        conds.append(AnalysisTask.portfolio_id == payload.portfolio_id)
    result = await db.execute(update(AnalysisTask).where(*conds).values(reviewed_at=datetime.utcnow()))
    await db.commit()
    return {"updated": result.rowcount or 0}


@router.post("/rerun-alpha")
async def rerun_alpha(
    payload: RerunAlphaInput,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """ALPHA on some (or all) holdings of a portfolio, started by the user from the dashboard."""
    portfolio = await _own_portfolio(db, payload.portfolio_id, current_user)
    holdings = analysis_runs.portfolio_tickers(portfolio)
    tickers = [t.upper() for t in (payload.tickers or holdings)]
    if not tickers:
        raise HTTPException(status_code=400, detail="This portfolio has no holdings to analyse")
    task = await analysis_runs.start_alpha(_require_pool(), db, user_id=str(current_user.id), portfolio=portfolio, tickers=tickers)
    return AnalysisTaskService.to_summary(task)


@router.get("/{task_id}")
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    task = await _own_task(db, task_id, current_user)
    out = AnalysisTaskService.to_dict(task)
    if task.task_type == "alpha":
        from sqlalchemy import select
        from app.database.models import TickerSignal
        from app.services.portfolio_pulse import signal_dict
        rows = (await db.execute(select(TickerSignal).where(TickerSignal.task_id == task.id).order_by(TickerSignal.ticker))).scalars().all()
        out["signals"] = [signal_dict(r) for r in rows]
    return out


@router.patch("/{task_id}")
async def patch_task(
    task_id: str,
    payload: TaskPatch,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    task = await _own_task(db, task_id, current_user)
    if payload.pinned is not None:
        task.pinned = payload.pinned
    if payload.tags is not None:
        task.tags = sorted({t.strip()[:40] for t in payload.tags if t and t.strip()})[:20]
    if payload.reviewed is not None:
        task.reviewed_at = datetime.utcnow() if payload.reviewed else None
    await db.commit()
    await db.refresh(task)
    await AnalysisTaskService.publish(task, get_arq_pool_if_ready())
    return AnalysisTaskService.to_summary(task)


@router.post("/{task_id}/retry")
async def retry_task(
    task_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    task = await _own_task(db, task_id, current_user)
    try:
        new_task = await analysis_runs.retry(_require_pool(), db, task)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return AnalysisTaskService.to_summary(new_task)


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    task = await _own_task(db, task_id, current_user)
    pool = get_arq_pool_if_ready()
    task = await analysis_runs.cancel(pool, db, task, redis_client=pool)
    return AnalysisTaskService.to_summary(task)


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Remove a run from the history. Its research chat goes too when it holds no answer (a cancelled or failed run's
    chat is only the request): an empty session would otherwise linger in the research tab. A chat with answers is kept."""
    task = await _own_task(db, task_id, current_user)
    if (task.status.value if hasattr(task.status, "value") else task.status) in ("PENDING", "RUNNING"):
        raise HTTPException(status_code=409, detail="Cancel the run before deleting it")
    ref = (task.id, task.portfolio_id, task.user_id)
    session_ref = (task.result_metadata or {}).get("session_ref")
    await AnalysisTaskService.delete_ids(db, [task.id])
    pool = get_arq_pool_if_ready()
    chat_deleted = False
    if session_ref and not await ChatService.has_answer(db, session_ref, str(current_user.id)):
        chat_deleted = await ChatService.delete_session(db, session_ref, redis_client=pool)
    if pool is not None:
        await AnalysisTaskService.publish_deleted(pool, *ref, session_ref=session_ref, chat_deleted=chat_deleted)
    return Response(status_code=204, headers={"X-Chat-Deleted": "1" if chat_deleted else "0"})


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
                    yield _sse_frame(message["data"])
                else:
                    yield "event: ping\ndata: keepalive\n\n"
        finally:
            await pubsub.unsubscribe(channel_for_portfolio(portfolio_id))
            await pubsub.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/stream/user/{user_id}")
async def stream_tasks_for_user(
    user_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Server-Sent Events stream of AnalysisTask updates across ALL of a
    user's portfolios — backs the cross-portfolio dashboard. One
    subscription to a user-scoped channel instead of one per portfolio;
    AnalysisTaskService.update_status publishes every update to both.
    """
    verify_user_id_matches(user_id, current_user)

    pool = _require_pool()

    async def event_generator():
        pubsub = pool.pubsub()
        await pubsub.subscribe(channel_for_user(user_id))
        try:
            yield "event: ping\ndata: connected\n\n"
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
                if message is not None:
                    yield _sse_frame(message["data"])
                else:
                    yield "event: ping\ndata: keepalive\n\n"
        finally:
            await pubsub.unsubscribe(channel_for_user(user_id))
            await pubsub.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
