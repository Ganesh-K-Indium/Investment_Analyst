"""The analysis dashboards: reading a run's outcome, the run history, the attention rules, and the opt-in schedules."""
import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.models import (AgentType, AnalysisSchedule, AnalysisTask, Base, Document, Portfolio, TaskStatus,
                                 TickerSignal, UsageEvent)
from app.services import analysis_runs, portfolio_pulse, run_insights
from app.services.analysis_tasks import AnalysisTaskService

# Shaped like a real ALPHA report (rag/prompts/prompts.py, get_alpha_report_combiner_chain)
REPORT = """# Indium's ALPHA Framework Analysis: Alphabet (GOOGL)

## A — Alignment (Stakeholder & Insider Signals)
Alphabet saw insider selling. [[-|Insiders sold shares worth $276.6 million.]] [[-|Sergey Brin sold 673,200 shares.]]

## L — Liquidity (Macro/Micro Operating Environment)
[[-|The Federal Funds rate rose to 3.88%.]] [[+|Unemployment fell to 4.1%.]] Conditions are mixed.

## P — Performance (Earnings Quality & Fundamentals)
[[+|Revenue grew from $307.4B to $402.8B.]] [[+|Net income rose 79.1%.]] [[~|Operating margin was stable at 32%.]]

## H — Horizon (Competitive Moat & Structural Opportunity)
The moat is durable. [[~|R&D is 15% of revenue.]]

## A — Action (Technical Timing & Valuation Context)
[[-|The price ($282.10) is below its 200-day SMA ($336.51).]] [[~|RSI is 41.04.]]

---
## Indium's ALPHA Summary — Overall Investment Stance
Insider selling suggests caution. Performance is strong, though regulatory risk remains the main concern. [[-|Overall, the investment stance is Cautiously Bearish, given liquidity pressures.]]

---
*For informational purposes only.*
"""


def test_alpha_report_is_read_from_its_own_markers():
    parsed = run_insights.parse_alpha_report(REPORT)
    assert parsed["stance"] == "Cautiously Bearish" and parsed["stance_score"] == -1
    assert parsed["pillars"] == {"alignment": "negative", "liquidity": "mixed", "performance": "positive",
                                 "horizon": "mixed", "action": "negative"}
    assert parsed["fact_counts"] == {"pos": 3, "neg": 5, "neutral": 3}   # the summary's tagged stance sentence counts too
    assert "regulatory risk" in parsed["key_risk"]
    assert "Cautiously Bearish" in parsed["summary"] and "[[" not in parsed["summary"]


def test_unreadable_report_is_left_empty_not_guessed():
    parsed = run_insights.parse_alpha_report("# Report\n\nSomething went wrong.")
    assert parsed["stance"] is None and parsed["stance_score"] is None
    assert set(parsed["pillars"].values()) == {None}


def test_diff_finds_the_stance_change_and_new_concerns():
    later = REPORT.replace("Cautiously Bearish", "Bearish").replace(
        "[[+|Revenue grew from $307.4B to $402.8B.]]", "[[-|Revenue fell 4% to $386.0B.]]")
    d = run_insights.diff_reports(REPORT, later)
    assert d["stance"] == {"before": "Cautiously Bearish", "after": "Bearish", "change": -1}
    assert "Revenue fell 4% to $386.0B." in d["new_negatives"]
    assert d["pillars"]["performance"] == {"before": "positive", "after": "mixed"}


def test_titles_and_results_for_each_run_kind():
    assert run_insights.describe_request("alpha", {"tickers": ["aapl", "msft"]}) == ("ALPHA · AAPL, MSFT", ["AAPL", "MSFT"])
    assert run_insights.describe_request("compare", {"company1": "Apple", "company2": "Meta", "year": 2025})[0] == "Apple vs Meta (2025)"
    assert run_insights.describe_request("ask", {"query": "What is   GOOGL's margin?"})[0] == "What is GOOGL's margin?"
    ask = run_insights.describe_result("ask", "rag", {"answer": "## Answer\n\nAlphabet earned $132.2B in 2025. More text.",
                                                      "company_filter": ["googl"], "documents": [1, 2], "web_searched": True})
    assert ask["summary"] == "Alphabet earned $132.2B in 2025." and ask["tickers"] == ["GOOGL"]
    assert ask["outcome"] == {"sources": 2, "web": True, "chart": False}
    alpha = run_insights.describe_result("alpha", "rag", {"tickers": ["GOOGL"], "results": [{"ticker": "GOOGL", "report": REPORT}]})
    assert alpha["outcome"]["stances"] == {"GOOGL": "Cautiously Bearish"} and len(alpha["signals"]) == 1


def test_failures_are_classified_and_their_step_found():
    assert run_insights.classify_error("Request timed out after 300s") == "timeout"
    assert run_insights.classify_error("OpenAI 429 rate limit") == "provider"
    assert run_insights.classify_error("weird") == "other"
    progress = {"lanes": [{"label": "GOOGL", "steps": [{"label": "Read alignment", "status": "done"},
                                                       {"label": "Write liquidity", "status": "failed"}]}]}
    assert run_insights.failed_step_of(progress) == "GOOGL · Write liquidity"


@pytest.mark.parametrize("trigger,weekday,now,expected", [
    ("daily", None, datetime(2026, 11, 27, 5, 0), datetime(2026, 11, 27, 6, 0)),
    ("daily", None, datetime(2026, 11, 27, 6, 0), datetime(2026, 11, 28, 6, 0)),          # strictly after now
    ("weekly", 0, datetime(2026, 11, 27, 12, 0), datetime(2026, 11, 30, 6, 0)),           # Fri -> next Monday
    ("monthly", None, datetime(2026, 11, 27, 12, 0), datetime(2026, 12, 1, 6, 0)),
    ("new_filing", None, datetime(2026, 11, 27), None),
])
def test_next_run(trigger, weekday, now, expected):
    assert analysis_runs.next_run(trigger, 6, weekday, now) == expected


def test_attention_rules():
    now = datetime(2026, 11, 27)
    sig = lambda score, stance, days, tid: {"task_id": tid, "stance": stance, "stance_score": score,  # noqa: E731
                                            "created_at": (now - timedelta(days=days)).isoformat() + "Z"}
    row = {"ticker": "MSFT", "filings": 3, "latest": sig(-1, "Cautiously Bearish", 10, "b"), "previous": sig(1, "Cautiously Bullish", 40, "a"),
           "latest_filing": {"form_type": "10-Q", "filing_date": "2026-11-20", "ingested_at": (now - timedelta(days=2)).isoformat() + "Z"},
           "insider": {"net_usd": -9_000_000, "trades": 4}}
    kinds = [a["kind"] for a in portfolio_pulse.attention_for_ticker(row, now, 30, 5_000_000)]
    assert kinds == ["stance_downgrade", "filing_after_analysis", "insider_spike"]
    fresh = {"ticker": "NEW", "filings": 0, "latest": None, "previous": None, "latest_filing": None, "insider": {}}
    assert [a["kind"] for a in portfolio_pulse.attention_for_ticker(fresh, now, 30, 5e6)] == ["no_filings", "never_analysed"]
    stale = {**row, "previous": None, "latest_filing": None, "insider": {}, "latest": sig(0, "Neutral", 45, "c")}
    assert [a["kind"] for a in portfolio_pulse.attention_for_ticker(stale, now, 30, 5e6)] == ["stale_analysis"]
    failed = {"id": "f", "task_type": "alpha", "status": "FAILED", "tickers": ["MSFT"], "created_at": now - timedelta(days=1),
              "title": "ALPHA · MSFT", "progress_message": "timeout", "can_retry": True}
    assert portfolio_pulse.failed_run_alerts([failed], now)[0]["action"] == {"type": "retry", "task_id": "f"}
    redone = {**failed, "id": "g", "status": "COMPLETED", "created_at": now}
    assert portfolio_pulse.failed_run_alerts([failed, redone], now) == []


# ── against a database ──

def _db():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _setup(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


class FakePool:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, function, *args, **kwargs):
        self.jobs.append((function, args, kwargs))

    async def publish(self, channel, payload):
        pass


def test_a_finished_run_records_outcome_signals_cost_and_stays_slim():
    async def run():
        engine, maker = _db()
        await _setup(engine)
        async with maker() as db:
            db.add(Portfolio(id=1, user_id="7", name="Tech", company_names=["GOOGL"]))
            await db.commit()
            task = await AnalysisTaskService.create(db, user_id="7", agent_type=AgentType.RAG, task_type="alpha", portfolio_id=1,
                                                    queue="batch", session_ref="t1",
                                                    request={"tickers": ["GOOGL"], "user_id": "7", "thread_id": "t1"})
            assert task.title == "ALPHA · GOOGL" and task.tickers == ["GOOGL"]
            await AnalysisTaskService.update_status(db, task.id, TaskStatus.RUNNING)
            db.add(UsageEvent(user_id="7", role="generator", model="gpt-4o", prompt_tokens=1000, completion_tokens=500, cost_usd=0.0075, task_id=task.id))
            await db.commit()
            done = await AnalysisTaskService.update_status(
                db, task.id, TaskStatus.COMPLETED, progress_message="Done",
                result_metadata={"response": {"thread_id": "t1", "tickers": ["GOOGL"], "results": [{"ticker": "GOOGL", "report": REPORT}]}})
            assert done.outcome["stances"] == {"GOOGL": "Cautiously Bearish"}
            assert done.cost_usd == 0.0075 and done.total_tokens == 1500 and done.started_at and done.finished_at
            sigs = (await db.execute(TickerSignal.__table__.select())).all()
            assert [(s.ticker, s.stance) for s in sigs] == [("GOOGL", "Cautiously Bearish")]

            slim = AnalysisTaskService.to_summary(done)
            assert slim["result_metadata"] == {"queue": "batch", "session_ref": "t1", "response": {"thread_id": "t1"}}   # no report
            assert slim["can_retry"] and "request" not in AnalysisTaskService.to_dict(done)["result_metadata"]

            # a cancelled run stays cancelled whatever its job writes later
            t2 = await AnalysisTaskService.create(db, user_id="7", agent_type=AgentType.RAG, task_type="ask", portfolio_id=1)
            await AnalysisTaskService.update_status(db, t2.id, TaskStatus.CANCELLED)
            after = await AnalysisTaskService.update_status(db, t2.id, TaskStatus.COMPLETED)
            assert after.status == TaskStatus.CANCELLED and after.error_kind == "cancelled"

            # pulse: the matrix and the KPIs come from these rows
            pulse = await portfolio_pulse.portfolio_pulse(db, "7", await db.get(Portfolio, 1))
            row = pulse["tickers"][0]
            assert row["ticker"] == "GOOGL" and row["latest"]["stance"] == "Cautiously Bearish" and not row["stale"]
            assert pulse["kpis"]["covered"] == 1 and pulse["kpis"]["unreviewed"] == 1
            assert [a["kind"] for a in pulse["attention"]] == ["no_filings"]
        await engine.dispose()

    asyncio.run(run())


def test_history_filters_and_pages():
    async def run():
        engine, maker = _db()
        await _setup(engine)
        async with maker() as db:
            db.add_all([Portfolio(id=1, user_id="7", name="A", company_names=["AAPL"]), Portfolio(id=2, user_id="8", name="B", company_names=["X"])])
            base = datetime(2026, 11, 1)
            for i in range(25):
                db.add(AnalysisTask(id=f"t{i:02d}", user_id="7", portfolio_id=1, agent_type=AgentType.RAG, task_type="ask" if i % 2 else "alpha",
                                    status=TaskStatus.COMPLETED if i % 3 else TaskStatus.FAILED, title=f"Question {i}",
                                    tickers=["AAPL"] if i < 10 else ["MSFT"], created_at=base + timedelta(hours=i), pinned=(i == 4),
                                    outcome={"stances": {"AAPL": "Bullish"}} if i == 2 else None))
            db.add(AnalysisTask(id="other", user_id="8", portfolio_id=2, agent_type=AgentType.RAG, task_type="ask", status=TaskStatus.COMPLETED,
                                created_at=base, pinned=False))
            await db.commit()

            page1, cur = await AnalysisTaskService.search(db, "7", limit=10)
            page2, cur2 = await AnalysisTaskService.search(db, "7", limit=10, cursor=cur)
            page3, cur3 = await AnalysisTaskService.search(db, "7", limit=10, cursor=cur2)
            ids = [t.id for t in page1 + page2 + page3]
            assert ids == [f"t{i:02d}" for i in range(24, -1, -1)] and cur3 is None and "other" not in ids

            assert {t.id for t in (await AnalysisTaskService.search(db, "7", ticker="aapl", limit=50))[0]} == {f"t{i:02d}" for i in range(10)}
            assert [t.id for t in (await AnalysisTaskService.search(db, "7", stance="Bullish"))[0]] == ["t02"]
            assert [t.id for t in (await AnalysisTaskService.search(db, "7", stance="Cautiously Bullish"))[0]] == []
            assert [t.id for t in (await AnalysisTaskService.search(db, "7", pinned=True))[0]] == ["t04"]
            assert [t.id for t in (await AnalysisTaskService.search(db, "7", q="question 13"))[0]] == ["t13"]
            failed = (await AnalysisTaskService.search(db, "7", statuses=["FAILED"], limit=50))[0]
            assert all(t.status == TaskStatus.FAILED for t in failed) and len(failed) == 9
            assert len((await AnalysisTaskService.search(db, "7", unreviewed=True, limit=50))[0]) == 16
        await engine.dispose()

    asyncio.run(run())


def test_schedules_start_nothing_until_switched_on(monkeypatch):
    async def run():
        engine, maker = _db()
        await _setup(engine)
        monkeypatch.setattr(analysis_runs, "_over_budget", lambda db, uid: asyncio.sleep(0, result=False))
        pool = FakePool()
        now = datetime(2026, 11, 30, 7, 0)
        async with maker() as db:
            db.add(Portfolio(id=1, user_id="7", name="Tech", company_names=["GOOGL", "MSFT"]))
            off = AnalysisSchedule(user_id="7", portfolio_id=1, trigger="daily", hour_utc=6, enabled=False, next_run_at=now - timedelta(hours=1))
            db.add(off)
            await db.commit()
            assert await analysis_runs.run_due_schedules(pool, db, now) == [] and pool.jobs == []   # off by default: nothing runs

            off.enabled = True
            await db.commit()
            started = await analysis_runs.run_due_schedules(pool, db, now)
            assert len(started) == 1 and pool.jobs[0][0] == "run_rag_alpha"
            assert pool.jobs[0][1][1]["tickers"] == ["GOOGL", "MSFT"] and pool.jobs[0][2]["_queue_name"] == "batch"
            await db.refresh(off)
            assert off.next_run_at == datetime(2026, 12, 1, 6, 0) and off.last_task_id == started[0]
            assert await analysis_runs.run_due_schedules(pool, db, now) == []        # not due again until tomorrow

            # new-filing trigger: runs only the holdings with a filing ingested since it was switched on
            nf = AnalysisSchedule(user_id="7", portfolio_id=1, trigger="new_filing", hour_utc=6, enabled=True,
                                  created_at=now - timedelta(days=1), updated_at=now - timedelta(days=1))
            db.add(nf)
            db.add(Document(doc_id="d1", ticker="MSFT", source_file="x", collection="c", ingested_at=now - timedelta(hours=2),
                            filing_date=date(2026, 11, 29)))
            await db.commit()
            started = await analysis_runs.run_due_schedules(pool, db, now)
            assert len(started) == 1 and pool.jobs[-1][1][1]["tickers"] == ["MSFT"]
            assert await analysis_runs.run_due_schedules(pool, db, now + timedelta(hours=1)) == []   # at most once a day

            # the deployment switch turns every schedule off
            from app.core import config
            monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(analysis_schedules_allowed=False))
            assert await analysis_runs.run_due_schedules(pool, db, now + timedelta(days=3)) == []
        await engine.dispose()

    asyncio.run(run())


def test_retry_reuses_the_request_and_links_the_runs():
    async def run():
        engine, maker = _db()
        await _setup(engine)
        pool = FakePool()
        async with maker() as db:
            first = await AnalysisTaskService.create(db, user_id="7", agent_type=AgentType.QUANT, task_type="query",
                                                     request={"query": "RSI of AAPL", "user_id": "7"})
            again = await analysis_runs.retry(pool, db, first)
            assert again.parent_task_id == first.id and pool.jobs[0][0] == "run_quant_query"
            assert pool.jobs[0][1][1] == {"query": "RSI of AAPL", "user_id": "7"}
            legacy = await AnalysisTaskService.create(db, user_id="7", agent_type=AgentType.RAG, task_type="ask")
            with pytest.raises(ValueError):
                await analysis_runs.retry(pool, db, legacy)
        await engine.dispose()

    asyncio.run(run())


def test_history_hides_runs_left_behind_by_deleted_portfolios_and_chats():
    from app.database.models import ChatSession

    async def run():
        engine, maker = _db()
        await _setup(engine)
        async with maker() as db:
            db.add(Portfolio(id=1, user_id="7", name="A", company_names=["AAPL"]))
            db.add(ChatSession(session_id="thread_live", user_id="7", agent_type=AgentType.RAG))
            for tid, pid, ref in (("in_portfolio", 1, "x"), ("adhoc_live", None, "thread_live"), ("orphan", None, "thread_gone"), ("no_ref", None, None)):
                meta = {"queue": "interactive", **({"session_ref": ref} if ref else {})}
                db.add(AnalysisTask(id=tid, user_id="7", portfolio_id=pid, agent_type=AgentType.RAG, task_type="compare",
                                    status=TaskStatus.COMPLETED, result_metadata=meta, pinned=False))
            await db.commit()
            assert {t.id for t in (await AnalysisTaskService.search(db, "7"))[0]} == {"in_portfolio", "adhoc_live"}
            assert [t.id for t in (await AnalysisTaskService.search(db, "7", adhoc=True))[0]] == ["adhoc_live"]
        await engine.dispose()

    asyncio.run(run())


def test_spend_counts_everything_done_in_the_portfolio_this_month():
    from app.services import usage_ledger
    from app.utils.usage_tracker import UsageTracker

    async def run():
        engine, maker = _db()
        await _setup(engine)
        async with maker() as db:
            db.add_all([Portfolio(id=1, user_id="7", name="A", company_names=["AAPL"]), Portfolio(id=2, user_id="7", name="B", company_names=["MSFT"])])
            await db.commit()
            # a chat in portfolio 1 (no background run), stored through the ledger with the request's portfolio
            tracker = UsageTracker("ask:thread_1")
            tracker.record(stage="generator", model="gpt-4o", prompt_tokens=10_000, completion_tokens=1_000, total_tokens=11_000, duration_seconds=1.0)
            token = usage_ledger.current_portfolio_id.set(1)
            try:
                await usage_ledger.persist(db, "7", tracker)
            finally:
                usage_ledger.current_portfolio_id.reset(token)
            db.add(UsageEvent(user_id="7", role="generator", model="gpt-4o", prompt_tokens=1, completion_tokens=1, cost_usd=1.0, portfolio_id=2))
            db.add(UsageEvent(user_id="7", role="generator", model="gpt-4o", prompt_tokens=1, completion_tokens=1, cost_usd=5.0, portfolio_id=1,
                              created_at=datetime.utcnow() - timedelta(days=40)))           # last month: not counted
            await db.commit()
            pulse = await portfolio_pulse.portfolio_pulse(db, "7", await db.get(Portfolio, 1))
            assert pulse["kpis"]["spend_month_usd"] == 0.035 and pulse["kpis"]["tokens_month"] == 11_000
            ov = await portfolio_pulse.overview(db, "7")
            assert ov["totals"]["spend_month_usd"] == 1.035
        await engine.dispose()

    asyncio.run(run())


def test_alpha_runs_made_before_usage_was_stored_get_it_from_their_response():
    async def run():
        engine, maker = _db()
        await _setup(engine)
        async with maker() as db:
            db.add(Portfolio(id=1, user_id="7", name="A", company_names=["GOOGL"]))
            usage = {"total_tokens": 48056, "total_prompt_tokens": 45623, "total_completion_tokens": 2433}
            db.add(AnalysisTask(id="old", user_id="7", portfolio_id=1, agent_type=AgentType.RAG, task_type="alpha", status=TaskStatus.COMPLETED,
                                pinned=False, result_metadata={"response": {"tickers": ["GOOGL"], "results": [{"ticker": "GOOGL", "report": REPORT, "usage": usage}]}}))
            await db.commit()
            await AnalysisTaskService.backfill(db)
            await AnalysisTaskService.backfill(db)                                  # idempotent: usage stored once
            task = await db.get(AnalysisTask, "old")
            assert task.total_tokens == 48056 and task.cost_usd == round((45623 * 2.5 + 2433 * 10) / 1e6, 4)
            assert task.title == "ALPHA · GOOGL"
        await engine.dispose()

    asyncio.run(run())


def test_deleting_a_run_removes_its_chat_only_when_the_chat_holds_no_answer():
    from app.api.analysis_tasks import delete_task
    from app.database.models import ChatMessage, ChatSession, MessageRole

    async def run():
        engine, maker = _db()
        await _setup(engine)
        async with maker() as db:
            db.add(Portfolio(id=1, user_id="7", name="A", company_names=["AAPL"]))
            empty = ChatSession(session_id="thread_cancelled", user_id="7", agent_type=AgentType.RAG, portfolio_id=1)
            kept = ChatSession(session_id="thread_answered", user_id="7", agent_type=AgentType.RAG, portfolio_id=1)
            db.add_all([empty, kept])
            await db.flush()
            db.add(ChatMessage(chat_session_id=empty.id, role=MessageRole.USER, content="Provide ALPHA for AAPL"))
            db.add_all([ChatMessage(chat_session_id=kept.id, role=MessageRole.USER, content="q"),
                        ChatMessage(chat_session_id=kept.id, role=MessageRole.ASSISTANT, content="a")])
            for tid, ref, status in (("cancelled", "thread_cancelled", TaskStatus.CANCELLED), ("answered", "thread_answered", TaskStatus.COMPLETED),
                                     ("running", "thread_answered", TaskStatus.RUNNING)):
                db.add(AnalysisTask(id=tid, user_id="7", portfolio_id=1, agent_type=AgentType.RAG, task_type="alpha", status=status,
                                    result_metadata={"session_ref": ref}, pinned=False))
            await db.commit()
            user = SimpleNamespace(id=7)

            r = await delete_task("cancelled", db, user)
            assert r.headers["X-Chat-Deleted"] == "1"
            assert (await db.execute(ChatSession.__table__.select().where(ChatSession.session_id == "thread_cancelled"))).first() is None

            r = await delete_task("answered", db, user)
            assert r.headers["X-Chat-Deleted"] == "0"
            assert (await db.execute(ChatSession.__table__.select().where(ChatSession.session_id == "thread_answered"))).first() is not None

            from fastapi import HTTPException
            with pytest.raises(HTTPException) as e:
                await delete_task("running", db, user)                     # a running run must be cancelled first
            assert e.value.status_code == 409
        await engine.dispose()

    asyncio.run(run())
