"""
Document Inspector endpoints (registry of documents ingested into Qdrant)

GET  /documents — list documents with filters, paging and facets
GET  /documents/facets — ticker / form / year counts for the filter UI
POST /documents/sync — force a Qdrant -> registry sync
GET  /documents/{doc_id} — one document with its live PDF status
GET  /documents/{doc_id}/pages — per-page index (chars / has_tables / has_images)
GET  /documents/{doc_id}/pages/{n} — page JSON: text, tables, images, blocks (geometry)
GET  /documents/{doc_id}/pages/{n}/locate?q= — rects of a quoted text on the PDF page
GET  /documents/{doc_id}/pdf — stream the PDF (HTTP Range + ETag)
POST /documents/{doc_id}/ensure-pdf — re-download a missing PDF from SEC EDGAR (idempotent)
GET  /documents/{doc_id}/pdf-status — poll the re-download
"""
import asyncio
import logging
from datetime import date, datetime
from typing import List, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.database.connection import get_db_session
from app.database.models import User
from app.services.documents import pages as pages_service
from app.services.documents import pdf as pdf_service
from app.services.documents import registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["Documents"])

# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    doc_id: str
    ticker: Optional[str] = None
    company: Optional[str] = None
    form_type: Optional[str] = None
    fiscal_year: Optional[int] = None
    fiscal_quarter: Optional[int] = None
    period_end_date: Optional[str] = None
    filing_date: Optional[date] = None
    accession: Optional[str] = None
    source_file: str
    collection: str
    page_count: int
    text_pages: int
    image_chunks: int
    pdf_status: str  # local | missing | downloading | failed | unsupported
    ingested_at: Optional[datetime] = None
    synced_at: datetime
    aligned: Optional[bool] = None  # re-downloaded PDF paginates like the indexed text (None = not checked)


class PdfStatus(BaseModel):
    status: str  # local | missing | downloading | failed | unsupported
    error: Optional[str] = None
    aligned: Optional[bool] = None


class PageSummary(BaseModel):
    page: int
    chars: int
    has_tables: bool
    has_images: bool


class PageIndexResponse(BaseModel):
    doc_id: str
    page_count: int
    pages: List[PageSummary]


class PageTable(BaseModel):
    markdown: str
    headers: Optional[List[str]] = None
    rows: Optional[List[List[str]]] = None
    bbox: Optional[List[float]] = None
    verified: Optional[dict] = None  # {"numbers": printed in the table area, "matched": found in the extracted cells}
    source: Literal["pdf", "index"] = "index"


class PageImage(BaseModel):
    image_id: Optional[str] = None
    type: Optional[str] = None
    summary: Optional[str] = None
    analysis: str
    bbox: Optional[List[float]] = None


class PageBlock(BaseModel):
    id: str
    bbox: List[float]
    text: str


class PageChunk(BaseModel):
    point_id: str
    content_type: Optional[str] = None


class PageSize(BaseModel):
    width: float
    height: float


class PageMeta(BaseModel):
    ticker: Optional[str] = None
    form_type: Optional[str] = None
    fiscal_year: Optional[int] = None
    ingested_at: Optional[str] = None
    pdf_available: bool
    aligned: Optional[bool] = None


class PageResponse(BaseModel):
    doc_id: str
    page: int
    page_count: int
    size: Optional[PageSize] = None  # PDF points; None when the PDF is not on disk
    text: str
    tables: List[PageTable]
    images: List[PageImage]
    blocks: List[PageBlock]  # [] when the PDF is not on disk
    chunks: List[PageChunk]
    meta: PageMeta


class FacetCount(BaseModel):
    value: Union[str, int]
    count: int


class Facets(BaseModel):
    tickers: List[FacetCount]
    forms: List[FacetCount]
    years: List[FacetCount]


class DocumentListResponse(BaseModel):
    items: List[DocumentOut]
    total: int
    next_cursor: Optional[str] = None
    facets: Facets


class SyncResponse(BaseModel):
    documents: int
    added: int
    removed: int


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _filters(ticker, form, year_from, year_to, quarter, q, pdf_status, has_images) -> dict:
    return {"ticker": ticker, "form": form, "year_from": year_from, "year_to": year_to, "quarter": quarter,
            "q": q.strip() if q else None, "pdf_status": pdf_status, "has_images": has_images}


def _live_status(doc) -> str:
    """Registry status, with disk as truth unless a re-download job is live (or has just failed)."""
    if doc.doc_id in registry.ACTIVE_DOWNLOADS:
        return "downloading"
    job = pdf_service.job_info(doc.doc_id)
    if job and job["status"] == "failed" and not pdf_service.pdf_path_for(doc):
        return "failed"
    return registry.resolve_pdf_status(doc.ticker, doc.source_file)


def _aligned(doc) -> Optional[bool]:
    fp = doc.page_fingerprints
    return fp.get("aligned") if isinstance(fp, dict) else None


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints (declaration order matters: fixed paths before /{doc_id})
# ─────────────────────────────────────────────────────────────────────────────

@router.get("", response_model=DocumentListResponse)
async def list_documents(
    ticker: Optional[List[str]] = Query(None),
    form: Optional[List[str]] = Query(None),
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    quarter: Optional[int] = Query(None, ge=1, le=4),
    q: Optional[str] = Query(None, description="Search filename, company and ticker"),
    pdf_status: Optional[Literal["local", "missing", "downloading", "failed", "unsupported"]] = None,
    has_images: Optional[bool] = None,
    sort: Literal["filing_date", "ticker", "ingested_at", "pages"] = "filing_date",
    order: Literal["asc", "desc"] = "desc",
    cursor: Optional[str] = Query(None, description="Opaque cursor from a previous response's next_cursor"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """List ingested documents. `facets` are counted against the other active filters."""
    try:
        offset = int(cursor) if cursor else 0
        if offset < 0:
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid cursor.")
    await registry.ensure_synced(db)
    f = _filters(ticker, form, year_from, year_to, quarter, q, pdf_status, has_images)
    rows, total, has_more = await registry.query_documents(db, f, sort, order, offset, limit)
    return DocumentListResponse(
        items=rows,
        total=total,
        next_cursor=str(offset + len(rows)) if has_more else None,
        facets=await registry.compute_facets(db, f),
    )


@router.get("/facets", response_model=Facets)
async def document_facets(
    ticker: Optional[List[str]] = Query(None),
    form: Optional[List[str]] = Query(None),
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    quarter: Optional[int] = Query(None, ge=1, le=4),
    q: Optional[str] = None,
    pdf_status: Optional[Literal["local", "missing", "downloading", "failed", "unsupported"]] = None,
    has_images: Optional[bool] = None,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Counts per ticker, form and year (same filters as the list; each facet ignores its own filter)."""
    await registry.ensure_synced(db)
    return await registry.compute_facets(db, _filters(ticker, form, year_from, year_to, quarter, q, pdf_status, has_images))


@router.post("/sync", response_model=SyncResponse)
async def sync_documents(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Force a Qdrant -> registry sync."""
    try:
        return await registry.sync_registry(db)
    except Exception as e:
        logger.error(f"Documents sync failed: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Sync from Qdrant failed: {e}")


@router.get("/{doc_id}", response_model=DocumentOut)
async def get_document(
    doc_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """One registry row with the PDF status re-checked against disk."""
    await registry.ensure_synced(db)
    doc = await registry.get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    out = DocumentOut.model_validate(doc)
    out.pdf_status = _live_status(doc)
    out.aligned = _aligned(doc)
    return out


@router.get("/{doc_id}/pages", response_model=PageIndexResponse)
async def get_page_index(
    doc_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Per-page summary for the thumbnail rail (which pages have text, tables, charts)."""
    doc = await registry.get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    pages = await asyncio.to_thread(pages_service.build_page_index, registry._get_client(), doc)
    return PageIndexResponse(doc_id=doc_id, page_count=doc.page_count, pages=pages)


@router.get("/{doc_id}/pages/{page_num}", response_model=PageResponse)
async def get_page(
    doc_id: str,
    page_num: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """One page: indexed text/tables/image analyses from Qdrant plus PDF geometry when the PDF is on disk."""
    doc = await registry.get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    if not 1 <= page_num <= doc.page_count:
        raise HTTPException(status_code=404, detail=f"Page {page_num} out of range (1-{doc.page_count}).")
    return await asyncio.to_thread(pages_service.build_page, registry._get_client(), doc, page_num)


class LocateResponse(BaseModel):
    method: Literal["exact", "fuzzy", "no_text_layer", "not_found", "no_pdf"]
    rects: List[List[float]]
    score: float


@router.get("/{doc_id}/pages/{page_num}/locate", response_model=LocateResponse)
async def locate_on_page(
    doc_id: str,
    page_num: int,
    q: str = Query(..., min_length=3, max_length=1200),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Where a cited quote sits on the PDF page (PDF points, top-left origin) for the highlight overlay."""
    doc = await registry.get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    if not 1 <= page_num <= doc.page_count:
        raise HTTPException(status_code=404, detail=f"Page {page_num} out of range (1-{doc.page_count}).")
    return await asyncio.to_thread(pages_service.locate_quote, doc, page_num, q)


@router.get("/{doc_id}/pdf")
async def get_document_pdf(
    doc_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Stream the PDF. FileResponse handles HTTP Range and ETag, which pdf.js uses to load pages lazily."""
    doc = await registry.get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    path = pdf_service.pdf_path_for(doc)
    if path is None:
        raise HTTPException(status_code=404, detail={"message": "PDF not available locally.", "pdf_status": _live_status(doc)})
    return FileResponse(path, media_type="application/pdf", content_disposition_type="inline", filename=path.name)


@router.post("/{doc_id}/ensure-pdf", response_model=PdfStatus)
async def ensure_document_pdf(
    doc_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Start (or join) the SEC EDGAR re-download of a missing PDF into sec_filings/{TICKER}/."""
    doc = await registry.get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    job = await pdf_service.start_download(db, doc)
    return PdfStatus(status=job["status"], error=job.get("error"), aligned=_aligned(doc))


@router.get("/{doc_id}/pdf-status", response_model=PdfStatus)
async def document_pdf_status(
    doc_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    doc = await registry.get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    job = pdf_service.job_info(doc_id)
    return PdfStatus(status=_live_status(doc), error=job.get("error") if job else None, aligned=_aligned(doc))
