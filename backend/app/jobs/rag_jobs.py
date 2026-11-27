"""
Arq job functions for the RAG agent (ask/compare/alpha). Each wraps the same
`*_core` functions the synchronous HTTP routes use (app/api/rag.py), so
there is exactly one implementation of the actual agent-invocation logic —
this module only adds AnalysisTask tracking around it.
"""
import logging

from sqlalchemy import select

from app.database.connection import SessionLocal
from app.database.models import AnalysisTask, AgentType, TaskStatus, User
from app.services.analysis_tasks import AnalysisTaskService
from app.services import usage_ledger
from app.utils import run_progress
from app.utils.run_progress import RunProgress
import app.api.rag as rag_router_module

logger = logging.getLogger("app.jobs.rag_jobs")


async def _load_user(db, user_id: str) -> User:
    """AnalysisTask.user_id is stored as str(User.id) everywhere in this codebase."""
    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise ValueError(f"User {user_id} not found")
    return user


def _progress_sink(task_id: str, redis):
    """Where a run's live progress goes: into the task row (result_metadata["progress"] + a one-line progress_message) and out
    over the same Redis -> SSE channel as every other status change. A short-lived session, so it never fights the run's own."""
    async def sink(snapshot: dict, headline: str):
        async with SessionLocal() as progress_db:
            await AnalysisTaskService.update_status(
                progress_db, task_id, TaskStatus.RUNNING,
                progress_message=headline,
                result_metadata={"progress": snapshot},
                redis_client=redis,
            )
    return sink


async def _fail(db, task_id: str, redis, error: Exception, progress):
    """Mark the task failed, keeping the step-by-step record so the UI can show which step it failed on."""
    meta = None
    if progress is not None:
        progress.finish_all(ok=False)
        await progress.aclose()   # no late 'running' write may overtake the failure below
        meta = {"progress": progress.snapshot()}
    await AnalysisTaskService.update_status(
        db, task_id, TaskStatus.FAILED, progress_message=str(error), result_metadata=meta, redis_client=redis
    )


async def run_rag_ask(ctx, task_id: str, payload_dict: dict):
    redis = ctx["redis"]
    usage_ledger.current_task_id.set(task_id)   # what this run spends is counted against it
    async with SessionLocal() as db:
        task = await AnalysisTaskService.update_status(db, task_id, TaskStatus.RUNNING, redis_client=redis)
        if task is None:
            return
        try:
            user = await _load_user(db, task.user_id)
            from app.api.rag import AskInput
            payload = AskInput(**payload_dict)
            progress = RunProgress("ask", _progress_sink(task_id, redis))
            with run_progress.use(progress):
                result = await rag_router_module.ask_core(payload, db, user)
            progress.finish_all()
            await progress.aclose()
            # Store the full response, not just answer/thread_id — the chat UI
            # renders documents, chart_url, citation_info, web_searched, etc.,
            # and this is what lets the queued path stay a drop-in replacement
            # for the old direct-call response shape (SecondaryAskResponse).
            await AnalysisTaskService.update_status(
                db, task_id, TaskStatus.COMPLETED,
                progress_message="Done",
                result_metadata={"response": result, "progress": progress.snapshot()},
                redis_client=redis,
            )
        except Exception as e:
            logger.exception("run_rag_ask failed for task %s", task_id)
            await _fail(db, task_id, redis, e, locals().get("progress"))


async def run_rag_compare(ctx, task_id: str, payload_dict: dict):
    redis = ctx["redis"]
    usage_ledger.current_task_id.set(task_id)   # what this run spends is counted against it
    async with SessionLocal() as db:
        task = await AnalysisTaskService.update_status(db, task_id, TaskStatus.RUNNING, redis_client=redis)
        if task is None:
            return
        try:
            user = await _load_user(db, task.user_id)
            from app.api.rag import CompareInput
            payload = CompareInput(**payload_dict)
            progress = RunProgress("compare", _progress_sink(task_id, redis))
            with run_progress.use(progress):
                result = await rag_router_module.compare_core(payload, db, user)
            progress.finish_all()
            await progress.aclose()
            await AnalysisTaskService.update_status(
                db, task_id, TaskStatus.COMPLETED,
                progress_message="Done",
                result_metadata={"response": result, "progress": progress.snapshot()},
                redis_client=redis,
            )
        except Exception as e:
            logger.exception("run_rag_compare failed for task %s", task_id)
            await _fail(db, task_id, redis, e, locals().get("progress"))


async def run_rag_alpha(ctx, task_id: str, payload_dict: dict):
    """Batch-lane job: runs the ALPHA framework across every ticker, reporting live step-by-step progress
    (see app/utils/run_progress.py) as each ticker works through its five dimensions."""
    redis = ctx["redis"]
    usage_ledger.current_task_id.set(task_id)   # what this run spends is counted against it
    async with SessionLocal() as db:
        task = await AnalysisTaskService.update_status(db, task_id, TaskStatus.RUNNING, redis_client=redis)
        if task is None:
            return
        try:
            user = await _load_user(db, task.user_id)
            from app.api.rag import AlphaInput
            payload = AlphaInput(**payload_dict)

            # Live, per-step progress (every ticker's five dimensions) replaces the old coarse "3/8 tickers done" ping:
            # the headline written alongside it still carries the counts.
            progress = RunProgress("alpha", _progress_sink(task_id, redis))
            with run_progress.use(progress):
                result = await rag_router_module.alpha_core(payload, db, user)
            progress.finish_all()
            await progress.aclose()
            await AnalysisTaskService.update_status(
                db, task_id, TaskStatus.COMPLETED,
                progress_message="Done",
                result_metadata={"response": result, "progress": progress.snapshot()},
                redis_client=redis,
            )
        except Exception as e:
            logger.exception("run_rag_alpha failed for task %s", task_id)
            await _fail(db, task_id, redis, e, locals().get("progress"))
