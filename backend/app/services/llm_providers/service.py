"""Saved provider credentials (per user, with an optional workspace-wide layer), connection tests, and model discovery."""
import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ProviderCredential
from app.docproc.engines.providers import credentials as dpc
from . import context, factory, urls
from .catalog import EMBEDDINGS, PROVIDERS, defaults_for, fields_of, installed, split_spec

logger = logging.getLogger("app.llm_providers")
WORKSPACE = "__workspace__"


def _owner_of(name: str) -> Optional[str]:
    return next((pid for pid, fs in FIELDS_BY_PROVIDER().items() if name in {f.name for f in fs}), None)


def FIELDS_BY_PROVIDER():
    return {pid: fields_of(pid) for pid in PROVIDERS}


# ---------------------------------------------------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------------------------------------------------

async def _layer(db: AsyncSession, owner: str) -> Dict[str, str]:
    rows = (await db.execute(select(ProviderCredential).where(ProviderCredential.user_id == owner))).scalars().all()
    out: Dict[str, str] = {}
    for r in rows:
        v = dpc._decrypt(r.value_enc)          # None when the encryption key changed: treated as not set, never a crash
        if v:
            out[r.name] = v
    return out


async def credentials_for(db: AsyncSession, user_id: Any) -> Dict[str, str]:
    """Saved values in force for this user: the workspace layer, overridden by the user's own. (The environment is the last fallback,
    applied by context.get.)"""
    return {**await _layer(db, WORKSPACE), **await _layer(db, str(user_id))}


async def save(db: AsyncSession, owner: str, provider: str, values: Dict[str, Optional[str]]) -> None:
    """Set (a string) or remove (None) saved values of one provider for `owner`. ValueError: bad input. StorageError: cannot encrypt."""
    names = {f.name for f in fields_of(provider)}
    cleaned: Dict[str, Optional[str]] = {}
    for name, v in values.items():
        if name not in names:
            raise ValueError("That setting does not belong to this provider.")
        if v is None:
            cleaned[name] = None
            continue
        c = dpc.validate(name, v)
        if next(f for f in fields_of(provider) if f.name == name).kind == "url":
            c = urls.check(c)
        cleaned[name] = c
    if any(v is not None for v in cleaned.values()):
        dpc._fernet()                           # fail before touching the database when nothing can be encrypted
    for name, v in cleaned.items():
        row = await db.get(ProviderCredential, (owner, name))
        if v is None:
            if row is not None:
                await db.delete(row)
        elif row is None:
            db.add(ProviderCredential(user_id=owner, name=name, value_enc=dpc._encrypt(v)))
        else:
            row.value_enc = dpc._encrypt(v)
    await db.commit()


async def remove_provider(db: AsyncSession, owner: str, provider: str) -> None:
    await db.execute(delete(ProviderCredential).where(ProviderCredential.user_id == owner, ProviderCredential.name.in_([f.name for f in fields_of(provider)])))
    await db.commit()


# ---------------------------------------------------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------------------------------------------------

def _preview(f, value: str) -> str:
    return ("••••••••" + value[-4:]) if f.secret and len(value) > 8 else ("••••••••" if f.secret else value)


async def providers_view(db: AsyncSession, user_id: Any) -> Dict[str, Any]:
    mine, shared = await _layer(db, str(user_id)), await _layer(db, WORKSPACE)
    out = []
    with context.use({**shared, **mine}):
        for p in PROVIDERS.values():
            fields = []
            for f in fields_of(p.id):
                if f.name in mine:
                    src, val = "yours", mine[f.name]
                elif f.name in shared:
                    src, val = "workspace", shared[f.name]
                else:
                    val = context.get(f.name)
                    src = "server" if val else None
                fields.append({"name": f.name, "label": f.label, "secret": f.secret, "kind": f.kind, "help": f.help, "set": bool(val), "source": src,
                               "preview": _preview(f, val) if val else ""})
            ok = factory.configured(p.id)
            out.append({"id": p.id, "label": p.label, "cloud": p.cloud, "hint": p.hint, "caveat": p.caveat, "installed": installed(p), "configured": ok,
                        "source": next((f["source"] for f in fields if f["set"] and f["source"]), None) if ok else None, "fields": fields,
                        "models": list(p.models), "defaults": defaults_for(p.id), "vision": p.vision, "presets": [{"label": a, "url": b} for a, b in p.presets]})
    from app.docproc import config as dcfg
    return {"providers": out, "embeddings": EMBEDDINGS, "local_only": dcfg.local_only(), "locked": dpc.locked(), "encryption_ok": dpc.encryption_ok()}


def missing_for(models: Dict[str, Any]) -> List[Dict[str, str]]:
    """Jobs whose model belongs to a provider this request has no credentials for, so a run can fail early with a clear message
    instead of deep inside the graph. Call inside `context.use(...)`."""
    out = []
    for key, spec in models.items():
        if not key.endswith("_model") or not isinstance(spec, str):
            continue
        provider, _ = split_spec(spec)
        if not factory.configured(provider):
            out.append({"job": key[:-6], "provider": provider, "spec": spec})
    return out


def missing_message(missing: List[Dict[str, str]]) -> str:
    by: Dict[str, List[str]] = {}
    for m in missing:
        by.setdefault(m["provider"], []).append(m["job"].replace("_", " "))
    parts = [f"{PROVIDERS[p].label if p in PROVIDERS else p} (used for {', '.join(sorted(set(jobs)))})" for p, jobs in by.items()]
    return "Some models are set to providers that are not connected: " + "; ".join(parts) + ". Connect them under Settings, AI providers, or choose other models."


# ---------------------------------------------------------------------------------------------------------------------
# test and discovery
# ---------------------------------------------------------------------------------------------------------------------

def _scrub(text: str, secrets: List[str]) -> str:
    for s in secrets:
        if s and len(s) > 6:
            text = text.replace(s, "••••")
    return re.sub(r"(sk-[A-Za-z0-9_\-]{6,}|AIza[0-9A-Za-z_\-]{10,})", "••••", text)[:400]


async def test(provider: str, model: str, unsaved: Dict[str, str], base: Dict[str, str]) -> Dict[str, Any]:
    """Make one tiny request with the given values (unsaved ones win over saved) and say whether it worked. Never echoes a key."""
    creds = {**base, **{k: v for k, v in unsaved.items() if v}}
    secrets = list(creds.values())
    p = PROVIDERS[provider]
    model = model or p.light       # Ollama and self-hosted servers have no default: only the user knows which model is installed
    if not model:
        return {"ok": False, "message": "Choose or enter a model name to test with."}

    def run():
        with context.use(creds):
            llm = factory.build(model if provider == "openai" else f"{provider}:{model}", temperature=0, timeout=20, max_retries=0, max_tokens=16)
            return llm.invoke("Reply with the single word: ok").content

    start = time.perf_counter()
    try:
        reply = await asyncio.wait_for(asyncio.to_thread(run), timeout=30)
    except factory.ProviderNotConfigured as e:
        return {"ok": False, "message": str(e)}
    except urls.UnsafeURL as e:
        return {"ok": False, "message": str(e)}
    except asyncio.TimeoutError:
        return {"ok": False, "message": "The provider did not answer within 30 seconds."}
    except Exception as e:  # noqa: BLE001
        logger.info("provider test failed for %s: %s", provider, type(e).__name__)
        return {"ok": False, "message": _scrub(f"{type(e).__name__}: {e}", secrets)}
    return {"ok": True, "latency_ms": int((time.perf_counter() - start) * 1000), "model": model, "message": f"Connected. The model replied: {str(reply)[:40]}"}


async def list_models(provider: str, creds: Dict[str, str]) -> List[str]:
    """Model names the provider's server reports (Ollama, OpenAI-compatible servers, OpenAI). Best effort: [] when it cannot say."""
    with context.use(creds):
        g = context.get
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as c:
                if provider == "ollama" and g("OLLAMA_BASE_URL"):
                    r = await c.get(f"{urls.check(g('OLLAMA_BASE_URL'))}/api/tags")
                    return sorted(m["name"] for m in r.json().get("models", []))
                if provider == "custom" and g("LLM_CUSTOM_BASE_URL"):
                    h = {"Authorization": f"Bearer {g('LLM_CUSTOM_API_KEY')}"} if g("LLM_CUSTOM_API_KEY") else {}
                    r = await c.get(f"{urls.check(g('LLM_CUSTOM_BASE_URL'))}/models", headers=h)
                    return sorted(m["id"] for m in r.json().get("data", []))
                if provider == "openai" and g("OPENAI_API_KEY"):
                    base = urls.check(g("OPENAI_BASE_URL")) if g("OPENAI_BASE_URL") else "https://api.openai.com/v1"
                    r = await c.get(f"{base}/models", headers={"Authorization": f"Bearer {g('OPENAI_API_KEY')}"})
                    return sorted(m["id"] for m in r.json().get("data", []) if m["id"].startswith(("gpt-", "o")))
        except Exception as e:  # noqa: BLE001
            logger.info("model listing failed for %s: %s", provider, type(e).__name__)
    return []
