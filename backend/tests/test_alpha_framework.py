"""The ALPHA framework: stance from pillar scores, fact checks, market-data sentences, report assembly, and the report node run
end to end with fake models (no model calls, no network)."""
import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from app.services import run_insights
from rag.alpha import framework as fw

# The Performance section of a real GOOGL report: its margin sentence contradicts its own figures.
GOOGL_PERF = ("[[+|Alphabet's revenue grew from $307.4B (2023) to $402.8B (2025), a CAGR of 14.5%.]] "
              "[[+|Net income increased from $73.8B (2023) to $132.2B (2025), +79.1%.]] "
              "[[+|Operating income rose from $84.3B (2023) to $129.0B (2025), +53.0%.]] Free cash flow improved. "
              "[[~|Operating margins remained stable at 32% from 2023 to 2025.]]")
GOOGL_SRC = ["Consolidated statements of income (in millions): Revenues 307,394 (2023) 402,836 (2025); "
             "Income from operations 84,293 128,980; Net income 73,795 132,170"]


# ── stance ──

@pytest.mark.parametrize("scores,stance", [
    ({"alignment": 2, "liquidity": 1, "performance": 2, "horizon": 1, "action": 1}, "Bullish"),           # mean 1.4
    ({"alignment": 1, "liquidity": 0, "performance": 1, "horizon": 1, "action": 0}, "Cautiously Bullish"),  # 0.6
    ({"alignment": -1, "liquidity": 0, "performance": 2, "horizon": 1, "action": -1}, "Neutral"),          # 0.2
    ({"alignment": -1, "liquidity": -1, "performance": 0, "horizon": -1, "action": 0}, "Cautiously Bearish"),  # -0.6
    ({"alignment": -2, "liquidity": -1, "performance": -2, "horizon": -1, "action": -2}, "Bearish"),       # -1.6
])
def test_stance_comes_from_the_pillar_scores_in_fixed_bands(scores, stance):
    assert fw.stance_from_scores(scores)["stance"] == stance


def test_missing_pillars_make_a_partial_stance_or_none():
    s = fw.stance_from_scores({"alignment": 1, "liquidity": 1, "performance": 1, "horizon": None, "action": None})
    assert s["stance"] == "Cautiously Bullish" and s["partial"] and s["missing"] == ["horizon", "action"]
    s = fw.stance_from_scores({"alignment": 1, "liquidity": None, "performance": None, "horizon": None, "action": 2})
    assert s["stance"] is None and s["partial"]                      # fewer than 3 pillars: no stance at all


def test_a_pillar_without_a_score_is_scored_from_its_own_verdict():
    assert fw.score_from_text("Negative — insider selling warrants caution") == -1
    assert fw.score_from_text("Positive — net insider buying signals conviction") == 1
    assert fw.score_from_text("Neutral: hold") == 0
    assert fw.clamp_score(5) == 2 and fw.clamp_score("-3") == -2 and fw.clamp_score("x") is None


# ── fact checks ──

def test_fact_check_catches_a_margin_called_stable_against_the_sections_own_figures():
    issues = fw.check_section(GOOGL_PERF, GOOGL_SRC)
    trend = [i for i in issues if i["kind"] == "trend"]
    assert trend and "27.4% (2023)" in trend[0]["detail"] and "32.0% (2025)" in trend[0]["detail"]


def test_fact_check_accepts_calculations_from_quoted_figures():
    # CAGR 14.5%, +79.1% and +53.0% are calculated from figures that are in the sources: not findings
    flagged = {i["text"] for i in fw.check_section(GOOGL_PERF, GOOGL_SRC) if i["kind"] != "trend"}
    assert not flagged & {"14.5%", "79.1%", "53.0%"}


def test_fact_check_flags_a_figure_that_is_in_no_source():
    issues = fw.check_section("[[~|GOOGL's latest P/E ratio is 14.73.]]", ["Trailing P/E: 25.90"])
    assert [i["text"] for i in issues] == ["14.73"]
    assert "14.73" in fw.issues_note(issues)


# ── market data ──

def test_action_is_written_from_market_data_without_a_model():
    out = fw.action_from_market_data("googl", {"price": 282.10, "sma200": 336.51, "rsi": 41.04, "pe": 25.9, "ebitda": 185.52e9,
                                               "as_of": "2026-11-26"})
    text = fw.strip_tags(out["analysis"])
    assert "GOOGL's current stock price ($282.10) is below its 200-day moving average ($336.51)" in text
    assert "41.04" in text and "25.90" in text and "$185.52B" in text and "2026-11-26" in text
    assert out["score"] == -1 and out["source"] == "market_data"
    assert fw.action_from_market_data("X", {"price": 1.0}) is None   # no SMA/RSI: the model path is used instead


# ── report ──

def _pillars(**over):
    base = {k: {"status": "ok", "analysis": f"[[~|{k} fact $1.0B.]]", "recommendation": f"{k} verdict", "score": 0, "issues": []}
            for k, _, _ in fw.PILLARS}
    base.update(over)
    return base


def test_report_keeps_the_headings_the_dashboards_read_and_marks_partial_runs():
    pillars = _pillars(horizon={"status": "no_data", "reason": "no source data was found for this pillar"})
    stance = fw.stance_from_scores({k: (p.get("score") if p["status"] == "ok" else None) for k, p in pillars.items()})
    report = fw.assemble_report("Alphabet Inc.", "googl", pillars, stance, "[[~|Overall, the investment stance is Neutral.]]")
    assert report.startswith("# Indium's ALPHA Framework Analysis: Alphabet Inc. (GOOGL)")
    assert "**Partial analysis:** Horizon could not be completed" in report and "_Not available: no source data" in report
    parsed = run_insights.parse_alpha_report(report)                 # the old reader still finds every section and the stance
    assert parsed["stance"] == "Neutral" and parsed["pillars"]["horizon"] is None and parsed["pillars"]["alignment"] == "mixed"


def test_signal_record_carries_each_pillars_own_verdict():
    pillars = _pillars(performance={"status": "ok", "analysis": "[[+|x $1.0B.]]", "recommendation": "Strong growth", "score": 2,
                                    "issues": [{"text": "1", "kind": "unsupported", "detail": ""}], "revised": True})
    stance = fw.stance_from_scores({k: p["score"] for k, p in pillars.items()})
    sig = fw.signal_record(stance, pillars, "Margins could fall.")
    assert sig["pillars"]["performance"] == "positive" and sig["pillar_detail"]["performance"]["score"] == 2
    assert sig["pillar_detail"]["performance"]["revised"] and sig["fact_issues"] == 1 and sig["key_risk"] == "Margins could fall."


def test_structured_signal_is_used_over_parsing_the_report():
    sig = fw.signal_record(fw.stance_from_scores({k: 1 for k, _, _ in fw.PILLARS}), _pillars(), None)
    found = run_insights.describe_result("alpha", "rag", {"tickers": ["X"], "results": [{"ticker": "X", "report": "# no markers", "signal": sig}]})
    assert found["signals"][0]["stance"] == "Cautiously Bullish" and found["signals"][0]["detail"]["pillars"]["alignment"]["score"] == 0


# ── the report node, end to end with fake models ──

class _Chain:
    def __init__(self, fn):
        self.fn = fn

    async def ainvoke(self, kw):
        return self.fn(kw)


def _out(analysis, rec="Neutral", score=0):
    return SimpleNamespace(analysis=analysis, key_points=[], recommendation=rec, score=score)


def _run_node(monkeypatch, docs_for, market, summary_text="Summary text."):
    from rag.graph import nodes
    from rag.prompts import prompts
    calls = {"revise": 0, "summary": None}
    writers = {
        "alignment": lambda kw: _out("[[-|Insiders sold $276.6M of shares.]]", "Negative — insider selling", -1),
        "liquidity": lambda kw: _out("[[-|The policy rate rose to 3.88%.]]", "Negative — tighter money", -1),
        "performance": lambda kw: _out(GOOGL_PERF, "Positive — strong growth", 2),
        "horizon": lambda kw: _out("[[+|Cloud revenue reached $58.0B.]]", "Positive — durable moat", 1),
        "action": lambda kw: _out("[[~|The P/E is 30.0.]]", "Neutral", 0),
    }
    for key, fn in writers.items():
        monkeypatch.setattr(prompts, f"get_alpha_{key}_chain", lambda llm, ticker=None, fn=fn: _Chain(fn))

    def revision(llm, model):
        def fix(kw):
            calls["revise"] += 1
            return _out(kw["analysis"].replace("remained stable at 32% from 2023 to 2025", "expanded from 27.4% (2023) to 32.0% (2025)"),
                        kw["recommendation"], kw["score"])
        return _Chain(fix)

    def summary(llm, analyst_notes=False):
        def run(kw):
            calls["summary"] = kw
            return SimpleNamespace(summary=summary_text, key_risk="Insider selling of $276.6M.", notes_view="")
        return _Chain(run)

    monkeypatch.setattr(prompts, "get_alpha_revision_chain", revision)
    monkeypatch.setattr(prompts, "get_alpha_summary_chain", summary)
    monkeypatch.setattr(nodes, "_verified_market_values", lambda t: market)
    monkeypatch.setattr(nodes, "get_llm", lambda *a, **k: object())
    state = {"ticker": "googl", "company_name": "alphabet", "analyst_notes": [],
             "alpha_dimensions": {k: {"documents": [Document(page_content=t, metadata={})] if t else []} for k, t in docs_for.items()}}
    return asyncio.run(nodes.alpha_generate_report(state)), calls


SOURCES = {"alignment": "Form 4: insiders sold shares worth $276.6M", "liquidity": "MACRO SNAPSHOT: Federal funds rate 3.88% (Oct 2026)",
           "performance": GOOGL_SRC[0], "horizon": "Google Cloud revenue was $58.0 billion", "action": "P/E 30.0"}


def test_report_node_revises_a_failed_check_computes_the_stance_and_uses_market_data(monkeypatch):
    market = {"price": 282.10, "sma200": 336.51, "rsi": 41.04, "pe": 25.9, "ebitda": 185.52e9, "as_of": "2026-11-26",
              "long_name": "Alphabet Inc."}
    out, calls = _run_node(monkeypatch, SOURCES, market)
    sig, report = out["alpha_result"], out["alpha_report"]
    assert calls["revise"] == 1                                               # Performance failed the margin check once
    assert "expanded from 27.4% (2023) to 32.0% (2025)" in report and "remained stable" not in report
    assert sig["pillar_detail"]["action"]["source"] == "market_data" and "($282.10)" in report and "30.0" not in report
    # scores -1, -1, +2, +1, -1 -> mean 0.0 -> Neutral; the summary did not state it, so it was added
    assert sig["stance"] == "Neutral" and "Overall, the investment stance is Neutral." in report
    assert calls["summary"]["stance"] == "Neutral" and "Alignment — score -1" in calls["summary"]["pillars"]
    assert report.startswith("# Indium's ALPHA Framework Analysis: Alphabet Inc. (GOOGL)") and sig["fact_issues"] == 0


def test_report_node_marks_missing_pillars_instead_of_inventing_them(monkeypatch):
    docs = dict(SOURCES, horizon=None, action=None)
    out, calls = _run_node(monkeypatch, docs, {}, summary_text="Overall, the investment stance is Neutral.")
    sig = out["alpha_result"]
    assert sig["partial"] and sig["missing"] == ["horizon", "action"]
    assert sig["stance"] == "Neutral" and "**Partial analysis:** Horizon, Action could not be completed" in out["alpha_report"]
    assert "Horizon, Action" in calls["summary"]["stance_basis"] or "missing: Horizon, Action" in calls["summary"]["stance_basis"]
