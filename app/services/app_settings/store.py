"""Persistence for settings layers (user, portfolio, system) and resolution of the effective set for a request."""
import logging
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import SettingsRecord
from . import resolver, schema

logger = logging.getLogger("app.settings.store")

SYSTEM_SCOPE_ID = "system"


async def get_layer(db: AsyncSession, scope: str, scope_id: str) -> Dict[str, Dict[str, Any]]:
    row = await db.get(SettingsRecord, (scope, str(scope_id)))
    return dict(row.data or {}) if row else {}


async def save_layer(db: AsyncSession, scope: str, scope_id: str, patch: Dict[str, Any], replace: bool = False) -> Dict[str, Dict[str, Any]]:
    """Validate `patch` and merge it into the stored layer (or replace the layer). Returns the stored layer."""
    clean = schema.validate(patch, allowed_models=await allowed_models(db))
    row = await db.get(SettingsRecord, (scope, str(scope_id)))
    base = {} if (replace or row is None) else dict(row.data or {})
    merged = resolver.merge(base, clean)
    if row is None:
        db.add(SettingsRecord(scope=scope, scope_id=str(scope_id), data=merged, version=1))
    else:
        row.data, row.version = merged, (row.version or 0) + 1
    await db.commit()
    return merged


async def reset_layer(db: AsyncSession, scope: str, scope_id: str, section: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    row = await db.get(SettingsRecord, (scope, str(scope_id)))
    if row is None:
        return {}
    data = dict(row.data or {})
    if section:
        data.pop(section, None)
    else:
        data = {}
    row.data, row.version = data, (row.version or 0) + 1
    await db.commit()
    return data


async def allowed_models(db: AsyncSession) -> list:
    """The models users may pick: the built-in gpt-4-series list, extended or narrowed by an admin through the system layer."""
    system = await get_layer(db, "system", SYSTEM_SCOPE_ID)
    extra = (system.get("models") or {}).get("allowed_models")
    return list(extra) if isinstance(extra, list) and extra else schema.ALLOWED_MODELS


async def effective(db: AsyncSession, user_id: Any, portfolio_id: Any = None, override: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """Defaults -> system -> user -> portfolio -> per-request override. A saved value that is no longer valid (e.g. a model an admin
    removed) is replaced by its default rather than failing the request."""
    layers = [await get_layer(db, "system", SYSTEM_SCOPE_ID), await get_layer(db, "user", str(user_id))]
    if portfolio_id is not None:
        layers.append(await get_layer(db, "portfolio", str(portfolio_id)))
    if override:
        try:
            layers.append(schema.validate(override, allowed_models=await allowed_models(db)))
        except schema.SettingsError as e:
            logger.warning("Ignoring invalid per-request settings override: %s", e)
    out = resolver.resolve(*layers)
    allowed = await allowed_models(db)
    for key, spec in schema.SECTIONS["models"]["fields"].items():
        if spec["type"] == "model" and out["models"].get(key) not in allowed:
            out["models"][key] = spec["default"] if spec["default"] in allowed else allowed[0]
    return out
