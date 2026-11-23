"""One place that sets up everything a model call needs for a request: the user's settings and their provider credentials."""
from contextlib import asynccontextmanager
from typing import Any, Dict, Iterable, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import app_settings, llm_providers
from app.services.app_settings import store as settings_store
from app.services.llm_providers import service as provider_service

RAG_ROLES = ("router", "grader", "generator", "rewriter", "analysis")
QUANT_ROLES = ("quant_supervisor", "quant_agent")


async def load(db: AsyncSession, user_id: Any, portfolio_id: Any = None, override: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    return await settings_store.effective(db, user_id, portfolio_id, override), await provider_service.credentials_for(db, user_id)


def preflight(settings: Dict[str, Dict[str, Any]], creds: Dict[str, str], roles: Iterable[str]) -> None:
    """400 with a plain message when a job the run will certainly use is set to a provider that is not connected."""
    wanted = {f"{r}_model" for r in roles}
    with llm_providers.use_credentials(creds):
        missing = provider_service.missing_for({k: v for k, v in settings["models"].items() if k in wanted})
    if missing:
        raise HTTPException(status_code=400, detail=provider_service.missing_message(missing))


@asynccontextmanager
async def run_context(db: AsyncSession, user_id: Any, portfolio_id: Any = None, settings: Optional[Dict[str, Dict[str, Any]]] = None, roles: Iterable[str] = ()):
    s, creds = await load(db, user_id, portfolio_id) if settings is None else (settings, await provider_service.credentials_for(db, user_id))
    if roles:
        preflight(s, creds, roles)
    with app_settings.use(s), llm_providers.use_credentials(creds):
        yield s, creds
