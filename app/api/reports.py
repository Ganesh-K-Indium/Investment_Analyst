"""
Report Repository API
Phase A: Draft clipboard endpoints (backend replacement for localStorage)
Phase B: Analyst report CRUD + PDF export
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Any, Dict
from sqlalchemy.orm import Session
from datetime import datetime
import io

from app.database.connection import get_db_session
from app.database.models import AnalystReport
from app.services import report as report_svc

router = APIRouter(prefix="/reports", tags=["Reports"])

_VALID_ITEM_TYPES = {"text", "image", "summary"}
_VALID_SOURCES = {"rag", "quant", "summary"}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class DraftItemCreate(BaseModel):
    user_id: str = Field(..., description="Author user ID")
    item_type: str = Field(..., description="text | image | summary")
    content: Optional[str] = Field(None, description="Markdown text or summary body")
    image_url: Optional[str] = Field(None, description="Cloudinary URL for chart images")
    source: Optional[str] = Field(None, description="rag | quant | summary")
    session_id: Optional[str] = Field(None, description="Originating chat session ID")
    label: Optional[str] = Field(None, description="User-editable label shown in creation tab")
    sort_order: Optional[int] = Field(0, description="Display position (0 = top)")


class DraftItemUpdate(BaseModel):
    label: Optional[str] = None
    content: Optional[str] = None
    sort_order: Optional[int] = None


class ReorderRequest(BaseModel):
    ordered_ids: List[int] = Field(..., description="Item IDs in the desired display order")


class DraftItemResponse(BaseModel):
    id: int
    user_id: str
    item_type: str
    content: Optional[str]
    image_url: Optional[str]
    source: Optional[str]
    session_id: Optional[str]
    label: Optional[str]
    sort_order: int
    created_at: str

    @classmethod
    def from_orm(cls, obj):
        return cls(
            id=obj.id,
            user_id=obj.user_id,
            item_type=obj.item_type,
            content=obj.content,
            image_url=obj.image_url,
            source=obj.source,
            session_id=obj.session_id,
            label=obj.label,
            sort_order=obj.sort_order or 0,
            created_at=obj.created_at.isoformat() if obj.created_at else "",
        )


# ---------------------------------------------------------------------------
# Draft clipboard endpoints
# ---------------------------------------------------------------------------

@router.post("/draft/items", response_model=DraftItemResponse, status_code=201)
def add_draft_item(payload: DraftItemCreate, db: Session = Depends(get_db_session)):
    """
    Add a generated text block, chart image, or summary to the user's clipboard.
    Call this whenever the RAG or Quant agent produces output the analyst wants
    to include in a report.
    """
    if payload.item_type not in _VALID_ITEM_TYPES:
        raise HTTPException(status_code=400, detail=f"item_type must be one of {_VALID_ITEM_TYPES}")

    if payload.source and payload.source not in _VALID_SOURCES:
        raise HTTPException(status_code=400, detail=f"source must be one of {_VALID_SOURCES}")

    if payload.item_type == "image" and not payload.image_url:
        raise HTTPException(status_code=400, detail="image_url is required for item_type='image'")

    if payload.item_type in {"text", "summary"} and not payload.content:
        raise HTTPException(status_code=400, detail="content is required for item_type='text' or 'summary'")

    item = report_svc.add_draft_item(
        db=db,
        user_id=payload.user_id,
        item_type=payload.item_type,
        content=payload.content,
        image_url=payload.image_url,
        source=payload.source,
        session_id=payload.session_id,
        label=payload.label,
        sort_order=payload.sort_order or 0,
    )
    return DraftItemResponse.from_orm(item)


@router.get("/draft/items/{user_id}", response_model=List[DraftItemResponse])
def get_draft_items(user_id: str, db: Session = Depends(get_db_session)):
    """
    Retrieve all clipboard items for the given user, sorted by sort_order then created_at.
    The creation tab calls this to populate the report builder.
    """
    items = report_svc.get_draft_items(db, user_id)
    return [DraftItemResponse.from_orm(i) for i in items]


@router.put("/draft/items/{item_id}", response_model=DraftItemResponse)
def update_draft_item(
    item_id: int,
    payload: DraftItemUpdate,
    user_id: str,
    db: Session = Depends(get_db_session),
):
    """
    Edit a clipboard item's label, text content, or sort position.
    Pass user_id as a query parameter.
    """
    item = report_svc.update_draft_item(
        db=db,
        item_id=item_id,
        user_id=user_id,
        label=payload.label,
        content=payload.content,
        sort_order=payload.sort_order,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Draft item not found")
    return DraftItemResponse.from_orm(item)


@router.post("/draft/items/reorder", response_model=List[DraftItemResponse])
def reorder_draft_items(
    payload: ReorderRequest,
    user_id: str,
    db: Session = Depends(get_db_session),
):
    """
    Reorder clipboard items by supplying all item IDs in the desired display order.
    Pass user_id as a query parameter.
    """
    items = report_svc.reorder_draft_items(db, user_id, payload.ordered_ids)
    return [DraftItemResponse.from_orm(i) for i in items]


@router.delete("/draft/items/{item_id}", status_code=200)
def delete_draft_item(item_id: int, user_id: str, db: Session = Depends(get_db_session)):
    """
    Remove a single clipboard item.
    Pass user_id as a query parameter.
    """
    success = report_svc.delete_draft_item(db, item_id, user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Draft item not found")
    return {"message": "Item removed", "item_id": item_id}


@router.delete("/draft/items/user/{user_id}", status_code=200)
def clear_draft_items(user_id: str, db: Session = Depends(get_db_session)):
    """
    Clear all clipboard items for a user (e.g. after the report is saved/exported).
    """
    count = report_svc.clear_draft_items(db, user_id)
    return {"message": f"Cleared {count} item(s)", "user_id": user_id, "deleted": count}


# ---------------------------------------------------------------------------
# Phase B — Analyst Report schemas
# ---------------------------------------------------------------------------

class ReportCreate(BaseModel):
    user_id: str = Field(..., description="Author user ID")
    title: str
    company_name: str
    ticker: Optional[str] = None
    description: Optional[str] = None
    recommendation: Optional[str] = Field(None, description="buy | sell | hold")
    content_markdown: Optional[str] = None
    image_urls: Optional[List[str]] = Field(default_factory=list)
    source_session_ids: Optional[List[str]] = Field(default_factory=list)
    portfolio_id: Optional[int] = None
    tags: Optional[List[str]] = Field(default_factory=list)


class ReportUpdate(BaseModel):
    title: Optional[str] = None
    company_name: Optional[str] = None
    ticker: Optional[str] = None
    description: Optional[str] = None
    recommendation: Optional[str] = None
    content_markdown: Optional[str] = None
    image_urls: Optional[List[str]] = None
    source_session_ids: Optional[List[str]] = None
    portfolio_id: Optional[int] = None
    tags: Optional[List[str]] = None


class ReportResponse(BaseModel):
    id: int
    user_id: str
    title: str
    company_name: str
    ticker: Optional[str]
    description: Optional[str]
    recommendation: Optional[str]
    content_markdown: Optional[str]
    image_urls: List[str]
    source_session_ids: List[str]
    portfolio_id: Optional[int]
    status: str
    tags: List[str]
    created_at: str
    updated_at: str

    @classmethod
    def from_orm(cls, r: AnalystReport):
        return cls(
            id=r.id,
            user_id=r.user_id,
            title=r.title,
            company_name=r.company_name,
            ticker=r.ticker,
            description=r.description,
            recommendation=r.recommendation.value if r.recommendation else None,
            content_markdown=r.content_markdown,
            image_urls=r.image_urls or [],
            source_session_ids=r.source_session_ids or [],
            portfolio_id=r.portfolio_id,
            status=r.status.value if r.status else "draft",
            tags=r.tags or [],
            created_at=r.created_at.isoformat() if r.created_at else "",
            updated_at=r.updated_at.isoformat() if r.updated_at else "",
        )


class ReportListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[ReportResponse]


# ---------------------------------------------------------------------------
# Phase B — Analyst Report CRUD endpoints
# ---------------------------------------------------------------------------

_VALID_RECOMMENDATIONS = {"buy", "sell", "hold"}


class ReportFromDraftRequest(BaseModel):
    title: str
    company_name: str
    ticker: Optional[str] = None
    description: Optional[str] = None
    recommendation: Optional[str] = Field(None, description="buy | sell | hold")
    source_session_ids: Optional[List[str]] = Field(default_factory=list)
    portfolio_id: Optional[int] = None
    tags: Optional[List[str]] = Field(default_factory=list)
    clear_draft: bool = Field(True, description="Clear clipboard after creating the report")


@router.post("/from-draft/{user_id}", response_model=ReportResponse, status_code=201)
def create_report_from_draft(
    user_id: str,
    payload: ReportFromDraftRequest,
    db: Session = Depends(get_db_session),
):
    """
    One-shot endpoint for the creation tab.

    Reads all the user's staged clipboard items, assembles them into a report:
    - text/summary items → joined as markdown sections (label becomes heading)
    - image items → collected as image_urls

    The clipboard is cleared afterwards (set clear_draft=false to keep it).
    Returns the saved report with its ID — pass that to GET /reports/{id}/export/pdf.
    """
    if payload.recommendation and payload.recommendation not in _VALID_RECOMMENDATIONS:
        raise HTTPException(status_code=400, detail=f"recommendation must be one of {_VALID_RECOMMENDATIONS}")

    try:
        report = report_svc.create_report_from_draft(
            db=db,
            user_id=user_id,
            title=payload.title,
            company_name=payload.company_name,
            ticker=payload.ticker,
            description=payload.description,
            recommendation=payload.recommendation,
            source_session_ids=payload.source_session_ids,
            portfolio_id=payload.portfolio_id,
            tags=payload.tags,
            clear_draft=payload.clear_draft,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return ReportResponse.from_orm(report)


@router.post("", response_model=ReportResponse, status_code=201)
def create_report(payload: ReportCreate, db: Session = Depends(get_db_session)):
    """
    Save an assembled analyst report. Pass content_markdown (built from clipboard items)
    and image_urls (Cloudinary chart URLs). Status starts as 'draft'.
    """
    if payload.recommendation and payload.recommendation not in _VALID_RECOMMENDATIONS:
        raise HTTPException(status_code=400, detail=f"recommendation must be one of {_VALID_RECOMMENDATIONS}")

    report = report_svc.create_report(
        db=db,
        user_id=payload.user_id,
        title=payload.title,
        company_name=payload.company_name,
        ticker=payload.ticker,
        description=payload.description,
        recommendation=payload.recommendation,
        content_markdown=payload.content_markdown,
        image_urls=payload.image_urls,
        source_session_ids=payload.source_session_ids,
        portfolio_id=payload.portfolio_id,
        tags=payload.tags,
    )
    return ReportResponse.from_orm(report)


@router.get("/repository/stats")
def get_repository_stats(db: Session = Depends(get_db_session)):
    """
    Fund Manager dashboard stats — published reports only.

    Returns:
    - total_published: total report count
    - by_recommendation: { buy, sell, hold, unrated } counts
    - top_companies: top 10 companies by report count
    - top_analysts: top 10 analysts by report count
    - recent_reports: 5 most recently published report cards
    """
    return report_svc.get_repository_stats(db)


@router.get("/user/{user_id}", response_model=ReportListResponse)
def list_user_reports(
    user_id: str,
    status: Optional[str] = Query(None, description="draft | published"),
    company: Optional[str] = None,
    ticker: Optional[str] = None,
    recommendation: Optional[str] = None,
    portfolio_id: Optional[int] = None,
    from_date: Optional[datetime] = Query(None, description="ISO date — earliest created_at"),
    to_date: Optional[datetime] = Query(None, description="ISO date — latest created_at"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db_session),
):
    """List an analyst's own reports with optional filters."""
    result = report_svc.list_reports(
        db=db, user_id=user_id, status=status,
        company=company, ticker=ticker, recommendation=recommendation,
        portfolio_id=portfolio_id, from_date=from_date, to_date=to_date,
        page=page, page_size=page_size,
    )
    return ReportListResponse(
        total=result["total"], page=result["page"], page_size=result["page_size"],
        items=[ReportResponse.from_orm(r) for r in result["items"]],
    )


@router.get("", response_model=ReportListResponse)
def list_published_reports(
    company: Optional[str] = None,
    ticker: Optional[str] = None,
    recommendation: Optional[str] = None,
    author: Optional[str] = Query(None, description="Filter by author user_id"),
    portfolio_id: Optional[int] = None,
    from_date: Optional[datetime] = Query(None, description="ISO date — earliest created_at"),
    to_date: Optional[datetime] = Query(None, description="ISO date — latest created_at"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db_session),
):
    """
    Fund Manager view — all published reports with optional filters.
    """
    result = report_svc.list_reports(
        db=db, user_id=author, status="published",
        company=company, ticker=ticker, recommendation=recommendation,
        portfolio_id=portfolio_id, from_date=from_date, to_date=to_date,
        page=page, page_size=page_size,
    )
    return ReportListResponse(
        total=result["total"], page=result["page"], page_size=result["page_size"],
        items=[ReportResponse.from_orm(r) for r in result["items"]],
    )


@router.get("/search", response_model=ReportListResponse)
def search_reports(
    q: str = Query(..., min_length=2, description="Full-text search query"),
    status: Optional[str] = Query("published", description="draft | published"),
    company: Optional[str] = None,
    ticker: Optional[str] = None,
    recommendation: Optional[str] = None,
    author: Optional[str] = None,
    from_date: Optional[datetime] = Query(None, description="ISO date — earliest created_at"),
    to_date: Optional[datetime] = Query(None, description="ISO date — latest created_at"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db_session),
):
    """
    Full-text search across report title, company, description, and body.
    Combine with company/ticker/recommendation/date filters for precision.
    Powered by SQLite FTS5.
    """
    result = report_svc.search_reports(
        db=db, q=q, status=status, user_id=author,
        company=company, ticker=ticker, recommendation=recommendation,
        from_date=from_date, to_date=to_date,
        page=page, page_size=page_size,
    )
    return ReportListResponse(
        total=result["total"], page=result["page"], page_size=result["page_size"],
        items=[ReportResponse.from_orm(r) for r in result["items"]],
    )


@router.get("/{report_id}", response_model=ReportResponse)
def get_report(report_id: int, db: Session = Depends(get_db_session)):
    """Get a single report by ID."""
    report = report_svc.get_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return ReportResponse.from_orm(report)


@router.put("/{report_id}", response_model=ReportResponse)
def update_report(
    report_id: int,
    payload: ReportUpdate,
    user_id: str,
    db: Session = Depends(get_db_session),
):
    """Update a report's content or metadata. Only the author can edit."""
    if payload.recommendation and payload.recommendation not in _VALID_RECOMMENDATIONS:
        raise HTTPException(status_code=400, detail=f"recommendation must be one of {_VALID_RECOMMENDATIONS}")

    report = report_svc.update_report(
        db=db, report_id=report_id, user_id=user_id,
        patch=payload.model_dump(exclude_none=True),
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found or not owned by user")
    return ReportResponse.from_orm(report)


@router.post("/{report_id}/publish", response_model=ReportResponse)
def publish_report(report_id: int, user_id: str, db: Session = Depends(get_db_session)):
    """Publish a draft report so Fund Managers can see it in the repository."""
    report = report_svc.publish_report(db, report_id, user_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found or not owned by user")
    return ReportResponse.from_orm(report)


@router.post("/{report_id}/unpublish", response_model=ReportResponse)
def unpublish_report(report_id: int, user_id: str, db: Session = Depends(get_db_session)):
    """Revert a published report back to draft."""
    report = report_svc.unpublish_report(db, report_id, user_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found or not owned by user")
    return ReportResponse.from_orm(report)


@router.delete("/{report_id}", status_code=200)
def delete_report(report_id: int, user_id: str, db: Session = Depends(get_db_session)):
    """Permanently delete a report. Only the author can delete."""
    success = report_svc.delete_report(db, report_id, user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Report not found or not owned by user")
    return {"message": "Report deleted", "report_id": report_id}


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------

@router.get("/{report_id}/export/pdf")
def export_report_pdf(report_id: int, db: Session = Depends(get_db_session)):
    """
    Export a report as a downloadable PDF.
    Renders metadata header + markdown body + embedded chart images.
    """
    try:
        from fpdf import FPDF
        import urllib.request
        import tempfile, os
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="PDF export requires fpdf2. Install with: pip install fpdf2",
        )

    report = report_svc.get_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    def _safe(text: str) -> str:
        """Sanitize text to latin-1 range for Helvetica; replace common Unicode."""
        replacements = {
            "\u2014": "--", "\u2013": "-", "\u2018": "'", "\u2019": "'",
            "\u201c": '"', "\u201d": '"', "\u2022": "*", "\u2026": "...",
            "\u00a0": " ", "\u2212": "-",
        }
        for ch, rep in replacements.items():
            text = text.replace(ch, rep)
        return text.encode("latin-1", errors="replace").decode("latin-1")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    w = pdf.epw  # effective page width (page width minus margins)

    # --- Header ---
    pdf.set_font("Helvetica", "B", 20)
    pdf.multi_cell(w, 10, _safe(report.title), align="C")
    pdf.ln(2)

    rec = report.recommendation.value.upper() if report.recommendation else "N/A"
    pdf.set_font("Helvetica", "B", 13)
    if rec == "BUY":
        pdf.set_text_color(0, 128, 0)
    elif rec == "SELL":
        pdf.set_text_color(200, 0, 0)
    else:
        pdf.set_text_color(100, 100, 100)
    pdf.cell(w, 8, f"Recommendation: {rec}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)

    pdf.set_font("Helvetica", "", 10)
    company_line = _safe(report.company_name + (f"  ({report.ticker})" if report.ticker else ""))
    pdf.cell(w, 6, f"Company: {company_line}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(w, 6, f"Author: {_safe(report.user_id)}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(w, 6, f"Date: {report.created_at.strftime('%Y-%m-%d') if report.created_at else ''}", new_x="LMARGIN", new_y="NEXT")
    if report.description:
        pdf.set_font("Helvetica", "I", 10)
        pdf.multi_cell(w, 6, _safe(report.description))
    pdf.ln(4)

    # Divider
    pdf.set_draw_color(180, 180, 180)
    pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + w, pdf.get_y())
    pdf.ln(4)

    # --- Body: markdown rendered as plain paragraphs ---
    if report.content_markdown:
        pdf.set_font("Helvetica", "", 11)
        for line in report.content_markdown.splitlines():
            stripped = line.strip()
            if stripped.startswith("### "):
                pdf.set_font("Helvetica", "B", 12)
                pdf.multi_cell(w, 7, _safe(stripped[4:]))
                pdf.set_font("Helvetica", "", 11)
            elif stripped.startswith("## "):
                pdf.set_font("Helvetica", "B", 13)
                pdf.multi_cell(w, 8, _safe(stripped[3:]))
                pdf.set_font("Helvetica", "", 11)
            elif stripped.startswith("# "):
                pdf.set_font("Helvetica", "B", 15)
                pdf.multi_cell(w, 9, _safe(stripped[2:]))
                pdf.set_font("Helvetica", "", 11)
            elif stripped.startswith("- ") or stripped.startswith("* "):
                pdf.multi_cell(w, 6, _safe(f"  * {stripped[2:]}"))
            elif stripped == "":
                pdf.ln(3)
            else:
                pdf.multi_cell(w, 6, _safe(stripped))

    # --- Images ---
    if report.image_urls:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(w, 8, "Charts & Visuals", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        tmp_files = []
        for url in report.image_urls:
            try:
                suffix = ".png" if "png" in url.lower() else ".jpg"
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                urllib.request.urlretrieve(url, tmp.name)
                tmp_files.append(tmp.name)
                pdf.image(tmp.name, x=10, w=w)
                pdf.ln(4)
            except Exception:
                pdf.set_font("Helvetica", "I", 9)
                pdf.multi_cell(w, 5, _safe(f"[Image unavailable: {url}]"))

        for f in tmp_files:
            try:
                os.remove(f)
            except Exception:
                pass

    # --- Output ---
    pdf_bytes = pdf.output()
    filename = f"report_{report_id}_{report.company_name.replace(' ', '_')}.pdf"

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
