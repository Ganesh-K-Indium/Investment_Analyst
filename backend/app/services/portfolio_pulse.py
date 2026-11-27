"""What the dashboards show about portfolios: the latest view on each holding, what changed, and what needs attention.

Everything is read in a handful of queries for all of a user's portfolios at once (`PulseData.load`), then shaped per
portfolio in plain Python, so the cross-portfolio page costs the same few queries as a single portfolio. The attention rules
are pure functions of that data (unit-tested in tests/test_analysis_dashboard.py)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AnalysisTask, Document, Form4Transaction, Portfolio, TaskStatus, TickerSignal, UsageEvent
from app.services.analysis_runs import portfolio_tickers
from app.utils.time import to_iso_z

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
ACTIVE = ("PENDING", "RUNNING")


def _status(t) -> str:
    s = t.status
    return s.value if hasattr(s, "value") else s


def signal_dict(s: TickerSignal) -> dict:
    return {"task_id": s.task_id, "ticker": s.ticker, "stance": s.stance, "stance_score": s.stance_score, "pillars": s.pillars or {},
            "fact_counts": s.fact_counts or {}, "key_risk": s.key_risk, "portfolio_id": s.portfolio_id,
            "created_at": to_iso_z(s.created_at)}


def _iso_date(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


# ── Attention rules (pure) ──────────────────────────────────────────────────────────────────────────────────────

def attention_for_ticker(row: Dict[str, Any], now: datetime, stale_days: int, insider_usd: float) -> List[dict]:
    """Alerts about one holding. `row` is a pulse ticker row (see PulseData.ticker_row)."""
    t = row["ticker"]
    out: List[dict] = []
    latest, previous = row.get("latest"), row.get("previous")
    if row.get("filings", 0) == 0:
        out.append({"kind": "no_filings", "ticker": t, "severity": "medium",
                    "message": f"No SEC filings ingested for {t}, so research on it relies on the web.",
                    "action": {"type": "ingest", "ticker": t}})
    if latest is None:
        out.append({"kind": "never_analysed", "ticker": t, "severity": "medium",
                    "message": f"{t} has no ALPHA analysis yet.", "action": {"type": "run_alpha", "ticker": t}})
        return out
    at = datetime.fromisoformat(latest["created_at"].rstrip("Z")) if latest.get("created_at") else None
    if previous and latest.get("stance_score") is not None and previous.get("stance_score") is not None \
            and latest["stance_score"] < previous["stance_score"]:
        out.append({"kind": "stance_downgrade", "ticker": t, "severity": "high",
                    "message": f"{t} moved from {previous['stance']} to {latest['stance']}.",
                    "action": {"type": "compare", "ticker": t, "a": previous["task_id"], "b": latest["task_id"]}})
    filing = row.get("latest_filing")
    if at and filing and filing.get("ingested_at") and datetime.fromisoformat(filing["ingested_at"].rstrip("Z")) > at:
        out.append({"kind": "filing_after_analysis", "ticker": t, "severity": "high",
                    "message": f"New {filing.get('form_type') or 'filing'} for {t} ({filing.get('filing_date') or 'recent'}) "
                               f"arrived after the last ALPHA.",
                    "action": {"type": "run_alpha", "ticker": t}})
    elif at and (now - at).days >= stale_days:
        out.append({"kind": "stale_analysis", "ticker": t, "severity": "low",
                    "message": f"{t}'s last ALPHA is {(now - at).days} days old.", "action": {"type": "run_alpha", "ticker": t}})
    insider = row.get("insider") or {}
    if insider_usd and abs(insider.get("net_usd") or 0) >= insider_usd:
        verb = "bought" if insider["net_usd"] > 0 else "sold"
        out.append({"kind": "insider_spike", "ticker": t, "severity": "medium",
                    "message": f"Insiders net {verb} ${abs(insider['net_usd']) / 1e6:,.1f}M of {t} since the last analysis "
                               f"({insider.get('trades', 0)} trades).",
                    "action": {"type": "run_alpha", "ticker": t}})
    return out


def failed_run_alerts(tasks: List[dict], now: datetime) -> List[dict]:
    """Failed runs of the last 7 days that no later run of the same kind (and tickers) has made up for."""
    out = []
    done = [t for t in tasks if t["status"] == "COMPLETED"]
    for t in tasks:
        if t["status"] != "FAILED" or not t["created_at"] or now - t["created_at"] > timedelta(days=7):
            continue
        if any(d["task_type"] == t["task_type"] and d["created_at"] and d["created_at"] > t["created_at"]
               and (d["tickers"] or []) == (t["tickers"] or []) for d in done):
            continue
        out.append({"kind": "failed_run", "ticker": (t["tickers"] or [None])[0], "severity": "medium",
                    "message": f"“{t['title'] or t['task_type']}” failed" + (f": {t['progress_message']}" if t.get("progress_message") else "."),
                    "action": {"type": "retry", "task_id": t["id"]} if t.get("can_retry") else {"type": "open_run", "task_id": t["id"]}})
    return out


def sort_attention(items: List[dict]) -> List[dict]:
    return sorted(items, key=lambda a: (SEVERITY_ORDER.get(a["severity"], 9), a.get("ticker") or ""))


# ── Data ────────────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class PulseData:
    now: datetime
    stale_days: int
    insider_usd: float
    signals: Dict[int, Dict[str, List[dict]]] = field(default_factory=dict)      # portfolio -> ticker -> newest first
    tasks: Dict[int, List[dict]] = field(default_factory=dict)                     # portfolio -> task rows
    filings: Dict[str, dict] = field(default_factory=dict)                         # ticker -> {count, latest}
    insider_rows: Dict[str, List[tuple]] = field(default_factory=dict)             # ticker -> [(date, signed usd)]
    spend: Dict[Optional[int], Dict[str, float]] = field(default_factory=dict)     # portfolio (None = none) -> month cost, tokens

    @classmethod
    async def load(cls, db: AsyncSession, user_id: str, portfolios: List[Portfolio]) -> "PulseData":
        from app.core.config import get_settings
        cfg = get_settings()
        now = datetime.utcnow()
        data = cls(now=now, stale_days=cfg.analysis_stale_days, insider_usd=cfg.analysis_insider_alert_usd)
        ids = [p.id for p in portfolios]
        all_tickers = sorted({t for p in portfolios for t in portfolio_tickers(p)})
        if not ids:
            return data

        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        for pid, usd, tokens in (await db.execute(
                select(UsageEvent.portfolio_id, func.sum(UsageEvent.cost_usd), func.sum(UsageEvent.prompt_tokens + UsageEvent.completion_tokens))
                .where(UsageEvent.user_id == str(user_id), UsageEvent.created_at >= month_start)
                .group_by(UsageEvent.portfolio_id))).all():
            data.spend[pid] = {"usd": round(float(usd or 0), 4), "tokens": int(tokens or 0)}

        sigs = (await db.execute(select(TickerSignal).where(TickerSignal.user_id == str(user_id), TickerSignal.portfolio_id.in_(ids))
                                 .order_by(TickerSignal.created_at.desc()).limit(5000))).scalars().all()
        for s in sigs:
            data.signals.setdefault(s.portfolio_id, {}).setdefault(s.ticker, []).append(signal_dict(s))

        since = now - timedelta(days=400)
        rows = (await db.execute(select(
            AnalysisTask.id, AnalysisTask.portfolio_id, AnalysisTask.task_type, AnalysisTask.status, AnalysisTask.tickers,
            AnalysisTask.created_at, AnalysisTask.finished_at, AnalysisTask.updated_at, AnalysisTask.reviewed_at,
            AnalysisTask.cost_usd, AnalysisTask.title, AnalysisTask.progress_message, AnalysisTask.result_metadata,
        ).where(AnalysisTask.user_id == str(user_id), AnalysisTask.portfolio_id.in_(ids), AnalysisTask.created_at >= since))).all()
        for r in rows:
            status = r.status.value if hasattr(r.status, "value") else r.status
            data.tasks.setdefault(r.portfolio_id, []).append({
                "id": r.id, "task_type": r.task_type, "status": status, "tickers": r.tickers or [], "created_at": r.created_at,
                "finished_at": r.finished_at or (r.updated_at if status == "COMPLETED" else None), "reviewed_at": r.reviewed_at,
                "cost_usd": r.cost_usd or 0.0, "title": r.title, "progress_message": r.progress_message,
                "can_retry": bool((r.result_metadata or {}).get("request")),
            })

        if all_tickers:
            counts = (await db.execute(select(Document.ticker, func.count()).where(Document.ticker.in_(all_tickers))
                                       .group_by(Document.ticker))).all()
            for ticker, n in counts:
                data.filings[ticker] = {"count": int(n), "latest": None}
            latest = (await db.execute(select(Document.ticker, Document.form_type, Document.filing_date, Document.ingested_at)
                                       .where(Document.ticker.in_(all_tickers))
                                       .order_by(Document.ingested_at.desc().nullslast()))).all()
            for ticker, form, filed, ingested in latest:
                entry = data.filings.setdefault(ticker, {"count": 0, "latest": None})
                if entry["latest"] is None:
                    entry["latest"] = {"form_type": form, "filing_date": _iso_date(filed), "ingested_at": to_iso_z(ingested)}

            start = (now - timedelta(days=180)).date()
            trades = (await db.execute(select(Form4Transaction.issuer_symbol, Form4Transaction.transaction_date,
                                              Form4Transaction.transaction_value, Form4Transaction.transaction_acquired_disposed_code)
                                       .where(Form4Transaction.issuer_symbol.in_(all_tickers),
                                              Form4Transaction.transaction_code.in_(("P", "S")),
                                              Form4Transaction.transaction_date >= start))).all()
            for sym, d, value, ad in trades:
                if d is None or not value:
                    continue
                data.insider_rows.setdefault(sym, []).append((d, -float(value) if ad == "D" else float(value)))
        return data

    # ── shaping ──

    def last_analysed(self, portfolio_id: int, ticker: str) -> Optional[datetime]:
        best = None
        for t in self.tasks.get(portfolio_id, []):
            if t["status"] == "COMPLETED" and ticker in (t["tickers"] or []) and t["finished_at"]:
                best = t["finished_at"] if best is None or t["finished_at"] > best else best
        return best

    def ticker_row(self, portfolio_id: int, ticker: str) -> dict:
        history = self.signals.get(portfolio_id, {}).get(ticker, [])
        latest = history[0] if history else None
        previous = history[1] if len(history) > 1 else None
        last = self.last_analysed(portfolio_id, ticker)
        sig_at = datetime.fromisoformat(latest["created_at"].rstrip("Z")) if latest and latest.get("created_at") else None
        since = (sig_at or self.now - timedelta(days=90)).date()
        flows = [v for d, v in self.insider_rows.get(ticker, []) if d >= since]
        filing = self.filings.get(ticker) or {"count": 0, "latest": None}
        return {
            "ticker": ticker, "latest": latest, "previous": previous, "runs": len(history),
            "last_analysed_at": to_iso_z(last),
            "alpha_age_days": (self.now - sig_at).days if sig_at else None,
            "stale": bool(sig_at is None or (self.now - sig_at).days >= self.stale_days),
            "filings": filing["count"], "latest_filing": filing["latest"],
            "insider": {"net_usd": round(sum(flows), 2), "trades": len(flows), "since": since.isoformat()},
        }

    def pulse(self, portfolio: Portfolio) -> dict:
        tickers = portfolio_tickers(portfolio)
        rows = [self.ticker_row(portfolio.id, t) for t in tickers]
        tasks = self.tasks.get(portfolio.id, [])
        attention = [a for r in rows for a in attention_for_ticker(r, self.now, self.stale_days, self.insider_usd)]
        attention += failed_run_alerts(tasks, self.now)
        spend = self.spend.get(portfolio.id, {"usd": 0.0, "tokens": 0})
        stance_mix: Dict[str, int] = {}
        for r in rows:
            if r["latest"] and r["latest"].get("stance"):
                stance_mix[r["latest"]["stance"]] = stance_mix.get(r["latest"]["stance"], 0) + 1
        alpha_done = [t["finished_at"] for t in tasks if t["task_type"] == "alpha" and t["status"] == "COMPLETED" and t["finished_at"]]
        covered = sum(1 for r in rows if r["latest"] and not r["stale"])
        return {
            "portfolio": {"id": portfolio.id, "name": portfolio.name, "tickers": tickers, "description": portfolio.description},
            "stale_days": self.stale_days,
            "tickers": rows,
            "attention": sort_attention(attention),
            "kpis": {
                "holdings": len(tickers),
                "covered": covered,
                "coverage_pct": round(100 * covered / len(tickers)) if tickers else 0,
                "last_alpha_at": to_iso_z(max(alpha_done)) if alpha_done else None,
                "stance_mix": stance_mix,
                "spend_month_usd": spend["usd"],
                "tokens_month": spend["tokens"],
                "running": sum(1 for t in tasks if t["status"] in ACTIVE),
                "unreviewed": sum(1 for t in tasks if t["status"] == "COMPLETED" and t["reviewed_at"] is None),
                "failed_7d": sum(1 for t in tasks if t["status"] == "FAILED" and t["created_at"] and self.now - t["created_at"] <= timedelta(days=7)),
                "runs_total": len(tasks),
            },
        }


async def portfolio_pulse(db: AsyncSession, user_id: str, portfolio: Portfolio) -> dict:
    data = await PulseData.load(db, user_id, [portfolio])
    return data.pulse(portfolio)


async def overview(db: AsyncSession, user_id: str) -> dict:
    """The cross-portfolio command centre: a card per portfolio, the book-wide view per ticker, movers and alerts."""
    portfolios = list((await db.execute(select(Portfolio).where(Portfolio.user_id == str(user_id))
                                        .order_by(Portfolio.updated_at.desc()))).scalars().all())
    data = await PulseData.load(db, user_id, portfolios)
    cards, alerts = [], []
    for p in portfolios:
        pulse = data.pulse(p)
        k = pulse["kpis"]
        tasks = data.tasks.get(p.id, [])
        latest_activity = max((t["finished_at"] or t["created_at"] for t in tasks if t["created_at"]), default=None)
        cards.append({**pulse["portfolio"], **k, "last_activity_at": to_iso_z(latest_activity),
                      "attention": len(pulse["attention"]), "high_alerts": sum(1 for a in pulse["attention"] if a["severity"] == "high")})
        alerts += [{**a, "portfolio_id": p.id, "portfolio_name": p.name} for a in pulse["attention"] if a["severity"] in ("high", "medium")]

    # book-wide: the newest signal per ticker across portfolios, and how it moved in the last 7 days
    by_ticker: Dict[str, List[dict]] = {}
    for per_ticker in data.signals.values():
        for ticker, hist in per_ticker.items():
            by_ticker.setdefault(ticker, []).extend(hist)
    distribution: Dict[str, int] = {}
    movers = []
    week_ago = data.now - timedelta(days=7)
    for ticker, hist in by_ticker.items():
        hist.sort(key=lambda s: s["created_at"] or "", reverse=True)
        latest = hist[0]
        if latest.get("stance"):
            distribution[latest["stance"]] = distribution.get(latest["stance"], 0) + 1
        if len(hist) > 1 and latest.get("created_at") and datetime.fromisoformat(latest["created_at"].rstrip("Z")) >= week_ago:
            prev = hist[1]
            if latest.get("stance_score") is not None and prev.get("stance_score") is not None and latest["stance_score"] != prev["stance_score"]:
                movers.append({"ticker": ticker, "from": prev["stance"], "to": latest["stance"],
                               "change": latest["stance_score"] - prev["stance_score"], "a": prev["task_id"], "b": latest["task_id"],
                               "at": latest["created_at"]})
    movers.sort(key=lambda m: (m["change"], m["ticker"]))
    totals = {k: sum(c[k] for c in cards) for k in ("running", "unreviewed", "failed_7d", "holdings", "covered")}
    totals["spend_month_usd"] = round(sum(v["usd"] for v in data.spend.values()), 4)       # everything, in or out of portfolios
    totals["tokens_month"] = sum(v["tokens"] for v in data.spend.values())
    return {
        "portfolios": cards,
        "conviction": distribution,
        "movers": movers[:10],
        "alerts": sort_attention(alerts)[:30],
        "inbox": totals["unreviewed"],
        "totals": totals,
    }


async def timeline(db: AsyncSession, user_id: str, ticker: str, portfolio_id: Optional[int] = None) -> dict:
    """A ticker's stance over every ALPHA run, with filing dates and monthly insider flow to read it against."""
    ticker = ticker.upper()
    stmt = select(TickerSignal).where(TickerSignal.user_id == str(user_id), TickerSignal.ticker == ticker)
    if portfolio_id is not None:
        stmt = stmt.where(TickerSignal.portfolio_id == portfolio_id)
    signals = [signal_dict(s) for s in (await db.execute(stmt.order_by(TickerSignal.created_at))).scalars().all()]
    start = datetime.utcnow() - timedelta(days=365 * 2)
    if signals and signals[0]["created_at"]:
        start = min(start, datetime.fromisoformat(signals[0]["created_at"].rstrip("Z")) - timedelta(days=90))
    filings = (await db.execute(select(Document.form_type, Document.filing_date, Document.fiscal_year, Document.fiscal_quarter)
                                .where(Document.ticker == ticker, Document.filing_date >= start.date())
                                .order_by(Document.filing_date))).all()
    trades = (await db.execute(select(Form4Transaction.transaction_date, Form4Transaction.transaction_value,
                                      Form4Transaction.transaction_acquired_disposed_code)
                               .where(Form4Transaction.issuer_symbol == ticker, Form4Transaction.transaction_code.in_(("P", "S")),
                                      Form4Transaction.transaction_date >= start.date()))).all()
    monthly: Dict[str, float] = {}
    for d, value, ad in trades:
        if d and value:
            key = d.strftime("%Y-%m")
            monthly[key] = monthly.get(key, 0.0) + (-float(value) if ad == "D" else float(value))
    return {
        "ticker": ticker,
        "signals": signals,
        "filings": [{"form_type": f, "filing_date": _iso_date(d), "fiscal_year": fy, "fiscal_quarter": fq} for f, d, fy, fq in filings],
        "insider_monthly": [{"month": k, "net_usd": round(v, 2)} for k, v in sorted(monthly.items())],
    }
