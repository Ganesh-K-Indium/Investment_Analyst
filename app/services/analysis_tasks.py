"""
Service layer for AnalysisTask — the row backing every background agent run
(interactive chat and batch alike). Every status transition also publishes
to a Redis pub/sub channel scoped to the task's portfolio, so the API
process's SSE endpoint can push updates to the frontend without polling the
database.
"""
import json
import logging
import uuid
from datetime import datetime
from typing import Optional, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AnalysisTask, AgentType, TaskStatus

logger = logging.getLogger("app.services.analysis_tasks")

CHANNEL_PREFIX = "analysis_tasks:portfolio:"


def channel_for_portfolio(portfolio_id: Optional[int]) -> str:
    return f"{CHANNEL_PREFIX}{portfolio_id if portfolio_id is not None else 'none'}"


def _serialize(task: AnalysisTask) -> dict:
    return {
        "id": task.id,
        "user_id": task.user_id,
        "portfolio_id": task.portfolio_id,
        "agent_type": task.agent_type.value if hasattr(task.agent_type, "value") else task.agent_type,
        "task_type": task.task_type,
        "status": task.status.value if hasattr(task.status, "value") else task.status,
        "progress_message": task.progress_message,
        "result_metadata": task.result_metadata,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


class AnalysisTaskService:
    @staticmethod
    async def create(
        db: AsyncSession,
        user_id: str,
        agent_type: AgentType,
        task_type: str,
        portfolio_id: Optional[int] = None,
        queue: str = "interactive",
    ) -> AnalysisTask:
        task = AnalysisTask(
            id=str(uuid.uuid4()),
            user_id=user_id,
            portfolio_id=portfolio_id,
            agent_type=agent_type,
            task_type=task_type,
            status=TaskStatus.PENDING,
            result_metadata={"queue": queue},
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

        task.status = status
        task.updated_at = datetime.utcnow()
        if progress_message is not None:
            task.progress_message = progress_message
        if result_metadata is not None:
            merged = dict(task.result_metadata or {})
            merged.update(result_metadata)
            task.result_metadata = merged

        await db.commit()
        await db.refresh(task)

        if redis_client is not None:
            try:
                await redis_client.publish(
                    channel_for_portfolio(task.portfolio_id), json.dumps(_serialize(task))
                )
            except Exception as e:
                logger.warning("Failed to publish task update for %s: %s", task_id, e)

        return task

    @staticmethod
    def to_dict(task: AnalysisTask) -> dict:
        return _serialize(task)
