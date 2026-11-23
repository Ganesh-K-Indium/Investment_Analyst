"""
Reading engines: which OCR engine reads scans, what is installed, and a way to try each one.

GET  /reading/engines              local OCR engines (installed? models on disk? download state), the reading strategies, and the plan YOUR settings resolve to
POST /reading/engines/{id}/prepare admin: download an installed engine's models into the server's models folder (background; poll the GET)
POST /reading/engines/test         read a small built-in sample page with one local engine, or one vision model (a model test costs a few hundred tokens)

Installing the Python packages themselves stays a deployment step (pip, or the Docker image), as in the standalone extraction app: letting a web
request change the server's own software is not offered. The screen shows the exact command for each engine that is missing.
"""
import asyncio
import logging
import time
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.database.connection import get_db_session
from app.database.models import User
from app.docproc import config as dcfg
from app.docproc.engines.ocr import local, prepare, sample
from app.docproc.engines.providers import credentials as dpc, llm
from app.docproc.engines.strategy import plan as plans
from app.docproc.services.reading import ocr
from app.services import llm_providers, run_context
from app.utils.usage_tracker import track_usage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reading", tags=["Reading engines"])


def _plan_for(settings: Dict[str, Dict[str, Any]]):
    d = settings["documents"]
    return plans.resolve({"local_engine": d["local_engine"], "escalate_below": d["escalate_below"], "token_cap": d["token_cap"],
                          "models": [d["vision_model_primary"], d["vision_model_secondary"]]}, {"strategy": "economy" if d["local_only"] else d["reading_strategy"]})


@router.get("/engines")
async def engines(db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    settings, creds = await run_context.load(db, user.id)
    with llm_providers.use_credentials(creds):
        plan = _plan_for(settings).describe()
    return {"local_only": dcfg.local_only(), "locked": dpc.locked(), "models_dir": str(local.models_dir()),
            "local_engines": [{**e, "prepare": prepare.current(e["id"])} for e in local.listing()],
            "strategies": [dict(s) for s in plans.STRATEGIES if s["id"] != "custom"],
            "selected_engine": settings["documents"]["local_engine"], "plan": plan, "can_prepare": user.role == "admin" and not dcfg.local_only() and not dpc.locked()}


@router.post("/engines/{engine_id}/prepare", status_code=202)
async def prepare_engine(engine_id: str, user: User = Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(403, "Only an administrator can download engine models onto the server.")
    try:
        return prepare.start(engine_id)
    except prepare.Refused as e:
        raise HTTPException(e.status, str(e))


class TestBody(BaseModel):
    kind: str            # "local" | "model"
    id: str              # a local engine id, or a model spec such as openai:gpt-4.1


@router.post("/engines/test")
async def test_engine(body: TestBody, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    jpeg = await asyncio.to_thread(sample.page)
    start = time.perf_counter()
    if body.kind == "local":
        eng = local.get(body.id)
        if eng is None:
            raise HTTPException(404, "No such engine.")
        if not eng.available():
            return {"ok": False, "error": f"{eng.label} is not installed. {eng.install}"}
        try:
            r = await eng.read(jpeg)
        except Exception as e:
            logger.warning("Engine test %s failed: %s", body.id, type(e).__name__)
            return {"ok": False, "error": f"{eng.label} could not read the sample page."}
        return {"ok": True, "seconds": round(time.perf_counter() - start, 2), "text": r.text, "confidence": r.confidence, "score": sample.found(r.text), "tokens": 0}
    if body.kind == "model":
        if not llm.valid_spec(body.id):
            raise HTTPException(422, "Write the model as provider:model, for example openai:gpt-4.1.")
        _, creds = await run_context.load(db, user.id)
        try:
            with llm_providers.use_credentials(creds), track_usage("engine-test") as usage:
                reply = await asyncio.wait_for(llm.complete(body.id, ocr.PROMPT, jpeg, stage="engine_test"), 90)
            text = "\n".join(ocr.parse_lines(reply))
        except llm.LLMError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            logger.warning("Model test %s failed: %s", body.id, type(e).__name__)
            return {"ok": False, "error": "The model did not answer. Check the key, the model name and the network."}
        return {"ok": True, "seconds": round(time.perf_counter() - start, 2), "text": text, "confidence": None, "score": sample.found(text),
                "tokens": sum(e.total_tokens for e in usage.entries)}
    raise HTTPException(422, "kind must be local or model.")
