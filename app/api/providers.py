"""
Bring-your-own language models (see app/services/llm_providers)

GET    /providers                     every provider with where its credentials come from (yours / workspace / server), masked, and its suggested models
PUT    /providers/{id}                save or remove this provider's fields: {"values": {"ANTHROPIC_API_KEY": "sk-ant-...", "OLLAMA_BASE_URL": null}}
DELETE /providers/{id}                remove everything you saved for the provider (the workspace or server value, if any, applies again)
POST   /providers/{id}/test           one tiny request to check a key/address works; can test values you have not saved yet
GET    /providers/{id}/models         model names the provider's server reports (Ollama, OpenAI-compatible, OpenAI)

?scope=workspace on PUT/DELETE (admin only) saves for everyone who has no key of their own. Keys are encrypted at rest, never returned,
never logged. LOCK_SETTINGS=1 makes all writes read-only for deployments managed through the environment.
"""
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.database.connection import get_db_session
from app.database.models import User
from app.docproc.engines.providers import credentials as dpc
from app.services.llm_providers import PROVIDERS, context, service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/providers", tags=["AI providers"])


def _provider(pid: str):
    if pid not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    return PROVIDERS[pid]


def _owner(scope: str, user: User) -> str:
    if scope == "workspace":
        if user.role != "admin":
            raise HTTPException(403, "Only an administrator can set provider keys for everyone.")
        return service.WORKSPACE
    if scope != "user":
        raise HTTPException(422, "scope must be user or workspace")
    return str(user.id)


def _writable() -> None:
    if dpc.locked():
        raise HTTPException(403, "Provider settings are managed by the server's environment on this deployment.")


@router.get("")
async def list_providers(db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    return await service.providers_view(db, user.id)


class SaveBody(BaseModel):
    values: Dict[str, Optional[str]]


@router.put("/{pid}")
async def save_provider(pid: str, body: SaveBody, scope: str = Query("user"), db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    _provider(pid); _writable()
    try:
        await service.save(db, _owner(scope, user), pid, body.values)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except dpc.StorageError as e:
        raise HTTPException(503, str(e))
    return next(p for p in (await service.providers_view(db, user.id))["providers"] if p["id"] == pid)


@router.delete("/{pid}")
async def remove_provider(pid: str, scope: str = Query("user"), db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    _provider(pid); _writable()
    await service.remove_provider(db, _owner(scope, user), pid)
    return next(p for p in (await service.providers_view(db, user.id))["providers"] if p["id"] == pid)


class TestBody(BaseModel):
    values: Dict[str, str] = {}
    model: str = ""


@router.post("/{pid}/test")
async def test_provider(pid: str, body: TestBody = Body(default_factory=TestBody), db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    p = _provider(pid)
    names = {f.name for f in service.fields_of(pid)}
    if set(body.values) - names:
        raise HTTPException(422, "Those values do not belong to this provider.")
    base = await service.credentials_for(db, user.id)
    return await service.test(pid, body.model.strip(), body.values, base) | {"provider": p.id}


@router.get("/{pid}/models")
async def provider_models(pid: str, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    _provider(pid)
    creds = await service.credentials_for(db, user.id)
    return {"models": await service.list_models(pid, creds)}
