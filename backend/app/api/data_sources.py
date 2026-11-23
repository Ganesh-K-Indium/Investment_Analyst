"""
Market-data providers you connect for the quant agent (see app/services/data_sources.py)

GET    /data-sources              the providers you can connect (with what each can fetch) and the ones you have connected
POST   /data-sources              connect one: {provider, name, api_key, base_url?, auth_name?, auth_in?, test_path?, description?}; tested before it is saved
PATCH  /data-sources/{id}         name, description, enabled, api_key (new key; tested)
DELETE /data-sources/{id}
POST   /data-sources/{id}/test    check the key still works
PostgreSQL databases are connected under Integrations (they already are).
"""
import json
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.database.connection import get_db_session
from app.database.models import User, UserDataSource
from app.docproc.engines.providers import credentials as dpc
from app.services import data_sources as svc, user_mcp

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/data-sources", tags=["Data sources"])

NAME = r"^[A-Za-z0-9 _.-]+$"


def _view(s: UserDataSource) -> Dict[str, Any]:
    spec = svc.CATALOG.get(s.provider, {})
    return {"id": s.id, "provider": s.provider, "provider_label": spec.get("label", s.provider), "name": s.name, "base_url": s.base_url,
            "description": s.description, "enabled": s.enabled, "has_key": bool(s.auth_enc), "last_error": s.last_error,
            "operations": list(spec.get("ops", {})), "updated_at": s.updated_at.isoformat() if s.updated_at else None}


def auth_of(s: UserDataSource) -> Optional[Dict[str, str]]:
    raw = dpc._decrypt(s.auth_enc) if s.auth_enc else None
    return json.loads(raw) if raw else None


async def _owned(db: AsyncSession, sid: str, user: User) -> UserDataSource:
    s = await db.get(UserDataSource, sid)
    if s is None or s.user_id != str(user.id):
        raise HTTPException(404, "Data source not found")
    return s


def _pack(provider: str, key: Optional[str], auth_name: Optional[str], auth_in: Optional[str]) -> Optional[Dict[str, str]]:
    if not key:
        return None
    d = {"value": key}
    if provider == "custom":
        d.update({"name": auth_name or "Authorization", "in": auth_in if auth_in in ("header", "query") else "header"})
    return d


async def _check(s: UserDataSource, auth: Optional[Dict[str, str]], test_path: str = "/") -> None:
    try:
        await svc.test(s.provider, auth, s.base_url, test_path)
        s.last_error = None
    except (svc.SourceError, user_mcp.UnsafeServer) as e:
        raise HTTPException(422, str(e))


class CreateBody(BaseModel):
    provider: str
    name: str = Field(..., min_length=1, max_length=60, pattern=NAME)
    api_key: Optional[str] = Field(None, max_length=2000)
    base_url: Optional[str] = Field(None, max_length=500)
    auth_name: Optional[str] = Field(None, max_length=60)
    auth_in: Optional[str] = None
    test_path: str = Field("/", max_length=200)
    description: Optional[str] = Field(None, max_length=500)


class PatchBody(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=60, pattern=NAME)
    description: Optional[str] = Field(None, max_length=500)
    enabled: Optional[bool] = None
    api_key: Optional[str] = Field(None, max_length=2000)
    auth_name: Optional[str] = Field(None, max_length=60)
    auth_in: Optional[str] = None


@router.get("")
async def list_sources(db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    rows = (await db.execute(select(UserDataSource).where(UserDataSource.user_id == str(user.id)).order_by(UserDataSource.created_at))).scalars().all()
    return {"providers": svc.catalog_view(), "sources": [_view(s) for s in rows], "max": svc.MAX_SOURCES_PER_USER}


@router.post("", status_code=201)
async def create(body: CreateBody, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    if body.provider not in svc.CATALOG:
        raise HTTPException(422, "Unknown provider")
    n = (await db.execute(select(func.count()).select_from(UserDataSource).where(UserDataSource.user_id == str(user.id)))).scalar() or 0
    if n >= svc.MAX_SOURCES_PER_USER:
        raise HTTPException(409, f"You can connect up to {svc.MAX_SOURCES_PER_USER} data sources.")
    if body.provider != "custom" and not body.api_key:
        raise HTTPException(422, "An API key is required.")
    if body.provider == "custom":
        try:
            user_mcp.check_url(body.base_url or "")
        except user_mcp.UnsafeServer as e:
            raise HTTPException(422, str(e))
    existing = (await db.execute(select(UserDataSource.name).where(UserDataSource.user_id == str(user.id)))).scalars().all()
    if body.name.strip().lower() in {x.lower() for x in existing}:
        raise HTTPException(409, f"You already have a data source named {body.name.strip()!r}.")
    s = UserDataSource(id=uuid.uuid4().hex, user_id=str(user.id), provider=body.provider, name=body.name.strip(),
                       base_url=(body.base_url or "").strip().rstrip("/") or None if body.provider == "custom" else None,
                       description=body.description, enabled=True)
    auth = _pack(body.provider, body.api_key, body.auth_name, body.auth_in)
    if auth:
        try:
            dpc._fernet()
            s.auth_enc = dpc._encrypt(json.dumps(auth))
        except Exception:      # noqa: BLE001
            raise HTTPException(503, "Cannot store the key: encryption is not set up on this server.")
    await _check(s, auth, body.test_path)                     # not saved when it does not work
    db.add(s)
    await db.commit()
    return _view(s)


@router.patch("/{sid}")
async def patch(sid: str, body: PatchBody, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    s = await _owned(db, sid, user)
    for f in ("name", "description", "enabled"):
        v = getattr(body, f)
        if v is not None:
            setattr(s, f, v)
    if body.api_key:
        old = auth_of(s) or {}
        auth = _pack(s.provider, body.api_key, body.auth_name or old.get("name"), body.auth_in or old.get("in"))
        await _check(s, auth)
        dpc._fernet()
        s.auth_enc = dpc._encrypt(json.dumps(auth))
    await db.commit()
    return _view(s)


@router.delete("/{sid}", status_code=204)
async def delete(sid: str, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    await db.delete(await _owned(db, sid, user))
    await db.commit()


@router.post("/{sid}/test")
async def test(sid: str, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    s = await _owned(db, sid, user)
    try:
        await svc.test(s.provider, auth_of(s), s.base_url)
        s.last_error = None
    except (svc.SourceError, user_mcp.UnsafeServer) as e:
        s.last_error = str(e)
    await db.commit()
    return _view(s)
