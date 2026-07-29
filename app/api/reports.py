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
    portfolio_id: Optional[int] = Field(None, description="Portfolio this clip belongs to")
    item_type: str = Field(..., description="text | image | summary")
    content: Optional[str] = Field(None, description="Markdown text or summary body")
    html: Optional[str] = Field(None, description="Rich-text HTML for this clip (sanitized server-side)")
    image_url: Optional[str] = Field(None, description="Cloudinary URL for chart images")
    source: Optional[str] = Field(None, description="rag | quant | summary")
    session_id: Optional[str] = Field(None, description="Originating chat session ID")
    label: Optional[str] = Field(None, description="User-editable label shown in creation tab")
    sort_order: Optional[int] = Field(0, description="Display position (0 = top)")


class DraftItemUpdate(BaseModel):
    label: Optional[str] = None
    content: Optional[str] = None
    html: Optional[str] = None
    sort_order: Optional[int] = None


class ReorderRequest(BaseModel):
    ordered_ids: List[int] = Field(..., description="Item IDs in the desired display order")


class DraftItemResponse(BaseModel):
    id: int
    user_id: str
    portfolio_id: Optional[int]
    item_type: str
    content: Optional[str]
    html: Optional[str]
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
            portfolio_id=obj.portfolio_id,
            item_type=obj.item_type,
            content=obj.content,
            html=obj.html,
            image_url=obj.image_url,
            source=obj.source,
            session_id=obj.session_id,
            label=obj.label,
            sort_order=obj.sort_order or 0,
            created_at=obj.created_at.isoformat() + "Z" if obj.created_at else "",
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
        portfolio_id=payload.portfolio_id,
        item_type=payload.item_type,
        content=payload.content,
        html=payload.html,
        image_url=payload.image_url,
        source=payload.source,
        session_id=payload.session_id,
        label=payload.label,
        sort_order=payload.sort_order or 0,
    )
    return DraftItemResponse.from_orm(item)


@router.get("/draft/items/{user_id}", response_model=List[DraftItemResponse])
def get_draft_items(
    user_id: str,
    portfolio_id: Optional[int] = Query(None, description="Filter clips by portfolio"),
    db: Session = Depends(get_db_session),
):
    """
    Retrieve clipboard items for the given user, scoped to a portfolio when portfolio_id is provided.
    Sorted by sort_order then created_at.
    """
    items = report_svc.get_draft_items(db, user_id, portfolio_id=portfolio_id)
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
        html=payload.html,
        sort_order=payload.sort_order,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Draft item not found")
    return DraftItemResponse.from_orm(item)


@router.post("/draft/items/reorder", response_model=List[DraftItemResponse])
def reorder_draft_items(
    payload: ReorderRequest,
    user_id: str,
    portfolio_id: Optional[int] = Query(None),
    db: Session = Depends(get_db_session),
):
    """
    Reorder clipboard items by supplying all item IDs in the desired display order.
    Pass user_id (and optionally portfolio_id) as query parameters.
    """
    items = report_svc.reorder_draft_items(db, user_id, payload.ordered_ids, portfolio_id=portfolio_id)
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
def clear_draft_items(
    user_id: str,
    portfolio_id: Optional[int] = Query(None, description="Clear only clips for this portfolio"),
    db: Session = Depends(get_db_session),
):
    """
    Clear clipboard items for a user. When portfolio_id is supplied, only clips for that portfolio are removed.
    """
    count = report_svc.clear_draft_items(db, user_id, portfolio_id=portfolio_id)
    return {"message": f"Cleared {count} item(s)", "user_id": user_id, "deleted": count}


# ---------------------------------------------------------------------------
# Phase B — Analyst Report schemas
# ---------------------------------------------------------------------------

class ReportCreate(BaseModel):
    user_id: str = Field(..., description="Author user ID")
    company_name: str
    ticker: Optional[str] = None
    content_markdown: Optional[str] = None
    content_html: Optional[str] = Field(None, description="Rich-text HTML — primary format, sanitized server-side")
    image_urls: Optional[List[str]] = Field(default_factory=list)
    source_session_ids: Optional[List[str]] = Field(default_factory=list)
    portfolio_id: Optional[int] = None


class ReportUpdate(BaseModel):
    company_name: Optional[str] = None
    ticker: Optional[str] = None
    content_markdown: Optional[str] = None
    content_html: Optional[str] = None
    image_urls: Optional[List[str]] = None
    source_session_ids: Optional[List[str]] = None
    portfolio_id: Optional[int] = None


class ReportResponse(BaseModel):
    id: int
    user_id: str
    author_name: str
    company_name: str
    ticker: Optional[str]
    content_markdown: Optional[str]
    content_html: Optional[str]
    image_urls: List[str]
    source_session_ids: List[str]
    portfolio_id: Optional[int]
    status: str
    created_at: str
    updated_at: str

    @classmethod
    def from_orm(cls, r: AnalystReport, db: Session = None):
        return cls(
            id=r.id,
            user_id=r.user_id,
            author_name=report_svc.resolve_author_name(db, r.user_id) if db else r.user_id,
            company_name=r.company_name,
            ticker=r.ticker,
            content_markdown=r.content_markdown,
            content_html=r.content_html,
            image_urls=r.image_urls or [],
            source_session_ids=r.source_session_ids or [],
            portfolio_id=r.portfolio_id,
            status=r.status.value if r.status else "draft",
            created_at=r.created_at.isoformat() + "Z" if r.created_at else "",
            updated_at=r.updated_at.isoformat() + "Z" if r.updated_at else "",
        )


class ReportListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[ReportResponse]


# ---------------------------------------------------------------------------
# Phase B — Analyst Report CRUD endpoints
# ---------------------------------------------------------------------------

class ReportFromDraftRequest(BaseModel):
    company_name: str
    ticker: Optional[str] = None
    portfolio_id: Optional[int] = None
    source_session_ids: Optional[List[str]] = Field(default_factory=list)
    clear_draft: bool = Field(True, description="Clear clipboard after creating the report")


@router.post("/from-draft/{user_id}", response_model=ReportResponse, status_code=201)
def create_report_from_draft(
    user_id: str,
    payload: ReportFromDraftRequest,
    db: Session = Depends(get_db_session),
):
    """
    One-shot endpoint for the creation tab. Reads all staged clipboard items,
    assembles them into a report, and optionally clears the clipboard.
    """
    try:
        report = report_svc.create_report_from_draft(
            db=db,
            user_id=user_id,
            company_name=payload.company_name,
            ticker=payload.ticker,
            source_session_ids=payload.source_session_ids,
            portfolio_id=payload.portfolio_id,
            clear_draft=payload.clear_draft,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return ReportResponse.from_orm(report, db)


@router.post("", response_model=ReportResponse, status_code=201)
def create_report(payload: ReportCreate, db: Session = Depends(get_db_session)):
    """Save an assembled analyst report. Status starts as 'draft'."""
    report = report_svc.create_report(
        db=db,
        user_id=payload.user_id,
        company_name=payload.company_name,
        ticker=payload.ticker,
        content_markdown=payload.content_markdown,
        content_html=payload.content_html,
        image_urls=payload.image_urls,
        source_session_ids=payload.source_session_ids,
        portfolio_id=payload.portfolio_id,
    )
    return ReportResponse.from_orm(report, db)


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
        company=company, ticker=ticker,
        portfolio_id=portfolio_id, from_date=from_date, to_date=to_date,
        page=page, page_size=page_size,
    )
    return ReportListResponse(
        total=result["total"], page=result["page"], page_size=result["page_size"],
        items=[ReportResponse.from_orm(r, db) for r in result["items"]],
    )


@router.get("", response_model=ReportListResponse)
def list_published_reports(
    company: Optional[str] = None,
    ticker: Optional[str] = None,
    author: Optional[str] = Query(None, description="Filter by author user_id"),
    portfolio_id: Optional[int] = None,
    from_date: Optional[datetime] = Query(None, description="ISO date — earliest created_at"),
    to_date: Optional[datetime] = Query(None, description="ISO date — latest created_at"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db_session),
):
    """Fund Manager view — all published reports with optional filters."""
    result = report_svc.list_reports(
        db=db, user_id=author, status="published",
        company=company, ticker=ticker,
        portfolio_id=portfolio_id, from_date=from_date, to_date=to_date,
        page=page, page_size=page_size,
    )
    return ReportListResponse(
        total=result["total"], page=result["page"], page_size=result["page_size"],
        items=[ReportResponse.from_orm(r, db) for r in result["items"]],
    )


@router.get("/search", response_model=ReportListResponse)
def search_reports(
    q: str = Query(..., min_length=2, description="Full-text search query"),
    status: Optional[str] = Query("published", description="draft | published"),
    company: Optional[str] = None,
    ticker: Optional[str] = None,
    author: Optional[str] = None,
    from_date: Optional[datetime] = Query(None, description="ISO date — earliest created_at"),
    to_date: Optional[datetime] = Query(None, description="ISO date — latest created_at"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db_session),
):
    """Full-text search across report company and body. Powered by SQLite FTS5."""
    result = report_svc.search_reports(
        db=db, q=q, status=status, user_id=author,
        company=company, ticker=ticker,
        from_date=from_date, to_date=to_date,
        page=page, page_size=page_size,
    )
    return ReportListResponse(
        total=result["total"], page=result["page"], page_size=result["page_size"],
        items=[ReportResponse.from_orm(r, db) for r in result["items"]],
    )


@router.get("/{report_id}", response_model=ReportResponse)
def get_report(report_id: int, db: Session = Depends(get_db_session)):
    """Get a single report by ID."""
    report = report_svc.get_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return ReportResponse.from_orm(report, db)


@router.put("/{report_id}", response_model=ReportResponse)
def update_report(
    report_id: int,
    payload: ReportUpdate,
    user_id: str,
    db: Session = Depends(get_db_session),
):
    """Update a report's content or metadata. Only the author can edit."""
    report = report_svc.update_report(
        db=db, report_id=report_id, user_id=user_id,
        patch=payload.model_dump(exclude_none=True),
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found or not owned by user")
    return ReportResponse.from_orm(report, db)


@router.post("/{report_id}/publish", response_model=ReportResponse)
def publish_report(report_id: int, user_id: str, db: Session = Depends(get_db_session)):
    """Publish a draft report so Fund Managers can see it in the repository."""
    report = report_svc.publish_report(db, report_id, user_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found or not owned by user")
    return ReportResponse.from_orm(report, db)


@router.post("/{report_id}/unpublish", response_model=ReportResponse)
def unpublish_report(report_id: int, user_id: str, db: Session = Depends(get_db_session)):
    """Revert a published report back to draft."""
    report = report_svc.unpublish_report(db, report_id, user_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found or not owned by user")
    return ReportResponse.from_orm(report, db)


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
def export_report_pdf(report_id: int, inline: bool = False, db: Session = Depends(get_db_session)):
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
    company_line = _safe(report.company_name + (f"  ({report.ticker})" if report.ticker else ""))
    pdf.set_font("Helvetica", "B", 20)
    pdf.multi_cell(w, 10, company_line, align="C")
    pdf.ln(2)

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(w, 6, f"Author: {_safe(report.user_id)}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(w, 6, f"Date: {report.created_at.strftime('%Y-%m-%d') if report.created_at else ''}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Divider
    pdf.set_draw_color(180, 180, 180)
    pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + w, pdf.get_y())
    pdf.ln(4)

    # --- Body ---
    if report.content_html:
        # Rich-text path — walks the sanitized editor HTML directly (see
        # pdf_render.py) instead of fpdf2's own write_html(), which ignores
        # inline color/background on anything but heading tags, doesn't
        # understand rgb()/rgba() colors, and crashes on TipTap's
        # width="450px"-style <img> attributes.
        from app.services.pdf_render import render_content_html
        render_content_html(pdf, _safe(report.content_html))
    elif report.content_markdown:
        import re

        def render_inline(text: str) -> str:
            """Strip inline markdown markers for safe() — bold/italic handled via font switching."""
            text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
            text = re.sub(r'__(.+?)__', r'\1', text)
            text = re.sub(r'\*(.+?)\*', r'\1', text)
            text = re.sub(r'_(.+?)_', r'\1', text)
            text = re.sub(r'`(.+?)`', r'\1', text)
            return text

        def has_bold(text: str) -> bool:
            return bool(re.search(r'\*\*(.+?)\*\*|__(.+?)__', text))

        def has_italic(text: str) -> bool:
            return bool(re.search(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)', text))

        lines = report.content_markdown.splitlines()
        i = 0
        list_counter = 0  # for ordered lists

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # ── Headings ──────────────────────────────────────────────────
            if stripped.startswith("#### "):
                pdf.set_font("Helvetica", "B", 11)
                pdf.multi_cell(w, 7, _safe(render_inline(stripped[5:])))
                pdf.set_font("Helvetica", "", 11)
            elif stripped.startswith("### "):
                pdf.set_font("Helvetica", "B", 12)
                pdf.multi_cell(w, 7, _safe(render_inline(stripped[4:])))
                pdf.set_font("Helvetica", "", 11)
            elif stripped.startswith("## "):
                pdf.ln(2)
                pdf.set_font("Helvetica", "B", 14)
                pdf.multi_cell(w, 9, _safe(render_inline(stripped[3:])))
                pdf.set_font("Helvetica", "", 11)
                pdf.ln(1)
            elif stripped.startswith("# "):
                pdf.ln(2)
                pdf.set_font("Helvetica", "B", 16)
                pdf.multi_cell(w, 10, _safe(render_inline(stripped[2:])))
                pdf.set_font("Helvetica", "", 11)
                pdf.ln(1)

            # ── Horizontal rule ───────────────────────────────────────────
            elif stripped in ("---", "***", "___"):
                pdf.set_draw_color(180, 180, 180)
                pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + w, pdf.get_y())
                pdf.ln(4)

            # ── Ordered list ──────────────────────────────────────────────
            elif re.match(r'^\d+\.\s', stripped):
                m = re.match(r'^(\d+)\.\s+(.*)', stripped)
                if m:
                    num, text = m.group(1), m.group(2)
                    indent = "    "
                    font = "B" if has_bold(text) else ""
                    pdf.set_font("Helvetica", font, 11)
                    pdf.multi_cell(w, 6, _safe(f"{indent}{num}. {render_inline(text)}"))
                    pdf.set_font("Helvetica", "", 11)

            # ── Unordered list ────────────────────────────────────────────
            elif re.match(r'^[-*+]\s', stripped):
                text = re.sub(r'^[-*+]\s+', '', stripped)
                indent = "    "
                # Detect nesting from raw indentation
                raw_indent = len(line) - len(line.lstrip())
                if raw_indent >= 4:
                    indent = "        "
                font = "B" if has_bold(text) else ""
                pdf.set_font("Helvetica", font, 11)
                pdf.multi_cell(w, 6, _safe(f"{indent}* {render_inline(text)}"))
                pdf.set_font("Helvetica", "", 11)

            # ── Blockquote ────────────────────────────────────────────────
            elif stripped.startswith("> "):
                pdf.set_font("Helvetica", "I", 10)
                pdf.set_text_color(100, 100, 100)
                pdf.multi_cell(w - 8, 6, _safe(render_inline(stripped[2:])), new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(0, 0, 0)
                pdf.set_font("Helvetica", "", 11)

            # ── Code block (``` ... ```) ───────────────────────────────────
            elif stripped.startswith("```"):
                code_lines = []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                if code_lines:
                    pdf.set_font("Courier", "", 9)
                    pdf.set_fill_color(245, 245, 245)
                    code_text = "\n".join(code_lines)
                    pdf.multi_cell(w, 5, _safe(code_text), fill=True)
                    pdf.set_fill_color(255, 255, 255)
                    pdf.set_font("Helvetica", "", 11)
                    pdf.ln(2)

            # ── Markdown table ────────────────────────────────────────────
            elif stripped.startswith("|") and "|" in stripped[1:]:
                # Collect all table rows
                table_rows = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    row = lines[i].strip()
                    # Skip separator rows (|---|---|)
                    if not re.match(r'^\|[\s\-:|]+\|$', row):
                        cells = [c.strip() for c in row.strip("|").split("|")]
                        table_rows.append(cells)
                    i += 1
                i -= 1  # outer loop will increment

                if table_rows:
                    col_count = max(len(r) for r in table_rows)
                    col_w = w / col_count
                    is_header = True
                    pdf.set_fill_color(240, 240, 240)
                    for row in table_rows:
                        pdf.set_font("Helvetica", "B" if is_header else "", 10)
                        x_start = pdf.get_x()
                        y_start = pdf.get_y()
                        max_h = 6
                        for ci, cell in enumerate(row[:col_count]):
                            pdf.set_xy(x_start + ci * col_w, y_start)
                            pdf.multi_cell(col_w, max_h, _safe(render_inline(cell)),
                                           border=1, fill=is_header, new_x="RIGHT", new_y="TOP")
                        pdf.set_xy(x_start, y_start + max_h)
                        is_header = False
                    pdf.set_fill_color(255, 255, 255)
                    pdf.set_font("Helvetica", "", 11)
                    pdf.ln(3)

            # ── HTML table (kept by turndown) ─────────────────────────────
            elif stripped.lower().startswith("<table"):
                # Collect raw HTML table lines and skip them (can't render HTML natively)
                while i < len(lines) and not lines[i].strip().lower().startswith("</table"):
                    i += 1
                pdf.set_font("Helvetica", "I", 9)
                pdf.set_text_color(130, 130, 130)
                pdf.multi_cell(w, 5, "[Table — see report in browser for full view]")
                pdf.set_text_color(0, 0, 0)
                pdf.set_font("Helvetica", "", 11)

            # ── Blank line ────────────────────────────────────────────────
            elif stripped == "":
                pdf.ln(3)

            # ── Regular paragraph (with inline bold/italic detection) ─────
            else:
                if has_bold(stripped):
                    pdf.set_font("Helvetica", "B", 11)
                elif has_italic(stripped):
                    pdf.set_font("Helvetica", "I", 11)
                else:
                    pdf.set_font("Helvetica", "", 11)
                pdf.multi_cell(w, 6, _safe(render_inline(stripped)))
                pdf.set_font("Helvetica", "", 11)

            i += 1

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
    disposition = f'inline; filename="{filename}"' if inline else f'attachment; filename="{filename}"'

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
