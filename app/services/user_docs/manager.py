"""
Ingestion of one user document, end to end, and everything that follows from a person's review.

    save original  ->  normalize (image->PDF | native parse)  ->  engine reads it (progress stored)  ->  classify / label
        ->  review items  ->  index the pages that are ready (policy: after_review | flagged | never)  ->  status

A page is "ready" when it has nothing left for a person to look at. Under the default policy (`after_review`) a page with doubtful
text is held back from search until its items are confirmed or corrected; under `flagged` it is indexed straight away and marked low
confidence until reviewed; under `never` it stays out. Corrections are stored by item id, so re-reading the same file re-applies them.
"""
import asyncio
import hashlib
import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import UserDocument
from app.services import app_settings
from app.services.app_settings import resolver
from . import chunks as chunkmod, normalize, store

logger = logging.getLogger("app.user_docs")

ROOT = Path(__file__).resolve().parents[3] / "data" / "user_docs"
DOC_TYPES = {
    "auto": "Detect automatically", "broker_note": "Broker / research note", "earnings_call": "Earnings call transcript", "financial_statement": "Financial statements",
    "bank_statement": "Bank or brokerage statement", "model": "Financial model / spreadsheet", "pitch_deck": "Pitch deck / presentation", "cim": "Information memorandum (CIM)",
    "credit_agreement": "Credit agreement / contract", "memo": "Internal memo", "filing": "Regulatory filing", "other": "Other",
}
_jobs: Dict[str, "asyncio.Task"] = {}


def doc_dir(doc_id: str) -> Path:
    return ROOT / doc_id


def result_path(doc_id: str) -> Path:
    return doc_dir(doc_id) / "result.json"


def load_result(doc_id: str) -> Optional[Dict[str, Any]]:
    p = result_path(doc_id)
    return json.loads(p.read_text()) if p.is_file() else None


def source_pdf(doc_id: str) -> Optional[Path]:
    p = doc_dir(doc_id) / "source.pdf"
    return p if p.is_file() else None


# ---------------------------------------------------------------------------------------------------------------------
# type guess (rules only: free, explainable; the user can always set it)
# ---------------------------------------------------------------------------------------------------------------------

_TYPE_HINTS = [("earnings_call", ("earnings call", "conference call", "operator:", "prepared remarks", "question-and-answer")),
               ("credit_agreement", ("credit agreement", "borrower", "lender", "covenant", "event of default")),
               ("bank_statement", ("account statement", "statement period", "opening balance", "closing balance", "account number")),
               ("broker_note", ("price target", "overweight", "underweight", "rating:", "analyst", "initiating coverage")),
               ("cim", ("confidential information memorandum", "investment highlights", "transaction overview")),
               ("financial_statement", ("balance sheet", "statements of operations", "income statement", "cash flows", "total liabilities")),
               ("filing", ("securities and exchange commission", "form 10-k", "form 10-q", "form 8-k"))]


def guess_type(ext: str, text: str, label: str = "") -> str:
    low = f"{label}\n{text[:6000]}".lower()
    if ext in ("xlsx", "xlsm", "csv", "tsv"):
        return "model"
    if ext == "pptx":
        return "pitch_deck"
    best, score = "other", 0
    for t, words in _TYPE_HINTS:
        s = sum(1 for w in words if w in low)
        if s > score:
            best, score = t, s
    return best if score >= 2 else ("memo" if ext in ("docx", "txt", "md") else "other")


def guess_entity(text: str, known: List[str]) -> Optional[str]:
    """A ticker or company the document is about: the one named most often among `known` (the user's portfolio companies)."""
    low = text.lower()
    best, n = None, 0
    for k in known:
        c = low.count(k.lower()) if len(k) > 2 else 0
        if c > n:
            best, n = k, c
    return best


# ---------------------------------------------------------------------------------------------------------------------
# state helpers
# ---------------------------------------------------------------------------------------------------------------------

async def _set(db: AsyncSession, doc: UserDocument, **kw) -> None:
    for k, v in kw.items():
        setattr(doc, k, v)
    await db.commit()


def base_meta(doc: UserDocument, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"doc_id": doc.id, "owner_id": doc.user_id, "visibility": doc.visibility, "filename": doc.filename, "doc_type": doc.doc_type,
            "doc_type_label": DOC_TYPES.get(doc.doc_type or "", doc.doc_type), "entity": doc.entity, "ticker": doc.entity, "company": doc.entity,
            "period": doc.period, "tags": doc.tags or [], "label": doc.label, "ext": doc.ext}


async def sync_index(db: AsyncSession, doc: UserDocument, result: Optional[Dict[str, Any]] = None) -> UserDocument:
    """Bring the index in line with the review state: index the pages that are ready, hold back (or flag) the rest, re-index a page
    whose items were just resolved. Safe to call any number of times."""
    result = result or load_result(doc.id)
    if result is None:
        return doc
    items = chunkmod.review_items(result)
    pending = chunkmod.pending_by_page(items, doc.corrections or {}, doc.confirmed or [])
    pages = [p["page"] for p in result["pages"] if (p.get("text") or "").strip()]
    policy = doc.index_policy or "after_review"
    meta = base_meta(doc, result)
    indexed = set(doc.indexed_pages or [])
    total = 0
    for pg in pages:
        waiting = pg in pending
        if waiting and policy == "never":
            continue
        want_state = "flagged" if waiting else "confirmed"
        stamp = f"{pg}:{want_state}:{len(doc.corrections or {})}"
        if pg in indexed and (doc.stats or {}).get("_stamps", {}).get(str(pg)) == stamp:
            continue
        if waiting and policy == "after_review":
            continue
        cs = chunkmod.page_chunks(result, pg, meta, doc.corrections or {}, review_state=want_state)
        await store.delete_page(doc.id, pg)
        total += await store.index_chunks(cs)
        indexed.add(pg)
        stats = dict(doc.stats or {})
        stats.setdefault("_stamps", {})[str(pg)] = stamp
        doc.stats = stats
    # pages that fell back into review (policy changed) leave the index again
    for pg in sorted(indexed):
        if pg in pending and policy in ("after_review", "never"):
            await store.delete_page(doc.id, pg)
            indexed.discard(pg)
    n_pending = sum(len(v) for v in pending.values())
    status = "ready" if not n_pending else "needs_review"
    if not pages:
        status = "failed"
    doc.indexed_pages = sorted(indexed)
    doc.review_pending = n_pending
    doc.status = status
    doc.stage = "done"
    doc.updated_at = datetime.utcnow()
    flag_modified(doc, "stats"); flag_modified(doc, "indexed_pages")
    await db.commit()
    return doc


def flag_modified(obj, attr):
    from sqlalchemy.orm.attributes import flag_modified as fm
    fm(obj, attr)


# ---------------------------------------------------------------------------------------------------------------------
# ingestion job
# ---------------------------------------------------------------------------------------------------------------------

async def create_document(db: AsyncSession, user_id: Any, filename: str, data: bytes, settings: Dict[str, Dict[str, Any]], *, doc_type: str = "auto",
                          entity: Optional[str] = None, period: Optional[str] = None, tags: Optional[List[str]] = None,
                          visibility: Optional[str] = None, reading: Optional[str] = None) -> UserDocument:
    d = settings["documents"]
    if len(data) > d["max_upload_mb"] * 1024 * 1024:
        raise normalize.UnsupportedFile(f"The file is larger than {d['max_upload_mb']} MB (your limit in Settings > Your documents).")
    if not data:
        raise normalize.UnsupportedFile("The file is empty.")
    ext = normalize.sniff(filename, data[:16])
    if doc_type not in DOC_TYPES:
        raise normalize.UnsupportedFile(f"Unknown document type {doc_type!r}.")
    sha = hashlib.sha256(data).hexdigest()
    dup = (await db.execute(select(UserDocument).where(UserDocument.user_id == str(user_id), UserDocument.sha256 == sha, UserDocument.status != "failed"))).scalars().first()
    if dup:
        raise DuplicateDocument(dup)
    doc = UserDocument(id=uuid.uuid4().hex, user_id=str(user_id), filename=Path(filename).name[:255], ext=ext, sha256=sha, size_bytes=len(data), doc_type=None if doc_type == "auto" else doc_type,
                       entity=(entity or "").strip().upper() or None, period=(period or "").strip() or None, tags=list(tags or []),
                       visibility=visibility or d["default_visibility"], status="queued", stage="queued", progress={"done": 0, "total": 0},
                       index_policy=d["index_policy"], indexed_pages=[], corrections={}, confirmed=[], stats={},
                       reading={"strategy": reading or d["reading_strategy"]})
    db.add(doc)
    await db.commit()
    doc_dir(doc.id).mkdir(parents=True, exist_ok=True)
    (doc_dir(doc.id) / f"original.{ext}").write_bytes(data)
    return doc


class DuplicateDocument(Exception):
    def __init__(self, existing: UserDocument):
        super().__init__(f"You already uploaded this file as {existing.filename!r}.")
        self.existing = existing


def start(doc_id: str, user_id: str, settings: Dict[str, Dict[str, Any]], known_entities: Optional[List[str]] = None, force_vision_pages: Optional[List[int]] = None) -> None:
    """Run the ingestion in the background (this process). A job per document; one document never blocks another."""
    task = asyncio.create_task(_run(doc_id, settings, known_entities or [], force_vision_pages or []))
    _jobs[doc_id] = task
    task.add_done_callback(lambda t: _jobs.pop(doc_id, None))


def is_running(doc_id: str) -> bool:
    return doc_id in _jobs


async def _run(doc_id: str, settings: Dict[str, Dict[str, Any]], known: List[str], force_vision: List[int]) -> None:
    from app.database.connection import SessionLocal
    async with SessionLocal() as db:
        doc = await db.get(UserDocument, doc_id)
        if doc is None:
            return
        try:
            from app.services import llm_providers
            from app.services.llm_providers import service as provider_service
            creds = await provider_service.credentials_for(db, doc.user_id)          # the owner's own keys, also in the background
            with app_settings.use(settings), llm_providers.use_credentials(creds):
                await _ingest(db, doc, settings, known, force_vision)
        except Exception as e:  # noqa: BLE001
            logger.exception("Ingesting %s failed", doc_id)
            msg = str(e) if isinstance(e, (normalize.UnsupportedFile,)) or e.__class__.__name__ == "OcrError" else "This file could not be read. Try another copy or format."
            await _set(db, doc, status="failed", stage="failed", error=msg)


async def _ingest(db: AsyncSession, doc: UserDocument, settings: Dict[str, Dict[str, Any]], known: List[str], force_vision: List[int]) -> None:
    await _set(db, doc, status="processing", stage="reading", error=None, progress={"done": 0, "total": 0})
    d = doc_dir(doc.id)
    original = d / f"original.{doc.ext}"
    if doc.ext in normalize.NATIVE:
        result = await asyncio.to_thread(normalize.read_native, str(original), doc.ext)
        if not result["pages"]:
            raise normalize.UnsupportedFile("No readable content was found in this file.")
        await _set(db, doc, progress={"done": len(result["pages"]), "total": len(result["pages"])})
    else:
        pdf = d / "source.pdf"
        if doc.ext == "pdf":
            shutil.copyfile(original, pdf)
        else:
            await asyncio.to_thread(normalize.image_to_pdf, str(original), str(pdf))
        result = await _read_pdf(db, doc, str(pdf), settings, force_vision)
    await _set(db, doc, stage="classifying")
    text = "\n".join(p.get("text", "") for p in result["pages"][:6])
    label = next((dd.get("label") for dd in result.get("documents", []) if dd.get("label") and dd["label"] not in ("Unclassified", "Other document", "Document")), None) or \
            (result["pages"][0]["classification"].get("label") if result["pages"] else None)
    result_path(doc.id).write_text(json.dumps(result, default=str))
    doc.label = doc.label or label
    doc.doc_type = doc.doc_type or guess_type(doc.ext, text, label or "")
    doc.entity = doc.entity or guess_entity(text, known)
    doc.pages = len(result["pages"])
    doc.reading = result.get("reading") or doc.reading
    st = dict(result.get("stats") or {})
    st["_stamps"] = {}
    doc.stats = st
    await db.commit()
    await _set(db, doc, stage="indexing")
    await sync_index(db, doc, result)


async def _read_pdf(db: AsyncSession, doc: UserDocument, pdf: str, settings: Dict[str, Dict[str, Any]], force_vision: List[int]) -> Dict[str, Any]:
    from app.docproc import pipeline
    from app.docproc.engines.strategy import plan as plans
    d = settings["documents"]
    strategy = (doc.reading or {}).get("strategy") or d["reading_strategy"]
    if d["local_only"]:
        strategy = "economy"
    plan = plans.resolve({"escalate_below": d["escalate_below"], "token_cap": d["token_cap"],
                          "models": [d["vision_model_primary"], d["vision_model_secondary"]]}, {"strategy": strategy})
    loop = asyncio.get_running_loop()
    state = {"stage": "reading", "done": 0, "total": 0}

    def progress(stage: str, done: int, total: int):
        state.update(stage=stage, done=done, total=total)
        asyncio.run_coroutine_threadsafe(_progress(doc.id, stage, done, total), loop)

    tmpl = "auto" if (d["auto_detect_type"] or not doc.doc_type) else "discover"
    return await pipeline.process(pdf, progress=progress, template_choice=tmpl, plan=plan, force_vision=force_vision)


async def _progress(doc_id: str, stage: str, done: int, total: int) -> None:
    from app.database.connection import SessionLocal
    try:
        async with SessionLocal() as db:
            doc = await db.get(UserDocument, doc_id)
            if doc and doc.status == "processing":
                doc.stage, doc.progress = {"pages": "reading", "preparing": "preparing", "documents": "extracting", "rest": "extracting"}.get(stage, stage), {"done": done, "total": total}
                await db.commit()
    except Exception:
        logger.debug("progress update skipped", exc_info=True)


# ---------------------------------------------------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------------------------------------------------

async def apply_review(db: AsyncSession, doc: UserDocument, edits: Dict[str, Any], confirm: List[str], confirm_pages: List[int]) -> UserDocument:
    """`edits` {item id: new value}; `confirm` item ids accepted as they are; `confirm_pages` accepts every pending item on those pages."""
    result = load_result(doc.id)
    if result is None:
        raise ValueError("This document has no extraction to review.")
    items = {i["id"]: i for i in chunkmod.review_items(result)}
    bad = [k for k in list(edits) + list(confirm) if k not in items]
    if bad:
        raise KeyError(f"unknown review items: {bad[:5]}")
    corr = dict(doc.corrections or {})
    conf = set(doc.confirmed or [])
    for k, v in edits.items():
        corr[k] = {"original": items[k]["value"], "value": v, "at": datetime.utcnow().isoformat()}
        conf.discard(k)
    for k in confirm:
        conf.add(k)
    for pg in confirm_pages:
        for k, it in items.items():
            if it["page"] == pg and k not in corr:
                conf.add(k)
    doc.corrections, doc.confirmed = corr, sorted(conf)
    flag_modified(doc, "corrections"); flag_modified(doc, "confirmed")
    await db.commit()
    return await sync_index(db, doc, result)


async def delete_document(db: AsyncSession, doc: UserDocument) -> None:
    t = _jobs.pop(doc.id, None)
    if t:
        t.cancel()
    await store.delete_document(doc.id)
    shutil.rmtree(doc_dir(doc.id), ignore_errors=True)
    await db.delete(doc)
    await db.commit()


async def update_metadata(db: AsyncSession, doc: UserDocument, **fields: Any) -> UserDocument:
    """Change type / entity / period / tags / visibility. Visibility, type and entity go to the stored chunks without re-embedding."""
    payload: Dict[str, Any] = {}
    for k in ("doc_type", "entity", "period", "tags", "visibility", "label"):
        if k in fields and fields[k] is not None:
            v = fields[k]
            if k == "entity":
                v = (v or "").strip().upper() or None
            setattr(doc, k, v)
            if k in ("doc_type", "entity", "visibility", "period"):
                payload[k] = v
    if "entity" in payload:
        payload["ticker"] = payload["company"] = payload["entity"]
    if "doc_type" in payload:
        payload["doc_type_label"] = DOC_TYPES.get(payload["doc_type"], payload["doc_type"])
    await db.commit()
    if payload and doc.indexed_pages:
        await store.set_metadata(doc.id, payload)
    return doc
