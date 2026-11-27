"""Starting, retrying and cancelling background runs, and the opt-in schedules that re-run ALPHA.

Nothing here spends model budget on its own: a run starts only from a user's click (run, retry, re-run) or from a schedule the
user switched on. Schedules are off when created, respect the deployment switch `analysis_schedules_allowed`, and are skipped
while the user is over their monthly token budget."""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AgentType, AnalysisSchedule, AnalysisTask, Document, Portfolio, TaskStatus
from app.jobs.queue import QUEUE_BATCH, QUEUE_INTERACTIVE
from app.services.analysis_tasks import AnalysisTaskService
from app.services.portfolio import PortfolioService

logger = logging.getLogger("app.services.analysis_runs")

# task_type -> (job function, queue, agent)
JOBS: Dict[str, tuple] = {
    "ask": ("run_rag_ask", QUEUE_INTERACTIVE, AgentType.RAG),
    "compare": ("run_rag_compare", QUEUE_INTERACTIVE, AgentType.RAG),
    "alpha": ("run_rag_alpha", QUEUE_BATCH, AgentType.RAG),
    "query": ("run_quant_query", QUEUE_INTERACTIVE, AgentType.QUANT),
}

TRIGGERS = ("daily", "weekly", "monthly", "new_filing")
NEW_FILING_COOLDOWN = timedelta(hours=24)


def portfolio_tickers(portfolio: Portfolio) -> List[str]:
    from app.utils.company_mapping import get_ticker
    out: List[str] = []
    for name in portfolio.company_names or []:
        t = (get_ticker(name) or str(name)).upper()
        if t and t not in out:
            out.append(t)
    return out


def new_thread_id() -> str:
    return f"thread_{datetime.utcnow():%Y%m%d%H%M%S}_{secrets.token_hex(3)}"


async def enqueue(pool, db: AsyncSession, *, user_id: str, task_type: str, payload: Dict[str, Any], portfolio_id: Optional[int],
                  parent_task_id: Optional[str] = None, schedule_id: Optional[int] = None,
                  scheduled_at: Optional[datetime] = None) -> AnalysisTask:
    function, queue, agent = JOBS[task_type]
    task = await AnalysisTaskService.create(
        db, user_id=str(user_id), agent_type=agent, task_type=task_type, portfolio_id=portfolio_id, queue=queue,
        session_ref=payload.get("thread_id") or payload.get("session_id"), request=payload,
        parent_task_id=parent_task_id, schedule_id=schedule_id,
    )
    await pool.enqueue_job(function, task.id, payload, _queue_name=queue, _job_id=task.id, _defer_until=scheduled_at)
    return task


async def start_alpha(pool, db: AsyncSession, *, user_id: str, portfolio: Portfolio, tickers: List[str],
                      parent_task_id: Optional[str] = None, schedule_id: Optional[int] = None) -> AnalysisTask:
    """ALPHA on some of a portfolio's holdings in a fresh research chat (it appears in Secondary Research like any other)."""
    session = await PortfolioService.create_session(db, portfolio.id, str(user_id), thread_id=new_thread_id())
    payload = {"tickers": tickers, "user_id": str(user_id), "thread_id": session.id}
    return await enqueue(pool, db, user_id=str(user_id), task_type="alpha", payload=payload, portfolio_id=portfolio.id,
                         parent_task_id=parent_task_id, schedule_id=schedule_id)


async def retry(pool, db: AsyncSession, task: AnalysisTask) -> AnalysisTask:
    request = (task.result_metadata or {}).get("request")
    if not request or task.task_type not in JOBS:
        raise ValueError("This run was made before retries were recorded; start it again from its tab.")
    return await enqueue(pool, db, user_id=task.user_id, task_type=task.task_type, payload=dict(request),
                         portfolio_id=task.portfolio_id, parent_task_id=task.id, schedule_id=task.schedule_id)


async def cancel(pool, db: AsyncSession, task: AnalysisTask, redis_client=None) -> AnalysisTask:
    status = task.status.value if hasattr(task.status, "value") else task.status
    if status not in ("PENDING", "RUNNING"):
        return task
    if pool is not None:
        try:
            from arq.jobs import Job
            queue = (task.result_metadata or {}).get("queue") or QUEUE_INTERACTIVE
            await Job(task.id, pool, _queue_name=queue).abort(timeout=2)
        except Exception as e:      # the job may not have started, or the worker may be slow to notice: the row decides
            logger.info("Abort of %s not confirmed: %s", task.id, e)
    return await AnalysisTaskService.update_status(db, task.id, TaskStatus.CANCELLED, progress_message="Cancelled",
                                                   redis_client=redis_client)


# ── Schedules ───────────────────────────────────────────────────────────────────────────────────────────────────

def next_run(trigger: str, hour_utc: int, weekday: Optional[int], now: datetime) -> Optional[datetime]:
    """The next time a timed schedule is due, strictly after `now`. None for new_filing (it is event-driven)."""
    if trigger == "new_filing":
        return None
    at = now.replace(hour=hour_utc % 24, minute=0, second=0, microsecond=0)
    if trigger == "daily":
        return at if at > now else at + timedelta(days=1)
    if trigger == "weekly":
        wd = 0 if weekday is None else weekday % 7
        at = at + timedelta(days=(wd - at.weekday()) % 7)
        return at if at > now else at + timedelta(days=7)
    if trigger == "monthly":
        first = at.replace(day=1)
        if first > now:
            return first
        return (first + timedelta(days=32)).replace(day=1)
    raise ValueError(f"unknown trigger {trigger!r}")


def schedule_dict(s: AnalysisSchedule) -> dict:
    from app.utils.time import to_iso_z
    return {"id": s.id, "portfolio_id": s.portfolio_id, "task_type": s.task_type, "tickers": s.tickers, "trigger": s.trigger,
            "hour_utc": s.hour_utc, "weekday": s.weekday, "enabled": bool(s.enabled), "next_run_at": to_iso_z(s.next_run_at),
            "last_run_at": to_iso_z(s.last_run_at), "last_task_id": s.last_task_id, "last_skip_reason": s.last_skip_reason,
            "created_at": to_iso_z(s.created_at)}


async def _over_budget(db: AsyncSession, user_id: str) -> bool:
    try:
        from app.services import usage_ledger
        from app.services.app_settings import store as settings_store
        eff = await settings_store.effective(db, user_id, None, None)
        budget = int((eff.get("models") or {}).get("monthly_token_budget") or 0)
        if not budget:
            return False
        return bool((await usage_ledger.month_summary(db, user_id, budget))["over_budget"])
    except Exception as e:
        logger.warning("Budget check failed for user %s: %s", user_id, e)
        return False


async def _tickers_with_new_filings(db: AsyncSession, tickers: List[str], since: datetime) -> List[str]:
    if not tickers:
        return []
    rows = (await db.execute(select(Document.ticker).where(Document.ticker.in_(tickers), Document.ingested_at > since)
                             .group_by(Document.ticker))).scalars().all()
    return [t for t in tickers if t in set(rows)]


async def run_due_schedules(pool, db: AsyncSession, now: Optional[datetime] = None) -> List[str]:
    """Start every switched-on schedule that is due. Returns the ids of the runs started."""
    from app.core.config import get_settings
    if not get_settings().analysis_schedules_allowed:
        return []
    now = now or datetime.utcnow()
    stmt = select(AnalysisSchedule).where(AnalysisSchedule.enabled.is_(True)).with_for_update(skip_locked=True)
    schedules = list((await db.execute(stmt)).scalars().all())
    started: List[str] = []
    for s in schedules:
        try:
            if s.trigger == "new_filing":
                if s.last_run_at and now - s.last_run_at < NEW_FILING_COOLDOWN:
                    continue
            elif s.next_run_at is None or s.next_run_at > now:
                continue
            portfolio = await db.get(Portfolio, s.portfolio_id)
            if portfolio is None or str(portfolio.user_id) != str(s.user_id):
                s.enabled = False
                s.last_skip_reason = "Portfolio no longer exists"
                continue
            tickers = [t.upper() for t in (s.tickers or portfolio_tickers(portfolio))]
            if s.trigger == "new_filing":
                tickers = await _tickers_with_new_filings(db, tickers, s.last_run_at or s.updated_at or s.created_at or now)
                if not tickers:
                    continue
            else:
                s.next_run_at = next_run(s.trigger, s.hour_utc, s.weekday, now)
            if not tickers:
                s.last_skip_reason = "No holdings to analyse"
                continue
            if await _over_budget(db, s.user_id):
                s.last_skip_reason = f"Skipped {now:%d %b %H:%M} UTC: monthly token budget used up"
                s.last_run_at = now
                continue
            task = await start_alpha(pool, db, user_id=s.user_id, portfolio=portfolio, tickers=tickers, schedule_id=s.id)
            s.last_run_at, s.last_task_id, s.last_skip_reason = now, task.id, None
            started.append(task.id)
        except Exception as e:
            logger.exception("Schedule %s failed to start: %s", s.id, e)
            s.last_skip_reason = f"Could not start: {e}"[:250]
    await db.commit()
    return started


async def schedules_for(db: AsyncSession, portfolio_id: int) -> List[AnalysisSchedule]:
    return list((await db.execute(select(AnalysisSchedule).where(AnalysisSchedule.portfolio_id == portfolio_id)
                                  .order_by(AnalysisSchedule.id))).scalars().all())


async def count_enabled(db: AsyncSession, user_id: str) -> int:
    return int((await db.execute(select(func.count()).select_from(AnalysisSchedule)
                                 .where(AnalysisSchedule.user_id == str(user_id), AnalysisSchedule.enabled.is_(True)))).scalar() or 0)
