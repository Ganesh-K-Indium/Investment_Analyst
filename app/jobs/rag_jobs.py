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
import app.api.rag as rag_router_module

logger = logging.getLogger("app.jobs.rag_jobs")


async def _load_user(db, user_id: str) -> User:
    """AnalysisTask.user_id is stored as str(User.id) everywhere in this codebase."""
    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise ValueError(f"User {user_id} not found")
    return user


async def run_rag_ask(ctx, task_id: str, payload_dict: dict):
    redis = ctx["redis"]
    async with SessionLocal() as db:
        task = await AnalysisTaskService.update_status(db, task_id, TaskStatus.RUNNING, redis_client=redis)
        if task is None:
            return
        try:
            user = await _load_user(db, task.user_id)
            from app.api.rag import AskInput
            payload = AskInput(**payload_dict)
            result = await rag_router_module.ask_core(payload, db, user)
            # Store the full response, not just answer/thread_id — the chat UI
            # renders documents, chart_url, citation_info, web_searched, etc.,
            # and this is what lets the queued path stay a drop-in replacement
            # for the old direct-call response shape (SecondaryAskResponse).
            await AnalysisTaskService.update_status(
                db, task_id, TaskStatus.COMPLETED,
                progress_message="Done",
                result_metadata={"response": result},
                redis_client=redis,
            )
        except Exception as e:
            logger.exception("run_rag_ask failed for task %s", task_id)
            await AnalysisTaskService.update_status(
                db, task_id, TaskStatus.FAILED, progress_message=str(e), redis_client=redis
            )


async def run_rag_compare(ctx, task_id: str, payload_dict: dict):
    redis = ctx["redis"]
    async with SessionLocal() as db:
        task = await AnalysisTaskService.update_status(db, task_id, TaskStatus.RUNNING, redis_client=redis)
        if task is None:
            return
        try:
            user = await _load_user(db, task.user_id)
            from app.api.rag import CompareInput
            payload = CompareInput(**payload_dict)
            result = await rag_router_module.compare_core(payload, db, user)
            await AnalysisTaskService.update_status(
                db, task_id, TaskStatus.COMPLETED,
                progress_message="Done",
                result_metadata={"response": result},
                redis_client=redis,
            )
        except Exception as e:
            logger.exception("run_rag_compare failed for task %s", task_id)
            await AnalysisTaskService.update_status(
                db, task_id, TaskStatus.FAILED, progress_message=str(e), redis_client=redis
            )


async def run_rag_alpha(ctx, task_id: str, payload_dict: dict):
    """Batch-lane job: runs the ALPHA framework across every ticker, pushing
    incremental progress ('3/8 tickers done') as each one finishes."""
    redis = ctx["redis"]
    async with SessionLocal() as db:
        task = await AnalysisTaskService.update_status(db, task_id, TaskStatus.RUNNING, redis_client=redis)
        if task is None:
            return
        try:
            user = await _load_user(db, task.user_id)
            from app.api.rag import AlphaInput
            payload = AlphaInput(**payload_dict)

            async def on_ticker_done(ticker, index, total):
                # Best-effort progress ping — uses a short-lived session so it
                # doesn't fight the outer `db` session mid-gather.
                async with SessionLocal() as progress_db:
                    await AnalysisTaskService.update_status(
                        progress_db, task_id, TaskStatus.RUNNING,
                        progress_message=f"{index + 1}/{total} tickers done ({ticker})",
                        redis_client=redis,
                    )

            result = await rag_router_module.alpha_core(payload, db, user, on_ticker_done=on_ticker_done)
            await AnalysisTaskService.update_status(
                db, task_id, TaskStatus.COMPLETED,
                progress_message="Done",
                result_metadata={"response": result},
                redis_client=redis,
            )
        except Exception as e:
            logger.exception("run_rag_alpha failed for task %s", task_id)
            await AnalysisTaskService.update_status(
                db, task_id, TaskStatus.FAILED, progress_message=str(e), redis_client=redis
            )
