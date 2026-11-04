"""
PDF resolution and EDGAR re-download for the Document Inspector.

A document's PDF lives at sec_filings/{TICKER}/{basename(source_file)}. If it was deleted after ingestion we
re-fetch it through SecEdgarFetcher into the same folder. Because the fetcher renders filing HTML to PDF with a
headless browser, the new copy may paginate differently from the indexed one, so after a download we compare
per-page fingerprints against the indexed text (see docs/DOCUMENT_INSPECTOR_PLAN.md §7).
"""
import asyncio
import hashlib
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from qdrant_client import models
from sqlalchemy import select

from app.database.models import Document
from app.services.documents import registry

logger = logging.getLogger("app.services.documents.pdf")

FINGERPRINT_CHARS = 200
ALIGNMENT_THRESHOLD = 0.9  # share of indexed pages whose fingerprint must match

# doc_id -> {"status": downloading|local|failed, "error": str|None, "started_at": float}
_jobs: Dict[str, Dict[str, Any]] = {}


def pdf_path_for(doc: Document) -> Optional[Path]:
    """The local PDF for a document, or None when it is not a .pdf source or not on disk."""
    name = os.path.basename(doc.source_file or "")
    if not name.lower().endswith(".pdf"):
        return None
    path = registry.SEC_FILINGS_DIR / (doc.ticker or "") / name
    return path if path.is_file() else None


def job_info(doc_id: str) -> Optional[Dict[str, Any]]:
    return _jobs.get(doc_id)


# ---------------------------------------------------------------------------
# Fingerprints / alignment
# ---------------------------------------------------------------------------

def fingerprint(text: str) -> Optional[str]:
    """Hash of the first FINGERPRINT_CHARS alphanumeric chars (None for pages with no text)."""
    norm = re.sub(r"[^a-z0-9]", "", (text or "").lower())[:FINGERPRINT_CHARS]
    return hashlib.sha1(norm.encode()).hexdigest()[:12] if norm else None


def pdf_fingerprints(path: Path) -> List[Optional[str]]:
    """Per-page fingerprints of a PDF, index 0 = page 1."""
    import fitz
    with fitz.open(path) as pdf:
        return [fingerprint(page.get_text("text")) for page in pdf]


def indexed_fingerprints(client, collection: str, doc_id: str) -> Dict[int, List[str]]:
    """page_num -> fingerprints of the text chunk(s) indexed for that page (a page can have more than one)."""
    flt = models.Filter(must=[
        models.FieldCondition(key="metadata.content_hash", match=models.MatchValue(value=doc_id)),
        models.FieldCondition(key="metadata.content_type", match=models.MatchValue(value="text")),
    ])
    out: Dict[int, List[str]] = {}
    offset = None
    while True:
        points, offset = client.scroll(collection, scroll_filter=flt, limit=256, offset=offset,
                                       with_payload=["page_content", "metadata.page_num"], with_vectors=False)
        for p in points:
            payload = p.payload or {}
            page = ((payload.get("metadata") or {}).get("page_num"))
            # ingestion appends "[TABLES]" markdown to the page text; the PDF side has only the plain text
            content = payload.get("page_content") or ""
            if content.startswith("[TABLES]\n"):  # table-only chunk: not page text
                continue
            fp = fingerprint(content.split("\n\n[TABLES]\n")[0])
            if page and fp:
                out.setdefault(int(page), []).append(fp)
        if offset is None:
            return out


def check_alignment(indexed: Dict[int, List[str]], pdf_fps: List[Optional[str]]) -> bool:
    """True when the PDF has the indexed page count and (almost) every indexed page's text starts the same."""
    if not indexed or len(pdf_fps) < max(indexed):
        return False
    matches = sum(1 for page, fp in indexed.items() if pdf_fps[page - 1] in fp)
    return matches / len(indexed) >= ALIGNMENT_THRESHOLD


def compute_alignment(client, doc: Document, path: Path) -> Dict[str, Any]:
    """Sync (run in a thread). Returns the dict stored in Document.page_fingerprints."""
    pdf_fps = pdf_fingerprints(path)
    indexed = indexed_fingerprints(client, doc.collection, doc.doc_id)
    return {
        "aligned": check_alignment(indexed, pdf_fps),
        "pdf_pages": len(pdf_fps),
        "indexed": {str(k): v for k, v in indexed.items()},  # page -> [fingerprint, ...]
    }


# ---------------------------------------------------------------------------
# Re-download
# ---------------------------------------------------------------------------

async def _download(doc: Document) -> None:
    """Fetch this one filing's PDF into sec_filings/{TICKER}/ (no ingestion)."""
    if not (doc.ticker and doc.accession and doc.filing_date and doc.form_type):
        raise RuntimeError("Cannot identify the SEC filing for this document (filename has no accession/date).")
    from ingestion.edgar_fetcher import SecEdgarFetcher
    async with SecEdgarFetcher() as fetcher:
        summary = await fetcher.fetch_filings(
            ticker=doc.ticker, form_types=[doc.form_type], start_date=doc.filing_date, end_date=doc.filing_date,
            output_dir=str(registry.SEC_FILINGS_DIR), ingest=False, accession_filter={doc.accession},
        )
    filings = summary.get("filings") or []
    if not filings:
        raise RuntimeError(f"EDGAR has no {doc.form_type} filing with accession {doc.accession}.")
    if filings[0]["status"] == "failed":
        raise RuntimeError(filings[0].get("error") or "Download failed.")


async def _run_job(doc_id: str) -> None:
    from app.database.connection import SessionLocal
    job = _jobs[doc_id]
    try:
        async with SessionLocal() as db:
            doc = await db.get(Document, doc_id)
            await _download(doc)
            path = pdf_path_for(doc)
            if path is None:
                raise RuntimeError("Download finished but the PDF is not on disk.")
            info = await asyncio.to_thread(compute_alignment, registry._get_client(), doc, path)
            doc.pdf_status, doc.page_fingerprints = "local", info
            await db.commit()
        job.update(status="local", error=None)
    except Exception as e:
        logger.error(f"PDF re-download failed for {doc_id}: {e}", exc_info=True)
        job.update(status="failed", error=str(e))
        async with SessionLocal() as db:
            doc = await db.get(Document, doc_id)
            if doc:
                doc.pdf_status = "failed"
                await db.commit()
    finally:
        registry.ACTIVE_DOWNLOADS.discard(doc_id)


async def start_download(db, doc: Document) -> Dict[str, Any]:
    """Idempotent: starts one background job per document and returns its state."""
    if pdf_path_for(doc):
        return {"status": "local", "error": None}
    if doc.doc_id in registry.ACTIVE_DOWNLOADS:
        return _jobs[doc.doc_id]
    if not (doc.source_file or "").lower().endswith(".pdf"):
        return {"status": "unsupported", "error": "Only PDF sources can be downloaded."}
    registry.ACTIVE_DOWNLOADS.add(doc.doc_id)
    _jobs[doc.doc_id] = {"status": "downloading", "error": None, "started_at": time.time()}
    doc.pdf_status = "downloading"
    await db.commit()
    asyncio.create_task(_run_job(doc.doc_id))
    return _jobs[doc.doc_id]


async def aligned_flag(db, doc_id: str) -> Optional[bool]:
    """Stored alignment verdict (None when never checked)."""
    row = (await db.execute(select(Document.page_fingerprints).where(Document.doc_id == doc_id))).scalar_one_or_none()
    return row.get("aligned") if isinstance(row, dict) else None
