"""Arq job function for the quant supervisor agent (/quant/query)."""
import logging

from app.database.connection import SessionLocal
from app.database.models import TaskStatus
from app.services.analysis_tasks import AnalysisTaskService
from app.jobs.rag_jobs import _load_user
import app.api.quant as quant_router_module

logger = logging.getLogger("app.jobs.quant_jobs")


async def run_quant_query(ctx, task_id: str, payload_dict: dict):
    redis = ctx["redis"]
    async with SessionLocal() as db:
        task = await AnalysisTaskService.update_status(db, task_id, TaskStatus.RUNNING, redis_client=redis)
        if task is None:
            return
        try:
            user = await _load_user(db, task.user_id)
            from app.api.quant import StockQueryRequest
            payload = StockQueryRequest(**payload_dict)
            result = await quant_router_module.query_core(payload, None, db, user)
            # Store the full response (matches ChatResponse shape) so the
            # queued path is a drop-in replacement for the direct quant call.
            await AnalysisTaskService.update_status(
                db, task_id, TaskStatus.COMPLETED,
                progress_message="Done",
                result_metadata={"response": result.model_dump()},
                redis_client=redis,
            )
        except Exception as e:
            logger.exception("run_quant_query failed for task %s", task_id)
            await AnalysisTaskService.update_status(
                db, task_id, TaskStatus.FAILED, progress_message=str(e), redis_client=redis
            )
