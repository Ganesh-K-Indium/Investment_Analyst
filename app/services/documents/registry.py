"""
Documents registry: Qdrant -> SQL sync, filename parsing and list/facet queries.

Qdrant is the source of truth for what is ingested; the `documents` table is a cache of it.
See docs/DOCUMENT_INSPECTOR_PLAN.md §5.
"""
import asyncio
import logging
import os
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from qdrant_client import models
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Document

logger = logging.getLogger("app.services.documents.registry")

LEGACY_COLLECTION = "unified_rag_db_hybrid"
SEC_FILINGS_DIR = Path("sec_filings")  # relative to the working directory, like /edgar/file
SYNC_TTL_SECONDS = 300
_SCROLL_BATCH = 256
_FACET_LIMIT = 10000

# ZM_10-K_2022-03-07_000158552122000037.pdf
_FILENAME_RE = re.compile(r"^(?P<ticker>[^_]+)_(?P<form>[^_]+)_(?P<date>\d{4}-\d{2}-\d{2})_(?P<accession>\d+)\.[A-Za-z0-9]+$")

_client = None
ACTIVE_DOWNLOADS: set = set()  # doc_ids with a live re-download job (see pdf.py)
_sync_lock = asyncio.Lock()
_last_sync: Optional[float] = None


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_filename(source_file: str) -> Dict[str, Any]:
    """Extract ticker, form, filing_date and accession from an SEC filing filename (empty dict if no match)."""
    m = _FILENAME_RE.match(os.path.basename(source_file or ""))
    if not m:
        return {}
    try:
        filing_date = date.fromisoformat(m.group("date"))
    except ValueError:
        filing_date = None
    return {
        "ticker": m.group("ticker").upper(),
        "form_type": m.group("form"),
        "filing_date": filing_date,
        "accession": m.group("accession"),
    }


def resolve_pdf_status(ticker: Optional[str], source_file: str) -> str:
    """local / missing for .pdf sources whose file is under sec_filings/{TICKER}/, unsupported for anything else."""
    name = os.path.basename(source_file or "")
    if not name.lower().endswith(".pdf"):
        return "unsupported"
    return "local" if (SEC_FILINGS_DIR / (ticker or "") / name).is_file() else "missing"


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt  # columns are naive UTC


# ---------------------------------------------------------------------------
# Qdrant scan (sync; run in a thread from async code)
# ---------------------------------------------------------------------------

def _get_client():
    """Shared read-only Qdrant client (never creates collections)."""
    global _client
    if _client is None:
        from rag.vectordb.client import load_vector_database
        _client = load_vector_database(collection_name=LEGACY_COLLECTION, create_if_missing=False).qdrant_client
    return _client


def _eq(key: str, value: Any) -> models.FieldCondition:
    return models.FieldCondition(key=f"metadata.{key}", match=models.MatchValue(value=value))


def list_doc_collections(client) -> List[str]:
    """ticker_* collections (sorted) followed by the legacy unified one, if present."""
    names = [c.name for c in client.get_collections().collections]
    tickers = sorted(n for n in names if n.startswith("ticker_"))
    return tickers + ([LEGACY_COLLECTION] if LEGACY_COLLECTION in names else [])


def _first_pages(client, collection: str) -> Dict[str, Dict[str, Any]]:
    """content_hash -> metadata of each document's page-1 text chunk (one point per document)."""
    flt = models.Filter(must=[_eq("content_type", "text"), _eq("page_num", 1)])
    found: Dict[str, Dict[str, Any]] = {}
    offset = None
    while True:
        points, offset = client.scroll(collection, scroll_filter=flt, limit=_SCROLL_BATCH,
                                       offset=offset, with_payload=["metadata"], with_vectors=False)
        for p in points:
            meta = (p.payload or {}).get("metadata") or {}
            if meta.get("content_hash"):
                found.setdefault(meta["content_hash"], meta)
        if offset is None:
            return found


def _lowest_page_meta(client, collection: str) -> Dict[str, Dict[str, Any]]:
    """Fallback for documents whose page 1 has no text chunk: facet the hashes, take each one's lowest page."""
    text_only = models.Filter(must=[_eq("content_type", "text")])
    try:
        hits = client.facet(collection, key="metadata.content_hash", facet_filter=text_only, limit=_FACET_LIMIT).hits
    except Exception as e:
        logger.warning(f"facet fallback unavailable for {collection}: {e}")
        return {}
    found: Dict[str, Dict[str, Any]] = {}
    for hit in hits:
        flt = models.Filter(must=[_eq("content_type", "text"), _eq("content_hash", hit.value)])
        points, _ = client.scroll(collection, scroll_filter=flt, limit=1, with_payload=["metadata"], with_vectors=False,
                                  order_by=models.OrderBy(key="metadata.page_num", direction="asc"))
        if points:
            found[hit.value] = (points[0].payload or {}).get("metadata") or {}
    return found


def _count(client, collection: str, doc_id: str, content_type: str) -> int:
    flt = models.Filter(must=[_eq("content_hash", doc_id), _eq("content_type", content_type)])
    return client.count(collection, count_filter=flt, exact=True).count


def _max_page(client, collection: str, doc_id: str) -> Optional[int]:
    flt = models.Filter(must=[_eq("content_hash", doc_id)])
    try:
        points, _ = client.scroll(collection, scroll_filter=flt, limit=1, with_payload=["metadata"], with_vectors=False,
                                  order_by=models.OrderBy(key="metadata.page_num", direction="desc"))
    except Exception:
        return None
    return _to_int(((points[0].payload or {}).get("metadata") or {}).get("page_num")) if points else None


def _build_row(client, collection: str, doc_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    source_file = meta.get("source_file") or ""
    parsed = parse_filename(source_file)
    ticker = meta.get("ticker") or parsed.get("ticker") or (collection.removeprefix("ticker_") if collection.startswith("ticker_") else None)
    ticker = ticker.upper() if ticker else None
    text_pages = _count(client, collection, doc_id, "text")
    return {
        "doc_id": doc_id,
        "ticker": ticker,
        "company": meta.get("company"),
        "form_type": meta.get("filing_type") or parsed.get("form_type"),
        "fiscal_year": _to_int(meta.get("year")),
        "fiscal_quarter": _to_int(meta.get("fiscal_quarter")),
        "period_end_date": meta.get("period_end_date") or None,
        "filing_date": parsed.get("filing_date"),
        "accession": parsed.get("accession"),
        "source_file": source_file,
        "collection": collection,
        "page_count": _max_page(client, collection, doc_id) or text_pages,
        "text_pages": text_pages,
        "image_chunks": _count(client, collection, doc_id, "image"),
        "pdf_status": resolve_pdf_status(ticker, source_file),
        "ingested_at": _to_datetime(meta.get("ingestion_timestamp")),
    }


def scan_qdrant(client) -> Dict[str, Any]:
    """
    Walk every document collection and return {"rows": [...], "scanned": [collection, ...]}.
    A doc_id found in several collections is kept once (ticker collections win over the legacy one).
    Collections that fail to scan are left out of `scanned` so their cached rows are not marked removed.
    """
    rows: Dict[str, Dict[str, Any]] = {}
    scanned: List[str] = []
    for collection in list_doc_collections(client):
        try:
            metas = _first_pages(client, collection)
            for doc_id, meta in _lowest_page_meta(client, collection).items():
                metas.setdefault(doc_id, meta)
            for doc_id, meta in metas.items():
                if doc_id not in rows:
                    rows[doc_id] = _build_row(client, collection, doc_id, meta)
            scanned.append(collection)
        except Exception as e:
            logger.error(f"Registry scan failed for {collection}: {e}")
    return {"rows": list(rows.values()), "scanned": scanned}


# ---------------------------------------------------------------------------
# Sync (Qdrant -> SQL)
# ---------------------------------------------------------------------------

def _is_fresh() -> bool:
    return _last_sync is not None and time.monotonic() - _last_sync < SYNC_TTL_SECONDS


async def sync_registry(db: AsyncSession, client=None, force: bool = True) -> Dict[str, int]:
    """Upsert the registry from Qdrant. Returns counts {"documents", "added", "removed"}.
    With force=False, requests that queued behind another sync skip the redundant full scan."""
    global _last_sync
    async with _sync_lock:
        if not force and _is_fresh():
            return {"documents": 0, "added": 0, "removed": 0}
        scan = await asyncio.to_thread(scan_qdrant, client or _get_client())
        now = datetime.utcnow()
        existing = {d.doc_id: d for d in (await db.execute(select(Document))).scalars()}
        seen, added = set(), 0
        for row in scan["rows"]:
            seen.add(row["doc_id"])
            doc = existing.get(row["doc_id"])
            if doc is None:
                db.add(Document(**row, removed=False, synced_at=now))
                added += 1
                continue
            if doc.doc_id in ACTIVE_DOWNLOADS:
                row.pop("pdf_status")  # owned by the re-download job until it finishes
            for k, v in row.items():
                setattr(doc, k, v)
            doc.removed, doc.synced_at = False, now
        removed = 0
        for doc_id, doc in existing.items():
            if doc_id not in seen and doc.collection in scan["scanned"] and not doc.removed:
                doc.removed = True
                removed += 1
        await db.commit()
        _last_sync = time.monotonic()
        logger.info(f"Documents registry synced: {len(seen)} docs, {added} added, {removed} removed")
        return {"documents": len(seen), "added": added, "removed": removed}


FAILURE_BACKOFF_SECONDS = 60


def _mark_failed() -> None:
    """Retry a failed first sync after FAILURE_BACKOFF_SECONDS instead of on every request."""
    global _last_sync
    _last_sync = time.monotonic() - SYNC_TTL_SECONDS + FAILURE_BACKOFF_SECONDS


async def ensure_synced(db: AsyncSession) -> None:
    """Sync only when there is nothing to show yet (first ever load).

    After that the registry changes only when the user presses Refresh (POST /documents/sync): opening or
    paging through documents never triggers a scan of Qdrant.
    """
    if _is_fresh():
        return
    has_rows = (await db.execute(select(func.count()).select_from(Document))).scalar_one() > 0
    if has_rows:
        return
    try:
        await sync_registry(db, force=False)
    except Exception as e:
        logger.error(f"Initial documents sync failed: {e}")
        _mark_failed()


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

SORT_COLUMNS = {
    "filing_date": Document.filing_date,
    "ticker": Document.ticker,
    "ingested_at": Document.ingested_at,
    "pages": Document.page_count,
}


def _conditions(f: Dict[str, Any], exclude: Sequence[str] = ()) -> list:
    """SQL conditions for the active filters, skipping the dimensions named in `exclude` (for facets)."""
    conds = [Document.removed.is_(False)]
    if f.get("ticker") and "ticker" not in exclude:
        conds.append(Document.ticker.in_([t.upper() for t in f["ticker"]]))
    if f.get("form") and "form" not in exclude:
        conds.append(Document.form_type.in_(f["form"]))
    if "year" not in exclude:
        if f.get("year_from") is not None:
            conds.append(Document.fiscal_year >= f["year_from"])
        if f.get("year_to") is not None:
            conds.append(Document.fiscal_year <= f["year_to"])
    if f.get("quarter") is not None:
        conds.append(Document.fiscal_quarter == f["quarter"])
    if f.get("pdf_status"):
        conds.append(Document.pdf_status == f["pdf_status"])
    if f.get("has_images") is not None:
        conds.append(Document.image_chunks > 0 if f["has_images"] else Document.image_chunks == 0)
    if f.get("q"):
        like = f"%{f['q'].lower()}%"
        conds.append(or_(func.lower(Document.source_file).like(like), func.lower(Document.company).like(like),
                         func.lower(Document.ticker).like(like)))
    return conds


async def compute_facets(db: AsyncSession, f: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Counts per ticker / form / year; each dimension is counted against the OTHER active filters."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for name, col, exclude in (("tickers", Document.ticker, "ticker"), ("forms", Document.form_type, "form"),
                               ("years", Document.fiscal_year, "year")):
        stmt = (select(col, func.count()).where(and_(*_conditions(f, (exclude,)), col.is_not(None)))
                .group_by(col).order_by(col.desc() if name == "years" else col))
        out[name] = [{"value": v, "count": c} for v, c in (await db.execute(stmt)).all()]
    return out


async def query_documents(db: AsyncSession, f: Dict[str, Any], sort: str, order: str, offset: int, limit: int):
    """Returns (rows, total, has_more) for the filters, ordered with doc_id as a stable tiebreaker."""
    where = and_(*_conditions(f))
    total = (await db.execute(select(func.count()).select_from(Document).where(where))).scalar_one()
    col = SORT_COLUMNS[sort]
    primary = col.asc() if order == "asc" else col.desc()
    stmt = select(Document).where(where).order_by(primary.nulls_last(), Document.doc_id).offset(offset).limit(limit)
    rows = list((await db.execute(stmt)).scalars())
    return rows, total, offset + len(rows) < total


async def get_document(db: AsyncSession, doc_id: str) -> Optional[Document]:
    doc = await db.get(Document, doc_id)
    return doc if doc and not doc.removed else None
