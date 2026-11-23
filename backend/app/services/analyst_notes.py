"""
Analyst notes: per-analyst research notes that join the RAG context for that analyst.

Notes live in their OWN Qdrant collection (`analyst_notes`), deliberately outside the `ticker_*` namespace, so
filing retrieval (rag/graph/nodes.py retrieve) and the Documents registry never see them. Every chunk carries
`metadata.owner_id` + `metadata.visibility`, and every read goes through `visibility_filter()`:

    private  -> only the owner can retrieve it
    shared   -> every authenticated user CAN use it, but only after opting in to that note (analyst_note_inclusions);
                nobody's analysis is changed by another analyst's note unless they chose it
    paused   -> (`active` = false) the owner's "hide for now": stored, but out of everyone's analysis

Text is the seam for ingestion: typed notes arrive as text today; handwritten/scanned notes plug in by producing text
(plus page/confidence) before calling `index_note`.
"""
import asyncio
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from langchain_core.documents import Document
from qdrant_client import models
from qdrant_client.http.models import PayloadSchemaType

from app.database.models import AnalystNote

logger = logging.getLogger("app.services.analyst_notes")

NOTES_COLLECTION = "analyst_notes"
PRIVATE, SHARED = "private", "shared"
VISIBILITIES = (PRIVATE, SHARED)
SOURCE_TYPE = "analyst_note"

MAX_CONTENT_CHARS = 20_000
MAX_TITLE_CHARS = 200
CHUNK_CHARS = 900

_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")
_ID_NAMESPACE = uuid.UUID("5c1e7a52-0b2f-4f6e-9a63-7d2f1c9a4e10")  # stable point ids: uuid5(note_id:index)
_INDEXED_FIELDS = {"owner_id": PayloadSchemaType.KEYWORD, "visibility": PayloadSchemaType.KEYWORD,
                   "ticker": PayloadSchemaType.KEYWORD, "note_id": PayloadSchemaType.KEYWORD,
                   "active": PayloadSchemaType.BOOL}

_db = None
_collection_ready = False


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def normalize_ticker(value: str) -> str:
    ticker = (value or "").strip().upper()
    if not _TICKER_RE.match(ticker):
        raise ValueError("Ticker must be 1-10 letters/digits (dots and dashes allowed), e.g. AAPL or BRK.B")
    return ticker


def chunk_note(text: str, max_chars: int = CHUNK_CHARS) -> List[str]:
    """Pack paragraphs (blank-line separated) into chunks of at most `max_chars`; over-long paragraphs are wrapped at
    whitespace. Never returns an empty chunk."""
    pieces: List[str] = []
    for para in re.split(r"\n\s*\n", (text or "").strip()):
        para = para.strip()
        while len(para) > max_chars:
            cut = para.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            pieces.append(para[:cut].strip())
            para = para[cut:].strip()
        if para:
            pieces.append(para)
    chunks: List[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + 2 + len(piece) > max_chars:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def point_id(note_id: str, index: int) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, f"{note_id}:{index}"))


def _eq(key: str, value: Any) -> models.FieldCondition:
    return models.FieldCondition(key=f"metadata.{key}", match=models.MatchValue(value=value))


def visibility_filter(user_id: Any, included_ids: Iterable[str] = ()) -> models.Filter:
    """What `user_id` may use: their own notes, plus the SHARED notes of others that they opted in to
    (`included_ids`); never anything paused. `must_not active=false` (not `must active=true`) so chunks written
    before the flag existed still count as active. The ONLY read path to note chunks."""
    should: List[Any] = [_eq("owner_id", str(user_id))]
    ids = sorted({str(i) for i in included_ids})
    if ids:
        should.append(models.Filter(must=[_eq("visibility", SHARED),
                                          models.FieldCondition(key="metadata.note_id", match=models.MatchAny(any=ids))]))
    return models.Filter(should=should, must_not=[
        models.FieldCondition(key="metadata.active", match=models.MatchValue(value=False))])


def read_filter(user_id: Any, tickers: Optional[Iterable[str]] = None, included_ids: Iterable[str] = ()) -> models.Filter:
    must: List[Any] = [visibility_filter(user_id, included_ids)]
    wanted = sorted({t.strip().upper() for t in (tickers or []) if t and t.strip()})
    if wanted:
        must.append(models.FieldCondition(key="metadata.ticker", match=models.MatchAny(any=wanted)))
    return models.Filter(must=must)


def chunk_metadata(note: AnalystNote, index: int, total: int) -> Dict[str, Any]:
    return {
        "source_file": f"Analyst note: {note.title}",
        "source_type": SOURCE_TYPE,
        "filing_type": SOURCE_TYPE,
        "content_type": "text",
        "content_hash": note.note_id,
        "note_id": note.note_id,
        "owner_id": str(note.owner_id),
        "author": note.author,
        "visibility": note.visibility,
        "active": note.active is not False,
        "input_method": note.input_method or "typed",
        "ticker": note.ticker,
        "company": note.ticker,
        "title": note.title,
        "note_date": note.note_date.isoformat(),
        "year": note.note_date.year,
        "period_end_date": None,
        "fiscal_quarter": None,
        "page_num": index + 1,
        "chunk_index": index,
        "chunk_count": total,
        "ingestion_timestamp": str(datetime.now()),
    }


def to_document(payload: Dict[str, Any]) -> Document:
    """A retrieved chunk as a Document. The header tells the LLM this is an opinion, not filed data."""
    meta = dict(payload.get("metadata") or {})
    via = " Transcribed from handwriting and checked by its author." if meta.get("input_method") == "handwritten" else ""
    header = (
        f"[ANALYST NOTE — {meta.get('author', 'an analyst')}'s {meta.get('visibility', PRIVATE)} note on "
        f"{meta.get('ticker', '')} dated {meta.get('note_date', '')}: \"{meta.get('title', '')}\". This is the "
        f"analyst's own view or estimate, not a company disclosure: attribute it as such and never present it as "
        f"filed data.{via}]"
    )
    return Document(page_content=f"{header}\n{payload.get('page_content', '')}", metadata=meta)


# ---------------------------------------------------------------------------
# Qdrant access
# ---------------------------------------------------------------------------

def _get_db():
    """Shared notes DB handle. Constructed WITHOUT creating the collection, so reads never create anything."""
    global _db
    if _db is None:
        from rag.vectordb.client import load_vector_database
        _db = load_vector_database(collection_name=NOTES_COLLECTION, create_if_missing=False)
    return _db


async def _ensure_collection(db) -> None:
    """Create the collection (hybrid dense+bm25) and the owner/visibility/ticker/note_id indexes once, on first write."""
    global _collection_ready
    if _collection_ready:
        return

    def _setup():
        db.ensure_collection_exists()
        for field, schema in _INDEXED_FIELDS.items():
            try:
                db.qdrant_client.create_payload_index(
                    collection_name=NOTES_COLLECTION, field_name=f"metadata.{field}", field_schema=schema)
            except Exception as e:  # already exists is fine; anything else shows up on first query
                logger.debug(f"payload index metadata.{field}: {e}")

    await asyncio.to_thread(_setup)
    _collection_ready = True


async def index_note(note: AnalystNote, previous_chunk_count: int = 0) -> int:
    """(Re)index a note's chunks; returns the new chunk count. Stale trailing chunks of an edited note are removed."""
    chunks = chunk_note(note.content)
    if not chunks:
        raise ValueError("Note has no text to index")
    db = _get_db()
    await _ensure_collection(db)
    emb = await db.generate_embeddings_for_ingestion(chunks, label="analyst note chunk(s)")
    points = []
    for i, text in enumerate(chunks):
        vector = {"dense": emb["dense"][i]}
        if emb["sparse"][i] is not None:
            vector["bm25"] = emb["sparse"][i]
        points.append(models.PointStruct(
            id=point_id(note.note_id, i), vector=vector,
            payload={"page_content": text, "metadata": chunk_metadata(note, i, len(chunks))}))
    await db.async_qdrant_client.upsert(collection_name=NOTES_COLLECTION, points=points, wait=True)
    if previous_chunk_count > len(chunks):
        stale = [point_id(note.note_id, i) for i in range(len(chunks), previous_chunk_count)]
        await db.async_qdrant_client.delete(
            collection_name=NOTES_COLLECTION, points_selector=models.PointIdsList(points=stale), wait=True)
    return len(chunks)


def _note_selector(note_id: str) -> models.FilterSelector:
    return models.FilterSelector(filter=models.Filter(must=[_eq("note_id", note_id)]))


async def delete_note_points(note_id: str) -> None:
    try:
        await _get_db().async_qdrant_client.delete(
            collection_name=NOTES_COLLECTION, points_selector=_note_selector(note_id), wait=True)
    except Exception as e:
        if "not found" in str(e).lower() or "doesn't exist" in str(e).lower():
            return  # collection never created: nothing to delete
        raise


async def set_points_visibility(note_id: str, visibility: str) -> None:
    """Flip visibility on the stored chunks without re-embedding."""
    await _get_db().async_qdrant_client.set_payload(
        collection_name=NOTES_COLLECTION, payload={"visibility": visibility}, key="metadata",
        points=_note_selector(note_id), wait=True)


async def set_points_active(note_id: str, active: bool) -> None:
    """Pause / resume a note's chunks without re-embedding."""
    await _get_db().async_qdrant_client.set_payload(
        collection_name=NOTES_COLLECTION, payload={"active": active}, key="metadata",
        points=_note_selector(note_id), wait=True)


async def included_note_ids(user_id: Any) -> List[str]:
    """Ids of other analysts' shared notes this user opted in to. Fails closed ([]) so a DB problem can only remove
    shared notes from an answer, never add any."""
    try:
        from sqlalchemy import select
        from app.database.connection import SessionLocal
        from app.database.models import AnalystNoteInclusion
        async with SessionLocal() as db:
            rows = await db.execute(select(AnalystNoteInclusion.note_id).where(AnalystNoteInclusion.user_id == str(user_id)))
            return [r[0] for r in rows]
    except Exception as e:
        logger.warning(f"Could not read note inclusions for user {user_id}: {e}")
        return []


async def has_visible_notes(user_id: Any, tickers: Iterable[str], included_ids: Iterable[str] = ()) -> bool:
    """Cheap pre-check (no embedding call): does this user have any usable note for these tickers?"""
    try:
        res = await _get_db().async_qdrant_client.count(
            collection_name=NOTES_COLLECTION, count_filter=read_filter(user_id, tickers, included_ids), exact=False)
        return res.count > 0
    except Exception as e:
        logger.debug(f"notes count skipped: {e}")  # collection absent (no note ever written) or Qdrant unreachable
        return False


async def search_notes(user_id: Any, query: str, tickers: Iterable[str], limit: int = 3) -> List[Document]:
    """Top note chunks for `query` that `user_id` may read, restricted to `tickers`. Returns [] on any failure."""
    tickers = [t for t in tickers if t]
    if not user_id or not tickers:
        return []
    included = await included_note_ids(user_id)
    if not await has_visible_notes(user_id, tickers, included):
        return []
    try:
        points = await _get_db().hybrid_search(
            query=query, content_type="text", limit=limit, dense_limit=30, sparse_limit=30,
            extra_conditions=[read_filter(user_id, tickers, included)])
        return [to_document(p.payload) for p in points if getattr(p, "payload", None)]
    except Exception as e:
        logger.warning(f"Analyst note search failed, continuing without notes: {e}")
        return []
