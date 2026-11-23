"""
Settings endpoints (see app/services/app_settings)

GET    /settings/schema                  — every option with its type, default, bounds and help (the UI renders forms from this)
GET    /settings                         — my saved layer, plus the effective (resolved) settings
PUT    /settings                         — save part of my layer (validated; unknown keys are errors)
DELETE /settings                         — reset my whole layer, or one section with ?section=
GET    /settings/portfolio/{id}          — the per-portfolio layer (owner only) / PUT / DELETE the same way
GET    /settings/system   PUT /settings/system — admin: defaults for everyone, allowed models
GET    /settings/usage                   — tokens and estimated cost this month, against the budget
"""
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_role, verify_owner
from app.database.connection import get_db_session
from app.database.models import Portfolio, User
from app.services.app_settings import schema, store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["Settings"])


def _bad(e: schema.SettingsError) -> HTTPException:
    return HTTPException(422, str(e))


async def _portfolio(db: AsyncSession, portfolio_id: int, user: User) -> Portfolio:
    p = await db.get(Portfolio, portfolio_id)
    if p is None:
        raise HTTPException(404, "Portfolio not found")
    verify_owner(p.user_id, user)
    return p


@router.get("/schema")
async def get_schema(db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    return schema.ui_schema(await store.allowed_models(db))


@router.get("")
async def get_settings(portfolio_id: Optional[int] = Query(None), db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    if portfolio_id is not None:
        await _portfolio(db, portfolio_id, user)
    return {"saved": await store.get_layer(db, "user", str(user.id)),
            "effective": await store.effective(db, user.id, portfolio_id)}


@router.put("")
async def put_settings(patch: Dict[str, Any] = Body(...), db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    try:
        saved = await store.save_layer(db, "user", str(user.id), patch)
    except schema.SettingsError as e:
        raise _bad(e)
    return {"saved": saved, "effective": await store.effective(db, user.id)}


@router.delete("")
async def reset_settings(section: Optional[str] = Query(None), db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    if section and section not in schema.SECTIONS:
        raise HTTPException(422, f"unknown section {section!r}")
    saved = await store.reset_layer(db, "user", str(user.id), section)
    return {"saved": saved, "effective": await store.effective(db, user.id)}


@router.get("/portfolio/{portfolio_id}")
async def get_portfolio_settings(portfolio_id: int, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    await _portfolio(db, portfolio_id, user)
    return {"saved": await store.get_layer(db, "portfolio", str(portfolio_id)), "effective": await store.effective(db, user.id, portfolio_id)}


@router.put("/portfolio/{portfolio_id}")
async def put_portfolio_settings(portfolio_id: int, patch: Dict[str, Any] = Body(...), db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    await _portfolio(db, portfolio_id, user)
    try:
        saved = await store.save_layer(db, "portfolio", str(portfolio_id), patch)
    except schema.SettingsError as e:
        raise _bad(e)
    return {"saved": saved, "effective": await store.effective(db, user.id, portfolio_id)}


@router.delete("/portfolio/{portfolio_id}")
async def reset_portfolio_settings(portfolio_id: int, section: Optional[str] = Query(None), db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    await _portfolio(db, portfolio_id, user)
    saved = await store.reset_layer(db, "portfolio", str(portfolio_id), section)
    return {"saved": saved, "effective": await store.effective(db, user.id, portfolio_id)}


@router.get("/system")
async def get_system(db: AsyncSession = Depends(get_db_session), user: User = Depends(require_role("admin"))):
    return {"saved": await store.get_layer(db, "system", store.SYSTEM_SCOPE_ID), "allowed_models": await store.allowed_models(db)}


@router.put("/system")
async def put_system(patch: Dict[str, Any] = Body(...), db: AsyncSession = Depends(get_db_session), user: User = Depends(require_role("admin"))):
    """Admin defaults. `models.allowed_models` (a list of model names) narrows or extends what users may pick."""
    patch = dict(patch)
    allowed = None
    if isinstance(patch.get("models"), dict) and "allowed_models" in patch["models"]:
        models = dict(patch["models"])
        allowed = models.pop("allowed_models")
        if not isinstance(allowed, list) or not all(isinstance(m, str) and m for m in allowed):
            raise HTTPException(422, "models.allowed_models must be a list of model names")
        patch["models"] = models
    try:
        saved = await store.save_layer(db, "system", store.SYSTEM_SCOPE_ID, patch)
    except schema.SettingsError as e:
        raise _bad(e)
    if allowed is not None:
        from app.database.models import SettingsRecord
        import copy
        row = await db.get(SettingsRecord, ("system", store.SYSTEM_SCOPE_ID))
        data = copy.deepcopy(row.data or {})          # a copy, so the JSON column sees a changed value and writes it
        data.setdefault("models", {})["allowed_models"] = allowed
        row.data = data
        await db.commit()
        saved = data
    return {"saved": saved}


@router.get("/prompts")
async def list_prompts(user: User = Depends(get_current_user)):
    """The prompts a user can override, each with its built-in text (the prompt editor's starting point). Save overrides with
    PUT /settings {"prompts": {"overrides": {"<name>": "<text>"}}}; remove one by omitting it (PUT the full overrides object)."""
    from rag.prompts import registry
    return {"prompts": registry.listing()}


@router.get("/usage")
async def usage(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    from app.services import usage_ledger
    eff = await store.effective(db, user.id)
    return await usage_ledger.month_summary(db, str(user.id), eff["models"]["monthly_token_budget"])


@router.get("/frameworks/template")
async def framework_template(user: User = Depends(get_current_user)):
    """A starting point for a user-defined analysis framework (the Frameworks page's "new framework")."""
    return {"id": "my-framework", "name": "My framework", "description": "", "triggers": ["my framework"], "enabled": True,
            "sections": ["Business quality", "Balance sheet", "Valuation", "Risks"], "sources": ["filings", "notes"],
            "prompt": "You are a rigorous equity analyst. Assess the company on each heading using only the evidence provided."}
