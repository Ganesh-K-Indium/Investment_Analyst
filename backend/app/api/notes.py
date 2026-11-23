"""
Analyst notes endpoints (see app/services/analyst_notes.py)

GET    /notes            — the caller's own notes plus notes other analysts shared (and have not paused)
POST   /notes/transcribe — read a handwritten/scanned note (image or PDF) into a REVIEWABLE draft; saves nothing
POST   /notes            — create + index a note (owner = the authenticated user, never taken from the body)
GET    /notes/{note_id}  — one note (owner, or anyone when shared)
PATCH  /notes/{note_id}  — owner only: edit text/title/ticker/date, flip private <-> shared, or pause/resume (`active`)
PUT    /notes/{note_id}/inclusion — another analyst's shared note: opt in/out of using it in MY analysis
DELETE /notes/{note_id}  — owner only
"""
import logging
import time
import uuid
from collections import defaultdict, deque
from datetime import date, datetime
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, verify_owner
from app.database.connection import get_db_session
from app.database.models import AnalystNote, AnalystNoteInclusion, User
from app.services import analyst_notes as notes_service
from app.services import handwriting_ocr as ocr

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notes", tags=["Analyst Notes"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

Visibility = Literal["private", "shared"]
InputMethod = Literal["typed", "handwritten"]


def _clean_text(v: str, field: str, max_len: int) -> str:
    v = (v or "").strip()
    if not v:
        raise ValueError(f"{field} must not be empty")
    if len(v) > max_len:
        raise ValueError(f"{field} must be at most {max_len} characters")
    return v


class NoteCreate(BaseModel):
    ticker: str
    title: str
    content: str
    note_date: Optional[date] = None
    visibility: Visibility = "private"
    input_method: InputMethod = "typed"  # "handwritten" only after the user reviewed a transcription draft

    @field_validator("ticker")
    @classmethod
    def _ticker(cls, v: str) -> str:
        return notes_service.normalize_ticker(v)

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        return _clean_text(v, "title", notes_service.MAX_TITLE_CHARS)

    @field_validator("content")
    @classmethod
    def _content(cls, v: str) -> str:
        return _clean_text(v, "content", notes_service.MAX_CONTENT_CHARS)


class NoteUpdate(BaseModel):
    ticker: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    note_date: Optional[date] = None
    visibility: Optional[Visibility] = None
    active: Optional[bool] = None  # False = "hide for now": stays stored, out of everyone's analysis

    @field_validator("ticker")
    @classmethod
    def _ticker(cls, v):
        return None if v is None else notes_service.normalize_ticker(v)

    @field_validator("title")
    @classmethod
    def _title(cls, v):
        return None if v is None else _clean_text(v, "title", notes_service.MAX_TITLE_CHARS)

    @field_validator("content")
    @classmethod
    def _content(cls, v):
        return None if v is None else _clean_text(v, "content", notes_service.MAX_CONTENT_CHARS)


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    note_id: str
    ticker: str
    title: str
    note_date: date
    visibility: Visibility
    input_method: InputMethod = "typed"
    author: str
    content: str
    chunk_count: int
    created_at: datetime
    updated_at: datetime
    is_owner: bool = False  # set per caller in _out(); not an attribute of the row
    active: bool = True  # the owner's pause flag (False = hidden for now)
    included: bool = False  # someone else's shared note: has the CALLER opted in to using it?
    used_in_analysis: bool = False  # will this note inform the CALLER's analysis right now?


def _out(note: AnalystNote, user: User, included_ids=frozenset()) -> NoteOut:
    out = NoteOut.model_validate(note)
    out.is_owner = str(note.owner_id) == str(user.id)
    out.active = note.active is not False
    out.included = (not out.is_owner) and note.note_id in included_ids
    out.used_in_analysis = out.active and (out.is_owner or out.included)
    return out


async def _included_ids(db: AsyncSession, user: User) -> set:
    rows = await db.execute(select(AnalystNoteInclusion.note_id).where(AnalystNoteInclusion.user_id == str(user.id)))
    return {r[0] for r in rows}


def _display_name(user: User) -> str:
    return user.full_name or user.username


async def _get_readable(db: AsyncSession, note_id: str, user: User) -> AnalystNote:
    """The note if the caller owns it, or it is shared and not paused; otherwise 404 (existence is not leaked)."""
    note = await db.get(AnalystNote, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Not found")
    if str(note.owner_id) != str(user.id) and (note.visibility != notes_service.SHARED or note.active is False):
        raise HTTPException(status_code=404, detail="Not found")
    return note


async def _get_owned(db: AsyncSession, note_id: str, user: User) -> AnalystNote:
    note = await db.get(AnalystNote, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Not found")
    verify_owner(note.owner_id, user)  # 404 for non-owners, even when the note is shared
    return note


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

# Each transcription is a paid model call per page, so cap them per user (in-process; resets on restart).
_TRANSCRIBE_LIMIT, _TRANSCRIBE_WINDOW_SECONDS = 10, 600
_transcribe_calls: Dict[str, deque] = defaultdict(deque)


def _check_transcribe_rate(user_id) -> None:
    now, calls = time.monotonic(), _transcribe_calls[str(user_id)]
    while calls and now - calls[0] > _TRANSCRIBE_WINDOW_SECONDS:
        calls.popleft()
    if len(calls) >= _TRANSCRIBE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many transcriptions in a short time. Please wait a few minutes.")
    calls.append(now)


@router.post("/transcribe")
async def transcribe_note(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """Draft text for a handwritten or scanned note. Lines the two readers disagree on come back flagged for the user
    to fix. Nothing is stored or indexed here; the user saves the reviewed text through POST /notes."""
    if not ocr.is_enabled():
        raise HTTPException(status_code=503, detail="Handwriting transcription is not enabled on this server.")
    data = await file.read(ocr.MAX_UPLOAD_BYTES + 1)  # never buffer more than one byte past the limit
    _check_transcribe_rate(current_user.id)
    try:
        result = await ocr.transcribe_upload(data)
    except ocr.OcrError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except Exception as e:
        logger.error("Transcription failed: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail="Transcription failed. Please try again, or type the note.")
    return result.to_dict()


@router.get("", response_model=List[NoteOut])
async def list_notes(
    ticker: Optional[str] = Query(None, description="Filter by ticker"),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    stmt = select(AnalystNote).where(or_(
        AnalystNote.owner_id == str(current_user.id),
        and_(AnalystNote.visibility == notes_service.SHARED, AnalystNote.active.is_not(False))))
    if ticker:
        stmt = stmt.where(AnalystNote.ticker == ticker.strip().upper())
    stmt = stmt.order_by(AnalystNote.note_date.desc(), AnalystNote.created_at.desc())
    included = await _included_ids(db, current_user)
    return [_out(n, current_user, included) for n in (await db.execute(stmt)).scalars()]


@router.post("", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
async def create_note(
    body: NoteCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    note = AnalystNote(
        note_id=str(uuid.uuid4()), owner_id=str(current_user.id), author=_display_name(current_user),
        ticker=body.ticker, title=body.title, content=body.content,
        note_date=body.note_date or date.today(), visibility=body.visibility, input_method=body.input_method, chunk_count=0)
    db.add(note)
    await db.flush()
    try:
        note.chunk_count = await notes_service.index_note(note)
        await db.commit()
    except Exception as e:
        await db.rollback()
        try:
            await notes_service.delete_note_points(note.note_id)  # best effort: don't leave orphan chunks
        except Exception:
            logger.warning("Could not clean up chunks for failed note %s", note.note_id)
        logger.error("Indexing analyst note failed: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail="Could not index the note; nothing was saved. Please try again.")
    await db.refresh(note)
    return _out(note, current_user)


@router.get("/{note_id}", response_model=NoteOut)
async def get_note(
    note_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    note = await _get_readable(db, note_id, current_user)
    return _out(note, current_user, await _included_ids(db, current_user))


@router.patch("/{note_id}", response_model=NoteOut)
async def update_note(
    note_id: str,
    body: NoteUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    note = await _get_owned(db, note_id, current_user)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        return _out(note, current_user)

    reindex = any(k in changes and changes[k] != getattr(note, k) for k in ("ticker", "title", "content", "note_date"))
    visibility_changed = "visibility" in changes and changes["visibility"] != note.visibility
    active_changed = "active" in changes and changes["active"] != (note.active is not False)
    previous_chunks = note.chunk_count
    for key, value in changes.items():
        setattr(note, key, value)
    try:
        if visibility_changed and note.visibility == notes_service.PRIVATE:
            # unsharing withdraws everyone's opt-in, so re-sharing later needs fresh consent
            await db.execute(delete(AnalystNoteInclusion).where(AnalystNoteInclusion.note_id == note.note_id))
        if reindex:  # re-embeds and rewrites every chunk, so the new visibility / active flag ride along in the same write
            note.chunk_count = await notes_service.index_note(note, previous_chunk_count=previous_chunks)
        else:
            if visibility_changed:
                await notes_service.set_points_visibility(note.note_id, note.visibility)
            if active_changed:
                await notes_service.set_points_active(note.note_id, note.active)
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error("Updating analyst note %s failed: %s", note_id, e, exc_info=True)
        raise HTTPException(status_code=502, detail="Could not update the note; no changes were saved.")
    await db.refresh(note)
    return _out(note, current_user)


class InclusionBody(BaseModel):
    included: bool


@router.put("/{note_id}/inclusion", response_model=NoteOut)
async def set_inclusion(
    note_id: str,
    body: InclusionBody,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Opt in/out of using another analyst's shared note in MY analysis. Your own notes use `active` instead."""
    note = await _get_readable(db, note_id, current_user)
    if str(note.owner_id) == str(current_user.id):
        raise HTTPException(status_code=400, detail="This is your own note: use Hide / Show instead.")
    if note.visibility != notes_service.SHARED:  # defensive: _get_readable already hides these
        raise HTTPException(status_code=404, detail="Not found")
    uid = str(current_user.id)
    existing = await db.get(AnalystNoteInclusion, (uid, note.note_id))
    if body.included and existing is None:
        db.add(AnalystNoteInclusion(user_id=uid, note_id=note.note_id))
    elif not body.included and existing is not None:
        await db.delete(existing)
    await db.commit()
    return _out(note, current_user, await _included_ids(db, current_user))


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    note_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    note = await _get_owned(db, note_id, current_user)
    try:
        await notes_service.delete_note_points(note.note_id)
    except Exception as e:  # keep the row so the user can retry; never leave chunks that outlive their note
        logger.error("Deleting chunks of analyst note %s failed: %s", note_id, e, exc_info=True)
        raise HTTPException(status_code=502, detail="Could not delete the note; please try again.")
    await db.execute(delete(AnalystNoteInclusion).where(AnalystNoteInclusion.note_id == note.note_id))
    await db.delete(note)
    await db.commit()
