"""
Your own MCP servers for the quant agent (see app/services/user_mcp.py)

GET    /mcp-servers              mine, with the tools found on the last test
POST   /mcp-servers              connect one: {name, url, description?, auth_name?, auth_value?, allow_actions?}; it is tested before it is saved
PATCH  /mcp-servers/{id}         enabled, allow_actions, description, name, auth_value
DELETE /mcp-servers/{id}
POST   /mcp-servers/{id}/test    connect again and refresh the tool list
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
from app.database.models import User, UserMcpServer
from app.docproc.engines.providers import credentials as dpc
from app.services import user_mcp

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mcp-servers", tags=["Your MCP servers"])


def _view(s: UserMcpServer) -> Dict[str, Any]:
    return {"id": s.id, "name": s.name, "url": s.url, "description": s.description, "enabled": s.enabled, "allow_actions": s.allow_actions,
            "has_auth": bool(s.auth_enc), "tools": [{k: v for k, v in t.items() if k != "schema"} for t in (s.tools or [])], "last_error": s.last_error,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None}


def auth_of(s: UserMcpServer) -> Optional[Dict[str, str]]:
    if not s.auth_enc:
        return None
    raw = dpc._decrypt(s.auth_enc)
    return json.loads(raw) if raw else None


async def _owned(db: AsyncSession, sid: str, user: User) -> UserMcpServer:
    s = await db.get(UserMcpServer, sid)
    if s is None or s.user_id != str(user.id):
        raise HTTPException(404, "Server not found")
    return s


async def _connect(s: UserMcpServer) -> None:
    try:
        s.tools = await user_mcp.list_tools(s.url, auth_of(s))
        s.last_error = None
    except user_mcp.UnsafeServer as e:
        s.tools, s.last_error = [], str(e)
    except BaseException as e:      # noqa: BLE001 - the SDK raises exception groups
        inner = getattr(e, "exceptions", [e])[0]
        s.tools, s.last_error = [], f"Could not connect: {type(inner).__name__}: {str(inner)[:200]}"


class CreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=60, pattern=r"^[A-Za-z0-9 _.-]+$")
    url: str = Field(..., max_length=500)
    description: Optional[str] = Field(None, max_length=500)
    auth_name: str = Field("Authorization", max_length=60)
    auth_value: Optional[str] = Field(None, max_length=2000)
    allow_actions: bool = False


class PatchBody(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=60, pattern=r"^[A-Za-z0-9 _.-]+$")
    description: Optional[str] = Field(None, max_length=500)
    enabled: Optional[bool] = None
    allow_actions: Optional[bool] = None
    auth_name: Optional[str] = Field(None, max_length=60)
    auth_value: Optional[str] = Field(None, max_length=2000)      # "" removes it


def _set_auth(s: UserMcpServer, name: str, value: Optional[str]) -> None:
    if value is None:
        return
    if value == "":
        s.auth_enc = None
        return
    dpc._fernet()
    s.auth_enc = dpc._encrypt(json.dumps({"name": name or "Authorization", "value": value}))


@router.get("")
async def list_servers(db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    rows = (await db.execute(select(UserMcpServer).where(UserMcpServer.user_id == str(user.id)).order_by(UserMcpServer.created_at))).scalars().all()
    return {"servers": [_view(s) for s in rows], "max": user_mcp.MAX_SERVERS_PER_USER, "private_allowed": user_mcp.allow_private()}


@router.post("", status_code=201)
async def create(body: CreateBody, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    n = (await db.execute(select(func.count()).select_from(UserMcpServer).where(UserMcpServer.user_id == str(user.id)))).scalar() or 0
    if n >= user_mcp.MAX_SERVERS_PER_USER:
        raise HTTPException(409, f"You can connect up to {user_mcp.MAX_SERVERS_PER_USER} servers.")
    try:
        user_mcp.check_url(body.url)
        s = UserMcpServer(id=uuid.uuid4().hex, user_id=str(user.id), name=body.name.strip(), url=body.url.strip(), description=body.description,
                          enabled=True, allow_actions=body.allow_actions)
        _set_auth(s, body.auth_name, body.auth_value)
    except user_mcp.UnsafeServer as e:
        raise HTTPException(422, str(e))
    except Exception as e:      # noqa: BLE001 - encryption not configured
        if "encrypt" in str(e).lower() or "key" in str(e).lower():
            raise HTTPException(503, "Cannot store the auth header: encryption is not set up on this server.")
        raise
    await _connect(s)
    if s.last_error:
        raise HTTPException(422, s.last_error)               # not saved: it does not work
    db.add(s)
    await db.commit()
    return _view(s)


@router.patch("/{sid}")
async def patch(sid: str, body: PatchBody, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    s = await _owned(db, sid, user)
    for f in ("name", "description", "enabled", "allow_actions"):
        v = getattr(body, f)
        if v is not None:
            setattr(s, f, v)
    if body.auth_value is not None:
        _set_auth(s, body.auth_name or (auth_of(s) or {}).get("name") or "Authorization", body.auth_value)
    await db.commit()
    return _view(s)


@router.delete("/{sid}", status_code=204)
async def delete(sid: str, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    await db.delete(await _owned(db, sid, user))
    await db.commit()


@router.post("/{sid}/test")
async def test(sid: str, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    s = await _owned(db, sid, user)
    await _connect(s)
    await db.commit()
    return _view(s)
