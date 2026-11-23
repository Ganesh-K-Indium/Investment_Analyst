"""Persist per-request token use and report it against the user's monthly budget. Costs are ESTIMATES from list prices."""
import datetime as dt
from typing import Any, Dict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import UsageEvent
from app.utils.usage_tracker import UsageTracker

# USD per 1M tokens (input, output). Estimates only; update when prices change.
PRICES = {"gpt-4o": (2.5, 10.0), "gpt-4o-mini": (0.15, 0.6), "gpt-4.1": (2.0, 8.0), "gpt-4.1-mini": (0.4, 1.6), "gpt-4-turbo": (10.0, 30.0)}


def cost(model: str, prompt: int, completion: int) -> float:
    """Estimated USD from list prices; 0 for a model with no known price (other providers, self-hosted): those are counted in tokens only."""
    price = PRICES.get(model.split(":", 1)[-1] if model.startswith("openai:") else model)
    if price is None:
        return 0.0
    return round((prompt * price[0] + completion * price[1]) / 1_000_000, 6)


async def persist(db: AsyncSession, user_id: str, tracker: UsageTracker) -> Dict[str, Any]:
    """Store what a request spent (one row per role+model) and return the per-request summary for the UI."""
    agg: Dict[tuple, list] = {}
    for e in tracker.entries:
        if e.stage == "graph_total":  # the aggregate row; the per-role rows below are the detail
            continue
        a = agg.setdefault((e.stage, e.model), [0, 0])
        a[0] += e.prompt_tokens
        a[1] += e.completion_tokens
    total_cost = 0.0
    for (role, model), (p, c) in agg.items():
        usd = cost(model, p, c)
        total_cost += usd
        db.add(UsageEvent(user_id=str(user_id), run_label=tracker.run_label[:200], role=role, model=model, prompt_tokens=p, completion_tokens=c, cost_usd=usd))
    if agg:
        await db.commit()
    p, c = sum(v[0] for v in agg.values()), sum(v[1] for v in agg.values())
    s = tracker.summary()
    if not agg:                                   # nothing reported per role: fall back to the tracker's own totals
        p, c = s["total_prompt_tokens"], s["total_completion_tokens"]
    return {"total_tokens": p + c, "prompt_tokens": p, "completion_tokens": c,
            "estimated_cost_usd": round(total_cost, 4)}


async def month_summary(db: AsyncSession, user_id: str, budget: int) -> Dict[str, Any]:
    start = dt.datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = (await db.execute(
        select(UsageEvent.role, func.sum(UsageEvent.prompt_tokens + UsageEvent.completion_tokens), func.sum(UsageEvent.cost_usd))
        .where(UsageEvent.user_id == str(user_id), UsageEvent.created_at >= start).group_by(UsageEvent.role))).all()
    tokens = int(sum(r[1] or 0 for r in rows))
    return {"month_start": start.date().isoformat(), "tokens": tokens, "estimated_cost_usd": round(float(sum(r[2] or 0 for r in rows)), 4),
            "budget": budget, "over_budget": bool(budget and tokens > budget), "by_role": {r[0]: {"tokens": int(r[1] or 0), "cost_usd": round(float(r[2] or 0), 4)} for r in rows}}
