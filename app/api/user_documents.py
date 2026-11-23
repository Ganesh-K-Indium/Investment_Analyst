"""
User documents: files a user brings (statements, broker notes, models, scans...) read, reviewed and made searchable.

POST   /user-documents                 upload one or more files (multipart); returns per-file status
GET    /user-documents                 my documents plus ones shared with the workspace (filters: status, doc_type, entity, q)
GET    /user-documents/types           the document types a file can be filed under
GET    /user-documents/{id}            one document (poll this for progress while status is processing)
PATCH  /user-documents/{id}            owner: type, entity, period, tags, visibility, label
DELETE /user-documents/{id}            owner: removes the file and everything indexed from it
POST   /user-documents/{id}/reread     owner: read it again (optionally another reading strategy, or a better look at some pages)
GET    /user-documents/{id}/review     what a person still needs to check
POST   /user-documents/{id}/approve    owner: accept the extraction as it stands; only then is the document indexed
POST   /user-documents/{id}/review     owner: correct or confirm items; pages with nothing left to check become searchable
GET    /user-documents/{id}/content    the extracted pages (text, blocks, tables)
GET    /user-documents/{id}/file       the original file;   GET /user-documents/{id}/pdf   the PDF the pages were read from
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, verify_owner
from app.database.connection import get_db_session
from app.database.models import Portfolio, User, UserDocument
from app.services.app_settings import store as settings_store
from app.services.user_docs import chunks as chunkmod, manager, normalize

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/user-documents", tags=["User documents"])

MAX_FILES_PER_REQUEST = 20


def _view(d: UserDocument, me: User) -> Dict[str, Any]:
    stats = {k: v for k, v in (d.stats or {}).items() if not k.startswith("_")}
    return {"id": d.id, "filename": d.filename, "ext": d.ext, "size_bytes": d.size_bytes, "doc_type": d.doc_type, "doc_type_label": manager.DOC_TYPES.get(d.doc_type or "", d.doc_type),
            "label": d.label, "entity": d.entity, "period": d.period, "tags": d.tags or [], "visibility": d.visibility, "status": d.status, "stage": d.stage,
            "progress": d.progress or {}, "error": d.error, "pages": d.pages, "pages_indexed": len(d.indexed_pages or []), "review_pending": d.review_pending,
            "index_policy": d.index_policy, "approved": chunkmod.is_approved(d.confirmed or []), "stats": stats, "reading": d.reading or {}, "has_pdf": manager.source_pdf(d.id) is not None,
            "owner": str(d.user_id) == str(me.id), "created_at": d.created_at.isoformat() if d.created_at else None,
            "updated_at": d.updated_at.isoformat() if d.updated_at else None}


async def _get(db: AsyncSession, doc_id: str, user: User, write: bool = False) -> UserDocument:
    d = await db.get(UserDocument, doc_id)
    if d is None or (str(d.user_id) != str(user.id) and (write or d.visibility != "workspace")):
        raise HTTPException(404, "Document not found")      # not 403: do not reveal that someone else's document exists
    return d


async def _known_entities(db: AsyncSession, user: User) -> List[str]:
    from app.utils.company_mapping import get_ticker
    rows = (await db.execute(select(Portfolio.company_names).where(Portfolio.user_id == str(user.id)))).scalars().all()
    out = []
    for names in rows:
        for n in names or []:
            t = get_ticker(n) or n
            if t and t not in out:
                out.append(str(t).upper() if len(str(t)) <= 6 else str(t))
    return out


@router.get("/types")
async def types(user: User = Depends(get_current_user)):
    return {"types": [{"id": k, "label": v} for k, v in manager.DOC_TYPES.items()],
            "formats": sorted(normalize.SUPPORTED), "native_formats": sorted(normalize.NATIVE)}


@router.post("", status_code=202)
async def upload(files: List[UploadFile] = File(...), doc_type: str = Form("auto"), entity: Optional[str] = Form(None), period: Optional[str] = Form(None),
                 tags: Optional[str] = Form(None), visibility: Optional[str] = Form(None), reading: Optional[str] = Form(None),
                 db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    if not files:
        raise HTTPException(422, "Choose at least one file.")
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(422, f"Upload at most {MAX_FILES_PER_REQUEST} files at a time.")
    if visibility not in (None, "private", "workspace"):
        raise HTTPException(422, "visibility must be private or workspace")
    if reading not in (None, "economy", "balanced", "accuracy"):
        raise HTTPException(422, "reading must be economy, balanced or accuracy")
    settings = await settings_store.effective(db, user.id)
    known = await _known_entities(db, user)
    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]
    results = []
    for f in files:
        data = await f.read(settings["documents"]["max_upload_mb"] * 1024 * 1024 + 1)
        try:
            doc = await manager.create_document(db, user.id, f.filename or "file", data, settings, doc_type=doc_type, entity=entity, period=period,
                                                tags=tag_list, visibility=visibility, reading=reading)
        except manager.DuplicateDocument as e:
            results.append({"filename": f.filename, "ok": False, "duplicate_of": e.existing.id, "error": str(e)})
            continue
        except normalize.UnsupportedFile as e:
            results.append({"filename": f.filename, "ok": False, "error": str(e)})
            continue
        manager.start(doc.id, str(user.id), settings, known)
        results.append({"filename": f.filename, "ok": True, "document": _view(doc, user)})
    return {"results": results}


@router.get("")
async def list_documents(status: Optional[str] = None, doc_type: Optional[str] = None, entity: Optional[str] = None, q: Optional[str] = None,
                         mine: bool = False, limit: int = Query(100, ge=1, le=500), db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    cond = [UserDocument.user_id == str(user.id)] if mine else [or_(UserDocument.user_id == str(user.id), UserDocument.visibility == "workspace")]
    if status:
        cond.append(UserDocument.status.in_(("needs_review", "awaiting_approval")) if status == "needs_review" else UserDocument.status == status)   # "needs_review" = anything waiting on the owner
    if doc_type:
        cond.append(UserDocument.doc_type == doc_type)
    if entity:
        cond.append(UserDocument.entity == entity.upper())
    if q:
        like = f"%{q.strip()}%"
        cond.append(or_(UserDocument.filename.ilike(like), UserDocument.label.ilike(like), UserDocument.entity.ilike(like)))
    rows = (await db.execute(select(UserDocument).where(*cond).order_by(UserDocument.created_at.desc()).limit(limit))).scalars().all()
    return {"documents": [_view(d, user) for d in rows],
            "needs_review": sum(1 for d in rows if d.status in ("needs_review", "awaiting_approval") and str(d.user_id) == str(user.id))}


@router.get("/{doc_id}")
async def get_document(doc_id: str, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    return _view(await _get(db, doc_id, user), user)


class PatchBody(BaseModel):
    doc_type: Optional[str] = None
    entity: Optional[str] = Field(None, max_length=40)
    period: Optional[str] = Field(None, max_length=40)
    tags: Optional[List[str]] = None
    visibility: Optional[str] = None
    label: Optional[str] = Field(None, max_length=200)


@router.patch("/{doc_id}")
async def patch_document(doc_id: str, body: PatchBody, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    d = await _get(db, doc_id, user, write=True)
    if body.doc_type is not None and body.doc_type not in manager.DOC_TYPES:
        raise HTTPException(422, "Unknown document type")
    if body.visibility not in (None, "private", "workspace"):
        raise HTTPException(422, "visibility must be private or workspace")
    await manager.update_metadata(db, d, **body.model_dump(exclude_unset=True))
    return _view(d, user)


@router.delete("/{doc_id}", status_code=204)
async def delete_document(doc_id: str, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    d = await _get(db, doc_id, user, write=True)
    await manager.delete_document(db, d)


class RereadBody(BaseModel):
    reading: Optional[str] = None
    better_pages: List[int] = []


@router.post("/{doc_id}/reread", status_code=202)
async def reread(doc_id: str, body: RereadBody = Body(default_factory=RereadBody), db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    d = await _get(db, doc_id, user, write=True)
    if manager.is_running(d.id):
        raise HTTPException(409, "This document is already being read.")
    if body.reading not in (None, "economy", "balanced", "accuracy"):
        raise HTTPException(422, "reading must be economy, balanced or accuracy")
    settings = await settings_store.effective(db, user.id)
    if body.reading:
        d.reading = {**(d.reading or {}), "strategy": body.reading}
    d.status, d.stage, d.error = "queued", "queued", None
    if settings["documents"].get("require_approval", True):        # a new reading is a new extraction: look at it again
        d.confirmed = [c for c in (d.confirmed or []) if c != chunkmod.APPROVED]
        manager.flag_modified(d, "confirmed")
    await db.commit()
    manager.start(d.id, str(user.id), settings, await _known_entities(db, user), body.better_pages)
    return _view(d, user)


@router.get("/{doc_id}/review")
async def review(doc_id: str, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    d = await _get(db, doc_id, user, write=True)
    result = manager.load_result(d.id)
    if result is None:
        return {"items": [], "pages": [], "status": d.status}
    done = set(d.corrections or {}) | set(d.confirmed or [])
    items = []
    for it in chunkmod.review_items(result, d.corrections or {}):
        fix = (d.corrections or {}).get(it["id"])
        items.append({**it, "resolved": it["id"] in done, "corrected_value": fix["value"] if fix else None})
    skipped = chunkmod.skipped_pages(d.confirmed or [])
    unreadable = chunkmod.unreadable_pages(result)
    by_page: Dict[int, int] = {}
    for it in items:
        if not it["resolved"] and it["page"] not in skipped:
            by_page[it["page"]] = by_page.get(it["page"], 0) + 1
    return {"status": d.status, "policy": d.index_policy, "pending": sum(by_page.values()), "items": items,
            "pages": [{"page": p, "pending": by_page.get(p, 0), "indexed": p in (d.indexed_pages or []), "skipped": p in skipped,
                       "unreadable": p in unreadable and p not in skipped, "doubtful_lines": unreadable.get(p, {}).get("doubtful"), "lines": unreadable.get(p, {}).get("lines")}
                      for p in range(1, d.pages + 1)]}


class ReviewBody(BaseModel):
    edits: Dict[str, str] = {}
    confirm: List[str] = []
    confirm_pages: List[int] = []
    skip_pages: List[int] = []         # leave these pages out of search (a page too poor to read)
    unskip_pages: List[int] = []


@router.post("/{doc_id}/review")
async def apply_review(doc_id: str, body: ReviewBody, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    d = await _get(db, doc_id, user, write=True)
    if d.status == "processing":
        raise HTTPException(409, "Wait until the document has been read.")
    try:
        await manager.apply_review(db, d, body.edits, body.confirm, body.confirm_pages, body.skip_pages, body.unskip_pages)
    except KeyError as e:
        raise HTTPException(422, str(e).strip("'\""))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return _view(d, user)


@router.post("/{doc_id}/approve")
async def approve(doc_id: str, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    """Owner: the extraction looks right; index it so it can be searched."""
    d = await _get(db, doc_id, user, write=True)
    if d.status in ("processing", "queued"):
        raise HTTPException(409, "Wait until the document has been read.")
    try:
        await manager.approve(db, d)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return _view(d, user)


@router.get("/{doc_id}/content")
async def content(doc_id: str, page: Optional[int] = None, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    d = await _get(db, doc_id, user)
    result = manager.load_result(d.id)
    if result is None:
        raise HTTPException(409, "This document has not been read yet.")
    pages = [{"page": p["page"], "source": p.get("source"), "label": (p.get("classification") or {}).get("label"), "text": p.get("text"),
              "blocks": (p.get("structure") or {}).get("blocks", []), "lines": p.get("lines", []), "size": p.get("size"), "handwritten": p.get("handwritten"),
              "reading": p.get("reading")} for p in result["pages"] if page is None or p["page"] == page]
    return {"id": d.id, "filename": d.filename, "pages": pages, "documents": result.get("documents", []), "forms": result.get("forms", []) if page is None else [],
            "validation": result.get("validation", []), "corrections": d.corrections or {}}


@router.get("/{doc_id}/file")
async def original_file(doc_id: str, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    d = await _get(db, doc_id, user)
    path = manager.doc_dir(d.id) / f"original.{d.ext}"
    if not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=d.filename)


@router.get("/{doc_id}/pdf")
async def pdf(doc_id: str, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    d = await _get(db, doc_id, user)
    path = manager.source_pdf(d.id)
    if path is None:
        raise HTTPException(404, "This document has no PDF view (it was read directly from its own format).")
    return FileResponse(path, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{d.filename}.pdf"'})
