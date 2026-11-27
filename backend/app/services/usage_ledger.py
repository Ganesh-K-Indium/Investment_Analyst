"""Persist token use and report it against the user's monthly budget. Costs are ESTIMATES from list prices (app/services/pricing.py).

Every model and embedding call, whatever the provider, reports its tokens to the active usage tracker (app/utils/usage_tracker.py).
What is stored, and when:
  - every signed-in API request runs inside `metered` (app/core/usage_middleware.py) and stores what it used when it ends;
  - flows that report their own spend (chat answers, ALPHA, quant) store it right away and mark it stored, so the request does not
    count it again;
  - background work that outlives a request (document processing, SEC ingestion, scheduled runs) stores its own (`detached`).
A model whose price is unknown is stored with its tokens and `priced = False`, never as a silent $0."""
import contextvars
import datetime as dt
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import UsageEvent
from app.services import pricing
from app.utils.usage_tracker import UsageTracker, get_current_tracker, track_usage

logger = logging.getLogger("app.services.usage_ledger")

# The background run (AnalysisTask id) the current request is part of, set by the job runners, so a run's spend can be
# added up for the dashboards. None for direct requests.
current_task_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("usage_task_id", default=None)
# The portfolio the current request works in (set by app/services/run_context.load), so spend adds up per portfolio for
# everything done in it: chats, comparisons, ALPHA and quant alike.
current_portfolio_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("usage_portfolio_id", default=None)


def cost(model: str, prompt: int, completion: int) -> float:
    """Estimated USD from list prices; 0 when the model's price is unknown (use pricing.cost to tell the two apart)."""
    return pricing.cost(model, prompt, completion) or 0.0


async def persist(db: AsyncSession, user_id: str, tracker: UsageTracker) -> Dict[str, Any]:
    """Store what a tracker recorded (one row per role+model) and return the per-request summary for the UI."""
    agg: Dict[tuple, list] = {}
    for e in tracker.entries:
        if e.stage == "graph_total" and len(tracker.entries) > 1:  # the aggregate row; the per-role rows are the detail
            continue
        a = agg.setdefault((e.stage, e.model), [0, 0])
        a[0] += e.prompt_tokens
        a[1] += e.completion_tokens
    agg = {k: v for k, v in agg.items() if v[0] or v[1]}     # timing-only entries (text extraction) cost nothing
    total_cost, unpriced = 0.0, 0
    for (role, model), (p, c) in agg.items():
        usd = pricing.cost(model, p, c)
        if usd is None:
            unpriced += p + c
        total_cost += usd or 0.0
        db.add(UsageEvent(user_id=str(user_id), run_label=tracker.run_label[:200], role=role[:120], model=(model or "unknown")[:120],
                          prompt_tokens=p, completion_tokens=c, cost_usd=usd or 0.0, priced=usd is not None,
                          task_id=current_task_id.get(), portfolio_id=current_portfolio_id.get()))
    if agg:
        await db.commit()
    tracker.persisted = True
    p, c = sum(v[0] for v in agg.values()), sum(v[1] for v in agg.values())
    return {"total_tokens": p + c, "prompt_tokens": p, "completion_tokens": c,
            "estimated_cost_usd": round(total_cost, 4), "unpriced_tokens": unpriced}


@asynccontextmanager
async def metered(user_id: Optional[str], label: str, *, portfolio_id: Optional[int] = None,
                  detached: bool = False) -> AsyncIterator[Optional[UsageTracker]]:
    """Count every model and embedding call made inside the block and store it for `user_id` when the block ends.

    Inside an already metered block it adds nothing (the outer block stores it), unless `detached`: background work that may
    outlive the request that started it stores its own usage."""
    if not user_id:
        yield None
        return
    if not detached and get_current_tracker() is not None:
        yield get_current_tracker()
        return
    ctx = track_usage(label[:200], detached=detached)
    tracker = ctx.__enter__()
    token = current_portfolio_id.set(portfolio_id) if portfolio_id is not None else None
    try:
        yield tracker
    finally:
        try:
            if tracker.entries and not tracker.persisted:
                from app.database.connection import SessionLocal
                async with SessionLocal() as db:
                    await persist(db, str(user_id), tracker)
        except Exception:
            logger.exception("Could not store usage for %s", label)
        if token is not None:
            current_portfolio_id.reset(token)
        ctx.__exit__(None, None, None)


async def month_summary(db: AsyncSession, user_id: str, budget: int) -> Dict[str, Any]:
    start = dt.datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    tokens_expr = UsageEvent.prompt_tokens + UsageEvent.completion_tokens
    where = (UsageEvent.user_id == str(user_id), UsageEvent.created_at >= start)
    rows = (await db.execute(
        select(UsageEvent.role, func.sum(tokens_expr), func.sum(UsageEvent.cost_usd)).where(*where).group_by(UsageEvent.role))).all()
    by_model = (await db.execute(
        select(UsageEvent.model, UsageEvent.priced, func.sum(tokens_expr), func.sum(UsageEvent.cost_usd))
        .where(*where).group_by(UsageEvent.model, UsageEvent.priced))).all()
    tokens = int(sum(r[1] or 0 for r in rows))
    unpriced = int(sum(r[2] or 0 for r in by_model if r[1] is False))
    return {"month_start": start.date().isoformat(), "tokens": tokens, "estimated_cost_usd": round(float(sum(r[2] or 0 for r in rows)), 4),
            "unpriced_tokens": unpriced, "prices_as_of": pricing.PRICES_AS_OF,
            "budget": budget, "over_budget": bool(budget and tokens > budget),
            "by_role": {r[0]: {"tokens": int(r[1] or 0), "cost_usd": round(float(r[2] or 0), 4)} for r in rows},
            "by_model": [{"model": m, "priced": bool(pr), "tokens": int(t or 0), "cost_usd": round(float(c or 0), 4)}
                         for m, pr, t, c in sorted(by_model, key=lambda r: -(r[3] or 0))]}


async def reprice(db: AsyncSession) -> int:
    """Recompute stored costs with the current price table (rows recorded before a model was priced, or under old prices)."""
    rows = list((await db.execute(select(UsageEvent))).scalars().all())
    changed = 0
    for r in rows:
        usd = pricing.cost(r.model or "", r.prompt_tokens or 0, r.completion_tokens or 0)
        new_cost, new_priced = (usd or 0.0), usd is not None
        if abs((r.cost_usd or 0.0) - new_cost) > 1e-9 or bool(r.priced) != new_priced:
            r.cost_usd, r.priced = new_cost, new_priced
            changed += 1
    await db.commit()
    return changed
