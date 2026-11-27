"""
Service layer for AnalysisTask — the row backing every background agent run
(interactive chat and batch alike). Every status transition also publishes
to a Redis pub/sub channel scoped to the task's portfolio, so the API
process's SSE endpoint can push updates to the frontend without polling the
database.

What a run was (title, tickers) is recorded when it is queued; what came out of
it (summary, stance per ticker, cost, the failed step) when it ends — see
app/services/run_insights.py. Lists and live events carry a slim copy of the row
(`to_summary`); the full stored response is only returned for one task (`to_dict`).
"""
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import Text, and_, cast, delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AnalysisTask, AgentType, ChatSession, TaskStatus, TickerSignal, UsageEvent
from app.services import run_insights
from app.utils.time import to_iso_z

logger = logging.getLogger("app.services.analysis_tasks")

CHANNEL_PREFIX = "analysis_tasks:portfolio:"


CHANNEL_USER_PREFIX = "analysis_tasks:user:"

TERMINAL = (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)


def channel_for_portfolio(portfolio_id: Optional[int]) -> str:
    return f"{CHANNEL_PREFIX}{portfolio_id if portfolio_id is not None else 'none'}"


def channel_for_user(user_id: str) -> str:
    return f"{CHANNEL_USER_PREFIX}{user_id}"


def _value(v):
    return v.value if hasattr(v, "value") else v


def _insight_fields(task: AnalysisTask) -> dict:
    duration = None
    if task.started_at and task.finished_at:
        duration = max(0, int((task.finished_at - task.started_at).total_seconds() * 1000))
    return {
        "title": task.title,
        "tickers": task.tickers or [],
        "summary": task.summary,
        "outcome": task.outcome,
        "started_at": to_iso_z(task.started_at),
        "finished_at": to_iso_z(task.finished_at),
        "duration_ms": duration,
        "cost_usd": task.cost_usd,
        "total_tokens": task.total_tokens,
        "error_kind": task.error_kind,
        "failed_step": task.failed_step,
        "reviewed_at": to_iso_z(task.reviewed_at),
        "pinned": bool(task.pinned),
        "tags": task.tags or [],
        "parent_task_id": task.parent_task_id,
        "schedule_id": task.schedule_id,
        "can_retry": bool((task.result_metadata or {}).get("request")),
    }


def _serialize(task: AnalysisTask) -> dict:
    """The whole row, stored response included — for one task's detail and the chat hand-off."""
    meta = dict(task.result_metadata or {})
    meta.pop("request", None)       # what was queued (for retry) stays on the server
    return {
        "id": task.id,
        "user_id": task.user_id,
        "portfolio_id": task.portfolio_id,
        "agent_type": _value(task.agent_type),
        "task_type": task.task_type,
        "status": _value(task.status),
        "progress_message": task.progress_message,
        "result_metadata": meta,
        "created_at": to_iso_z(task.created_at),
        "updated_at": to_iso_z(task.updated_at),
        **_insight_fields(task),
    }


def _slim_metadata(task: AnalysisTask) -> dict:
    """Only what lists and live updates need: the chat it belongs to, and step progress while it is in flight."""
    meta = task.result_metadata or {}
    slim: Dict[str, Any] = {k: meta[k] for k in ("queue", "session_ref") if k in meta}
    response = meta.get("response")
    if isinstance(response, dict):
        slim["response"] = {k: response[k] for k in ("thread_id", "session_id") if isinstance(response.get(k), str)}
    if _value(task.status) in ("PENDING", "RUNNING", "FAILED") and meta.get("progress"):
        slim["progress"] = meta["progress"]
    return slim


def to_summary(task: AnalysisTask) -> dict:
    return {**_serialize_head(task), "result_metadata": _slim_metadata(task), **_insight_fields(task)}


def _serialize_head(task: AnalysisTask) -> dict:
    return {
        "id": task.id,
        "user_id": task.user_id,
        "portfolio_id": task.portfolio_id,
        "agent_type": _value(task.agent_type),
        "task_type": task.task_type,
        "status": _value(task.status),
        "progress_message": task.progress_message,
        "created_at": to_iso_z(task.created_at),
        "updated_at": to_iso_z(task.updated_at),
    }


def encode_cursor(task: AnalysisTask) -> str:
    return f"{task.created_at.isoformat()}|{task.id}"


def decode_cursor(cursor: str) -> Optional[Tuple[datetime, str]]:
    try:
        ts, task_id = cursor.split("|", 1)
        return datetime.fromisoformat(ts), task_id
    except Exception:
        return None


def has_live_chat():
    """A run without a portfolio is shown only while its chat still exists: otherwise nothing is left to open."""
    ref = AnalysisTask.result_metadata["session_ref"].as_string()
    return exists().where(ChatSession.session_id == ref, ChatSession.user_id == AnalysisTask.user_id)


def json_contains_text(column, value: str):
    """A JSON list/object column holding the exact string `value` (portable: Postgres and SQLite)."""
    return cast(column, Text).ilike(f'%"{value}"%')


class AnalysisTaskService:
    @staticmethod
    async def create(
        db: AsyncSession,
        user_id: str,
        agent_type: AgentType,
        task_type: str,
        portfolio_id: Optional[int] = None,
        queue: str = "interactive",
        session_ref: Optional[str] = None,
        request: Optional[dict] = None,
        parent_task_id: Optional[str] = None,
        schedule_id: Optional[int] = None,
    ) -> AnalysisTask:
        # session_ref ties the task to its chat session from the moment it is
        # queued, so delete_for_session can also clean up FAILED/PENDING/RUNNING
        # tasks (they never get result_metadata.response written).
        metadata: Dict[str, Any] = {"queue": queue}
        if session_ref:
            metadata["session_ref"] = session_ref
        if request is not None:
            metadata["request"] = request          # lets the run be retried exactly as it was asked
        title, tickers = run_insights.describe_request(task_type, request or {})
        task = AnalysisTask(
            id=str(uuid.uuid4()),
            user_id=user_id,
            portfolio_id=portfolio_id,
            agent_type=agent_type,
            task_type=task_type,
            status=TaskStatus.PENDING,
            result_metadata=metadata,
            title=title,
            tickers=tickers or None,
            parent_task_id=parent_task_id,
            schedule_id=schedule_id,
            pinned=False,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task

    @staticmethod
    async def get(db: AsyncSession, task_id: str) -> Optional[AnalysisTask]:
        result = await db.execute(select(AnalysisTask).where(AnalysisTask.id == task_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def list_for_portfolio(
        db: AsyncSession, portfolio_id: int, limit: int = 50
    ) -> List[AnalysisTask]:
        result = await db.execute(
            select(AnalysisTask)
            .where(AnalysisTask.portfolio_id == portfolio_id)
            .order_by(AnalysisTask.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_for_user(
        db: AsyncSession, user_id: str, limit: int = 50
    ) -> List[AnalysisTask]:
        """
        Recent activity across all of a user's portfolios, newest first.
        Excludes tasks with no portfolio_id (e.g. /rag/compare runs, which
        aren't tied to any portfolio) — this feed is portfolio-scoped, and
        those tasks never show up on any single-portfolio dashboard either,
        so including them here would surface them as "Unknown portfolio"
        with no page they actually belong to.
        """
        result = await db.execute(
            select(AnalysisTask)
            .where(AnalysisTask.user_id == user_id, AnalysisTask.portfolio_id.isnot(None))
            .order_by(AnalysisTask.updated_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def search(
        db: AsyncSession,
        user_id: str,
        *,
        portfolio_id: Optional[int] = None,
        adhoc: bool = False,
        statuses: Iterable[str] = (),
        agent_type: Optional[str] = None,
        task_type: Optional[str] = None,
        ticker: Optional[str] = None,
        stance: Optional[str] = None,
        q: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        pinned: Optional[bool] = None,
        unreviewed: bool = False,
        cursor: Optional[str] = None,
        limit: int = 30,
    ) -> Tuple[List[AnalysisTask], Optional[str]]:
        """One page of a user's run history, newest first, and the cursor for the next page (None at the end)."""
        conds = [AnalysisTask.user_id == str(user_id)]
        if portfolio_id is not None:
            conds.append(AnalysisTask.portfolio_id == portfolio_id)
        elif adhoc:
            conds.extend([AnalysisTask.portfolio_id.is_(None), has_live_chat()])
        else:
            conds.append(or_(AnalysisTask.portfolio_id.isnot(None), has_live_chat()))
        wanted = [TaskStatus(s) for s in statuses if s in TaskStatus.__members__]
        if wanted:
            conds.append(AnalysisTask.status.in_(wanted))
        if agent_type in ("rag", "quant"):
            conds.append(AnalysisTask.agent_type == AgentType(agent_type))
        if task_type:
            conds.append(AnalysisTask.task_type == task_type)
        if ticker:
            conds.append(json_contains_text(AnalysisTask.tickers, ticker.strip().upper()))
        if stance and stance in run_insights.STANCES:
            conds.append(json_contains_text(AnalysisTask.outcome, stance))
        if q and q.strip():
            like = f"%{q.strip()}%"
            conds.append(or_(AnalysisTask.title.ilike(like), AnalysisTask.summary.ilike(like),
                             AnalysisTask.progress_message.ilike(like)))
        if date_from:
            conds.append(AnalysisTask.created_at >= date_from)
        if date_to:
            conds.append(AnalysisTask.created_at < date_to)
        if pinned is not None:
            conds.append(AnalysisTask.pinned.is_(pinned))
        if unreviewed:
            conds.extend([AnalysisTask.status == TaskStatus.COMPLETED, AnalysisTask.reviewed_at.is_(None)])
        decoded = decode_cursor(cursor) if cursor else None
        if decoded:
            ts, last_id = decoded
            conds.append(or_(AnalysisTask.created_at < ts, and_(AnalysisTask.created_at == ts, AnalysisTask.id < last_id)))
        limit = max(1, min(limit, 100))
        rows = list((await db.execute(
            select(AnalysisTask).where(*conds)
            .order_by(AnalysisTask.created_at.desc(), AnalysisTask.id.desc())
            .limit(limit + 1)
        )).scalars().all())
        more = len(rows) > limit
        rows = rows[:limit]
        return rows, (encode_cursor(rows[-1]) if more and rows else None)

    @staticmethod
    async def update_status(
        db: AsyncSession,
        task_id: str,
        status: TaskStatus,
        progress_message: Optional[str] = None,
        result_metadata: Optional[dict] = None,
        redis_client=None,
    ) -> Optional[AnalysisTask]:
        task = await AnalysisTaskService.get(db, task_id)
        if task is None:
            logger.warning("update_status: task %s not found", task_id)
            return None
        if _value(task.status) == TaskStatus.CANCELLED.value and status != TaskStatus.CANCELLED:
            return task                      # a cancelled run stays cancelled, whatever its job writes afterwards

        now = datetime.utcnow()
        task.status = status
        task.updated_at = now
        if status == TaskStatus.RUNNING and task.started_at is None:
            task.started_at = now
        if status in TERMINAL:
            task.finished_at = now
        if progress_message is not None:
            task.progress_message = progress_message
        if result_metadata is not None:
            merged = dict(task.result_metadata or {})
            merged.update(result_metadata)
            task.result_metadata = merged

        if status == TaskStatus.COMPLETED:
            await AnalysisTaskService._record_outcome(db, task)
        elif status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            task.error_kind = "cancelled" if status == TaskStatus.CANCELLED else run_insights.classify_error(task.progress_message)
            task.failed_step = run_insights.failed_step_of((task.result_metadata or {}).get("progress"))
            await AnalysisTaskService._record_cost(db, task)

        await db.commit()
        await db.refresh(task)
        await AnalysisTaskService.publish(task, redis_client)
        return task

    @staticmethod
    async def publish(task: AnalysisTask, redis_client) -> None:
        if redis_client is None:
            return
        payload = json.dumps(to_summary(task))
        try:
            await redis_client.publish(channel_for_portfolio(task.portfolio_id), payload)
        except Exception as e:
            logger.warning("Failed to publish task update for %s: %s", task.id, e)
        try:
            await redis_client.publish(channel_for_user(task.user_id), payload)
        except Exception as e:
            logger.warning("Failed to publish user-scoped task update for %s: %s", task.id, e)

    @staticmethod
    async def _record_cost(db: AsyncSession, task: AnalysisTask) -> None:
        try:
            row = (await db.execute(
                select(func.sum(UsageEvent.cost_usd), func.sum(UsageEvent.prompt_tokens + UsageEvent.completion_tokens))
                .where(UsageEvent.task_id == task.id))).one()
            if row[1]:
                task.cost_usd = round(float(row[0] or 0.0), 4)
                task.total_tokens = int(row[1])
        except Exception as e:   # the dashboards' numbers must never fail a run
            logger.warning("Could not add up the cost of task %s: %s", task.id, e)

    @staticmethod
    async def _record_outcome(db: AsyncSession, task: AnalysisTask) -> None:
        """Summary, tickers, stance per ticker and cost, from the stored response. Safe to call again (backfill)."""
        response = (task.result_metadata or {}).get("response")
        if isinstance(response, dict):
            try:
                found = run_insights.describe_result(task.task_type, _value(task.agent_type), response)
                if found["title"] and (not task.title or task.title in ("Research question", "Company comparison", "ALPHA analysis")):
                    task.title = found["title"]
                if found["tickers"]:
                    task.tickers = found["tickers"]
                task.summary = found["summary"]
                task.outcome = found["outcome"]
                if task.task_type == "alpha":
                    await db.execute(delete(TickerSignal).where(TickerSignal.task_id == task.id))
                    for sig in found["signals"]:
                        db.add(TickerSignal(
                            task_id=task.id, user_id=task.user_id, portfolio_id=task.portfolio_id, ticker=sig["ticker"],
                            stance=sig["stance"], stance_score=sig["stance_score"], pillars=sig["pillars"],
                            fact_counts=sig["fact_counts"], key_risk=sig["key_risk"],
                            created_at=task.finished_at or task.updated_at or datetime.utcnow(),
                        ))
            except Exception as e:
                logger.warning("Could not read the outcome of task %s: %s", task.id, e)
        task.error_kind = None
        task.failed_step = None
        await AnalysisTaskService._record_cost(db, task)

    @staticmethod
    async def backfill(db: AsyncSession, limit: Optional[int] = None) -> int:
        """Fill the dashboard fields of runs made before they existed. Idempotent."""
        stmt = select(AnalysisTask).order_by(AnalysisTask.created_at)
        if limit:
            stmt = stmt.limit(limit)
        tasks = list((await db.execute(stmt)).scalars().all())
        for task in tasks:
            request = (task.result_metadata or {}).get("request") or {}
            if not task.title:
                task.title, tickers = run_insights.describe_request(task.task_type, request)
                task.tickers = task.tickers or tickers or None
            status = _value(task.status)
            if status == "COMPLETED":
                await AnalysisTaskService._usage_from_response(db, task)
                await AnalysisTaskService._record_outcome(db, task)
                task.reviewed_at = task.reviewed_at or task.updated_at   # old runs do not flood the review inbox
            elif status == "FAILED":
                task.error_kind = task.error_kind or run_insights.classify_error(task.progress_message)
                task.failed_step = task.failed_step or run_insights.failed_step_of((task.result_metadata or {}).get("progress"))
            if task.pinned is None:
                task.pinned = False
        await db.commit()
        return len(tasks)

    @staticmethod
    async def _usage_from_response(db: AsyncSession, task: AnalysisTask) -> None:
        """ALPHA runs made before their usage was stored still carry token counts per ticker in their response: store those
        as usage (estimated at the price of gpt-4o, the model the ALPHA steps report), once."""
        if task.task_type != "alpha":
            return
        already = (await db.execute(select(func.count()).select_from(UsageEvent).where(UsageEvent.task_id == task.id))).scalar()
        if already:
            return
        from app.services import usage_ledger
        response = (task.result_metadata or {}).get("response") or {}
        for item in response.get("results") or []:
            usage = (item or {}).get("usage") or {}
            p, c = int(usage.get("total_prompt_tokens") or 0), int(usage.get("total_completion_tokens") or 0)
            if p + c == 0:
                continue
            db.add(UsageEvent(user_id=task.user_id, run_label=f"alpha:backfill:{item.get('ticker', '')}"[:200], role="alpha (backfilled)",
                              model="gpt-4o", prompt_tokens=p, completion_tokens=c, cost_usd=usage_ledger.cost("gpt-4o", p, c),
                              task_id=task.id, portfolio_id=task.portfolio_id,
                              created_at=task.finished_at or task.updated_at or task.created_at))
        await db.flush()

    @staticmethod
    def to_dict(task: AnalysisTask) -> dict:
        return _serialize(task)

    @staticmethod
    def to_summary(task: AnalysisTask) -> dict:
        return to_summary(task)

    @staticmethod
    async def delete_for_session(db: AsyncSession, session_id: str, redis_client=None) -> int:
        """
        Deletes any AnalysisTask whose completed result is tied to this chat
        session — thread_id for RAG (ask/compare/alpha) tasks, session_id for
        quant tasks, both nested under result_metadata.response. Called when
        a chat session is deleted so it doesn't linger in the Overview
        dashboard as a "Ready" run whose View button points at nothing.

        COMPLETED tasks carry the id inside result_metadata.response;
        every task (including FAILED ones, which never get a response) also
        carries it as result_metadata.session_ref, set when it is queued, so a
        failed run disappears from the dashboard when its chat is deleted.

        Publishes a "task_deleted" event per removed task (when redis_client
        is given) so an open dashboard drops it immediately instead of only
        on the next full refetch — without this, a deleted run kept showing
        up as "Ready" until the user navigated away and back.
        """
        thread_id_expr = AnalysisTask.result_metadata.op("->")("response").op("->>")("thread_id")
        session_id_expr = AnalysisTask.result_metadata.op("->")("response").op("->>")("session_id")
        session_ref_expr = AnalysisTask.result_metadata.op("->>")("session_ref")
        condition = or_(
            thread_id_expr == session_id,
            session_id_expr == session_id,
            session_ref_expr == session_id,
        )

        result = await db.execute(select(AnalysisTask).where(condition))
        tasks = list(result.scalars().all())
        if not tasks:
            return 0

        deleted_refs = [(t.id, t.portfolio_id, t.user_id) for t in tasks]
        await AnalysisTaskService.delete_ids(db, [t.id for t in tasks])

        if redis_client is not None:
            for task_id, portfolio_id, user_id in deleted_refs:
                await AnalysisTaskService.publish_deleted(redis_client, task_id, portfolio_id, user_id)

        return len(deleted_refs)

    @staticmethod
    async def delete_ids(db: AsyncSession, task_ids: List[str]) -> None:
        # ticker_signals cascade in Postgres; deleted explicitly too so SQLite (tests) and old FKs agree
        await db.execute(delete(TickerSignal).where(TickerSignal.task_id.in_(task_ids)))
        await db.execute(delete(AnalysisTask).where(AnalysisTask.id.in_(task_ids)))
        await db.commit()

    @staticmethod
    async def publish_deleted(redis_client, task_id: str, portfolio_id: Optional[int], user_id: str) -> None:
        payload = json.dumps({"type": "task_deleted", "id": task_id, "portfolio_id": portfolio_id})
        try:
            await redis_client.publish(channel_for_portfolio(portfolio_id), payload)
        except Exception as e:
            logger.warning("Failed to publish task deletion for %s: %s", task_id, e)
        try:
            await redis_client.publish(channel_for_user(user_id), payload)
        except Exception as e:
            logger.warning("Failed to publish user-scoped task deletion for %s: %s", task_id, e)
