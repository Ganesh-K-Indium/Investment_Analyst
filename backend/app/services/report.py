"""
Report service — draft clipboard CRUD + analyst report CRUD + search
"""
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from sqlalchemy import select, delete as sa_delete, func, text
from sqlalchemy.ext.asyncio import AsyncSession
import markdown as _markdown
from markdownify import markdownify as _html_to_markdown
from app.database.models import ReportDraftItem, AnalystReport, ReportStatus, User
from app.services.html_sanitize import sanitize_report_html

_VALID_ITEM_TYPES = {"text", "image", "summary"}
_VALID_SOURCES = {"rag", "quant", "summary", None}


def _markdown_to_html(md: str) -> str:
    return _markdown.markdown(md or "", extensions=["tables", "fenced_code"])


def _derive_markdown(content_html: Optional[str]) -> Optional[str]:
    """Authoritative Markdown (for FTS + legacy consumers), derived from sanitized HTML."""
    if not content_html:
        return None
    return _html_to_markdown(content_html, heading_style="ATX").strip()


async def _count(db: AsyncSession, stmt) -> int:
    """Run a count() over an arbitrary select() statement's filters."""
    result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Draft clipboard
# ---------------------------------------------------------------------------

async def add_draft_item(
    db: AsyncSession,
    user_id: str,
    item_type: str,
    portfolio_id: Optional[int] = None,
    content: Optional[str] = None,
    html: Optional[str] = None,
    image_url: Optional[str] = None,
    source: Optional[str] = None,
    session_id: Optional[str] = None,
    label: Optional[str] = None,
    sort_order: int = 0,
) -> ReportDraftItem:
    clean_html = sanitize_report_html(html) if html else None
    item = ReportDraftItem(
        user_id=user_id,
        portfolio_id=portfolio_id,
        item_type=item_type,
        content=content or (_derive_markdown(clean_html) if clean_html else None),
        html=clean_html,
        image_url=image_url,
        source=source,
        session_id=session_id,
        label=label,
        sort_order=sort_order,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def get_draft_items(
    db: AsyncSession,
    user_id: str,
    portfolio_id: Optional[int] = None,
) -> List[ReportDraftItem]:
    q = select(ReportDraftItem).where(ReportDraftItem.user_id == user_id)
    if portfolio_id is not None:
        q = q.where(ReportDraftItem.portfolio_id == portfolio_id)
    q = q.order_by(ReportDraftItem.sort_order, ReportDraftItem.created_at)
    result = await db.execute(q)
    return list(result.scalars().all())


async def get_draft_item(db: AsyncSession, item_id: int, user_id: str) -> Optional[ReportDraftItem]:
    result = await db.execute(
        select(ReportDraftItem).where(ReportDraftItem.id == item_id, ReportDraftItem.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def update_draft_item(
    db: AsyncSession,
    item_id: int,
    user_id: str,
    label: Optional[str] = None,
    content: Optional[str] = None,
    html: Optional[str] = None,
    sort_order: Optional[int] = None,
) -> Optional[ReportDraftItem]:
    item = await get_draft_item(db, item_id, user_id)
    if not item:
        return None
    if label is not None:
        item.label = label
    if html is not None:
        item.html = sanitize_report_html(html)
    if content is not None:
        item.content = content
    if sort_order is not None:
        item.sort_order = sort_order
    await db.commit()
    await db.refresh(item)
    return item


async def delete_draft_item(db: AsyncSession, item_id: int, user_id: str) -> bool:
    item = await get_draft_item(db, item_id, user_id)
    if not item:
        return False
    await db.delete(item)
    await db.commit()
    return True


async def clear_draft_items(
    db: AsyncSession,
    user_id: str,
    portfolio_id: Optional[int] = None,
) -> int:
    q = select(ReportDraftItem).where(ReportDraftItem.user_id == user_id)
    if portfolio_id is not None:
        q = q.where(ReportDraftItem.portfolio_id == portfolio_id)
    count = await _count(db, q)

    del_stmt = sa_delete(ReportDraftItem).where(ReportDraftItem.user_id == user_id)
    if portfolio_id is not None:
        del_stmt = del_stmt.where(ReportDraftItem.portfolio_id == portfolio_id)
    await db.execute(del_stmt)
    await db.commit()
    return count


async def create_report_from_draft(
    db: AsyncSession,
    user_id: str,
    company_name: str,
    ticker: Optional[str] = None,
    source_session_ids: Optional[List[str]] = None,
    portfolio_id: Optional[int] = None,
    clear_draft: bool = True,
) -> AnalystReport:
    """
    Assemble a report from the user's clipboard for this portfolio, then
    optionally clear it. Text/summary items become content_markdown sections;
    image items become image_urls.
    """
    items = await get_draft_items(db, user_id, portfolio_id=portfolio_id)
    if not items:
        raise ValueError("No draft items found — clipboard is empty for this portfolio")

    html_sections: List[str] = []
    image_urls: List[str] = []

    for item in items:
        if item.item_type == "image":
            if item.image_url:
                image_urls.append(item.image_url)
        else:
            heading = item.label or item.item_type.capitalize()
            body_html = item.html or _markdown_to_html(item.content or "")
            html_sections.append(f"<h2>{heading}</h2>{body_html}")

    content_html = "<hr/>".join(html_sections) if html_sections else None
    content_markdown = _derive_markdown(content_html)
    draft_session_ids = list({i.session_id for i in items if i.session_id})
    merged_session_ids = list(set((source_session_ids or []) + draft_session_ids))

    report = await create_report(
        db=db,
        user_id=user_id,
        company_name=company_name,
        ticker=ticker,
        content_markdown=content_markdown,
        content_html=content_html,
        image_urls=image_urls,
        source_session_ids=merged_session_ids,
        portfolio_id=portfolio_id,
    )

    if clear_draft:
        await clear_draft_items(db, user_id, portfolio_id=portfolio_id)

    return report


async def reorder_draft_items(
    db: AsyncSession,
    user_id: str,
    ordered_ids: List[int],
    portfolio_id: Optional[int] = None,
) -> List[ReportDraftItem]:
    result = await db.execute(
        select(ReportDraftItem).where(
            ReportDraftItem.user_id == user_id,
            ReportDraftItem.id.in_(ordered_ids),
        )
    )
    items_map = {item.id: item for item in result.scalars().all()}
    for position, item_id in enumerate(ordered_ids):
        if item_id in items_map:
            items_map[item_id].sort_order = position
    await db.commit()
    return await get_draft_items(db, user_id, portfolio_id=portfolio_id)


# ---------------------------------------------------------------------------
# Analyst report CRUD
# ---------------------------------------------------------------------------

async def create_report(
    db: AsyncSession,
    user_id: str,
    company_name: str,
    ticker: Optional[str] = None,
    content_markdown: Optional[str] = None,
    content_html: Optional[str] = None,
    image_urls: Optional[List[str]] = None,
    source_session_ids: Optional[List[str]] = None,
    portfolio_id: Optional[int] = None,
) -> AnalystReport:
    clean_html = sanitize_report_html(content_html) if content_html else None
    report = AnalystReport(
        user_id=user_id,
        company_name=company_name,
        ticker=ticker,
        content_markdown=_derive_markdown(clean_html) or content_markdown,
        content_html=clean_html,
        image_urls=image_urls or [],
        source_session_ids=source_session_ids or [],
        portfolio_id=portfolio_id,
        status=ReportStatus.DRAFT,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


async def get_report(db: AsyncSession, report_id: int) -> Optional[AnalystReport]:
    result = await db.execute(select(AnalystReport).where(AnalystReport.id == report_id))
    return result.scalar_one_or_none()


async def get_report_by_user(db: AsyncSession, report_id: int, user_id: str) -> Optional[AnalystReport]:
    result = await db.execute(
        select(AnalystReport).where(AnalystReport.id == report_id, AnalystReport.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def update_report(
    db: AsyncSession,
    report_id: int,
    user_id: str,
    patch: Dict[str, Any],
) -> Optional[AnalystReport]:
    report = await get_report_by_user(db, report_id, user_id)
    if not report:
        return None

    allowed = {"company_name", "ticker", "content_markdown", "content_html", "image_urls", "source_session_ids", "portfolio_id"}
    for field, value in patch.items():
        if field not in allowed or value is None:
            continue
        if field == "content_html":
            value = sanitize_report_html(value)
        setattr(report, field, value)

    if "content_html" in patch and patch["content_html"] is not None:
        report.content_markdown = _derive_markdown(report.content_html)

    report.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(report)
    return report


async def publish_report(db: AsyncSession, report_id: int, user_id: str) -> Optional[AnalystReport]:
    report = await get_report_by_user(db, report_id, user_id)
    if not report:
        return None
    report.status = ReportStatus.PUBLISHED
    report.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(report)
    return report


async def unpublish_report(db: AsyncSession, report_id: int, user_id: str) -> Optional[AnalystReport]:
    report = await get_report_by_user(db, report_id, user_id)
    if not report:
        return None
    report.status = ReportStatus.DRAFT
    report.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(report)
    return report


async def delete_report(db: AsyncSession, report_id: int, user_id: str) -> bool:
    report = await get_report_by_user(db, report_id, user_id)
    if not report:
        return False
    await db.delete(report)
    await db.commit()
    return True


async def list_reports(
    db: AsyncSession,
    user_id: Optional[str] = None,
    status: Optional[str] = None,
    company: Optional[str] = None,
    ticker: Optional[str] = None,
    portfolio_id: Optional[int] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    q = select(AnalystReport)
    if user_id:
        q = q.where(AnalystReport.user_id == user_id)
    if status:
        q = q.where(AnalystReport.status == ReportStatus(status))
    if company:
        q = q.where(AnalystReport.company_name.ilike(f"%{company}%"))
    if ticker:
        q = q.where(AnalystReport.ticker.ilike(f"%{ticker}%"))
    if portfolio_id:
        q = q.where(AnalystReport.portfolio_id == portfolio_id)
    if from_date:
        q = q.where(AnalystReport.created_at >= from_date)
    if to_date:
        q = q.where(AnalystReport.created_at <= to_date)

    total = await _count(db, q)
    result = await db.execute(
        q.order_by(AnalystReport.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    reports = list(result.scalars().all())
    return {"total": total, "page": page, "page_size": page_size, "items": reports}


async def search_reports(
    db: AsyncSession,
    q: str,
    status: Optional[str] = "published",
    user_id: Optional[str] = None,
    company: Optional[str] = None,
    ticker: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """Full-text search backed by Postgres tsvector (see migration 014_postgres_fulltext_search)."""
    fts_result = await db.execute(
        text(
            "SELECT id FROM analyst_reports "
            "WHERE search_vector @@ plainto_tsquery('english', :q) "
            "ORDER BY ts_rank(search_vector, plainto_tsquery('english', :q)) DESC"
        ),
        {"q": q},
    )
    matched_ids = [row[0] for row in fts_result.fetchall()]

    if not matched_ids:
        return {"total": 0, "page": page, "page_size": page_size, "items": []}

    query = select(AnalystReport).where(AnalystReport.id.in_(matched_ids))

    if status:
        query = query.where(AnalystReport.status == ReportStatus(status))
    if user_id:
        query = query.where(AnalystReport.user_id == user_id)
    if company:
        query = query.where(AnalystReport.company_name.ilike(f"%{company}%"))
    if ticker:
        query = query.where(AnalystReport.ticker.ilike(f"%{ticker}%"))
    if from_date:
        query = query.where(AnalystReport.created_at >= from_date)
    if to_date:
        query = query.where(AnalystReport.created_at <= to_date)

    total = await _count(db, query)
    result = await db.execute(
        query.order_by(AnalystReport.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    items = list(result.scalars().all())
    return {"total": total, "page": page, "page_size": page_size, "items": items}


async def resolve_author_name(db: AsyncSession, user_id: str) -> str:
    user = None
    try:
        result = await db.execute(select(User).where(User.id == int(user_id)))
        user = result.scalar_one_or_none()
    except (ValueError, TypeError):
        result = await db.execute(select(User).where(User.username == user_id))
        user = result.scalar_one_or_none()
    if user:
        return (user.full_name or '').strip() or user.username
    return user_id


async def get_repository_stats(db: AsyncSession) -> Dict[str, Any]:
    """Aggregate stats for the Fund Manager dashboard — published reports only."""
    base_filter = AnalystReport.status == ReportStatus.PUBLISHED

    total = await _count(db, select(AnalystReport).where(base_filter))

    top_companies_result = await db.execute(
        select(AnalystReport.company_name, func.count())
        .where(base_filter)
        .group_by(AnalystReport.company_name)
        .order_by(func.count().desc())
        .limit(10)
    )
    top_companies = [{"company": row[0], "count": row[1]} for row in top_companies_result.all()]

    top_analysts_result = await db.execute(
        select(AnalystReport.user_id, func.count())
        .where(base_filter)
        .group_by(AnalystReport.user_id)
        .order_by(func.count().desc())
        .limit(10)
    )
    top_analysts = [{"analyst": row[0], "count": row[1]} for row in top_analysts_result.all()]

    # Published in the last 2 days
    two_days_ago = datetime.utcnow() - timedelta(days=2)
    recent_result = await db.execute(
        select(AnalystReport)
        .where(base_filter, AnalystReport.created_at >= two_days_ago)
        .order_by(AnalystReport.created_at.desc())
        .limit(10)
    )
    recent_published = list(recent_result.scalars().all())

    async def _report_dict(r):
        return {
            "id": r.id,
            "company_name": r.company_name,
            "ticker": r.ticker,
            "author": await resolve_author_name(db, r.user_id),
            "created_at": (r.created_at.isoformat() + "Z") if r.created_at else None,
            "updated_at": (r.updated_at.isoformat() + "Z") if r.updated_at else None,
        }

    return {
        "total_published": total,
        "top_companies": top_companies,
        "top_analysts": top_analysts,
        "recent_reports": [await _report_dict(r) for r in recent_published],
    }
