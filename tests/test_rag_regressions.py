"""
Offline regression tests for retrieval / ALPHA bugs found while debugging the
Embraer and Sanofi 20-F questions. No network, no LLM calls.

Run:  ./venv/bin/python -m pytest tests/test_rag_regressions.py -q
"""
import asyncio
from unittest.mock import MagicMock

from langchain_core.documents import Document

from rag.graph.nodes import (
    _CONTEXT_TOKEN_BUDGET,
    _alpha_source_label,
    _alpha_vdb_search,
    _doc_key,
    _fit_texts_to_token_budget,
    _format_alpha_docs,
)


# ---- chunk budgeting: whole chunks only --------------------------------------

def test_budget_keeps_everything_when_it_fits():
    kept, dropped, _ = _fit_texts_to_token_budget(["a b c", "d e f"], budget=100)
    assert kept == [0, 1] and dropped == 0


def test_budget_drops_whole_low_priority_chunks_never_cuts_one():
    texts = ["x " * 400, "y " * 400, "z " * 400]
    kept, dropped, used = _fit_texts_to_token_budget(texts, budget=900, priority=[1, 0, 0])
    assert kept == [1, 2] and dropped == 1 and used <= 900


def test_budget_never_returns_empty():
    kept, _, _ = _fit_texts_to_token_budget(["w " * 500], budget=10)
    assert kept == [0]


# ---- dedup key ---------------------------------------------------------------

def test_doc_key_distinguishes_chunks_sharing_a_page_header():
    header = "Table of Contents\nEmbraer S.A.\nNotes to the Consolidated Financial Statements\nIn millions"
    a, b = header + "\nBacklog US$31.6 billion", header + "\nContract assets 562.0"
    meta = {"company": "embraer f", "source_file": "x.pdf", "page_num": 223}
    assert a[:50] == b[:50]  # the old 50-char prefix key could not tell these apart
    assert _doc_key(meta, a) != _doc_key(meta, b)
    assert _doc_key(meta, a) == _doc_key(meta, a)


# ---- ALPHA: latest-annual-report scoping -------------------------------------

class _FakeDB:
    """Returns results only for the (years, filing_type) combinations in `have`."""

    def __init__(self, have):
        self.have, self.calls = have, []

    async def hybrid_search(self, **kw):
        self.calls.append(kw)
        years = tuple(kw.get("years") or ())
        return ["hit"] if years in self.have else []


def _run(coro):
    return asyncio.run(coro)


def test_alpha_search_is_scoped_to_latest_fiscal_year_and_annual_forms():
    db = _FakeDB(have={(2025,)})
    assert _run(_alpha_vdb_search(db, "q", 3, 2025)) == ["hit"]
    assert db.calls[0]["years"] == [2025]
    assert set(db.calls[0]["filing_type"]) == {"10-K", "20-F"}


def test_alpha_search_falls_back_to_prior_year_then_unfiltered():
    db = _FakeDB(have={(2024,)})
    assert _run(_alpha_vdb_search(db, "q", 3, 2025)) == ["hit"]
    assert [c.get("years") for c in db.calls] == [[2025], [2024]]

    db = _FakeDB(have=set())
    _run(_alpha_vdb_search(db, "q", 3, 2025))
    assert "years" not in db.calls[-1]  # last resort: unfiltered, so a dimension is never empty


# ---- ALPHA: labels come from metadata, not the file name ---------------------

def test_source_label_uses_fiscal_year_not_filing_date_in_filename():
    doc = Document(page_content="x", metadata={
        "source_file": "EMBJ_20-F_2026-03-30_000162828026021824.pdf",
        "filing_type": "20-F", "year": 2025, "period_end_date": "2025-12-31", "page_num": 87,
    })
    label = _alpha_source_label(doc, "EMBJ")
    assert "FY2025" in label and "20-F" in label and "2025-12-31" in label
    assert "2026" not in label


def test_web_excerpts_are_labelled_undated():
    doc = Document(page_content="x", metadata={"source": "govt_source", "title": "Fed policy"})
    assert "undated" in _alpha_source_label(doc, "EMBJ")


def test_format_alpha_docs_sends_whole_chunks():
    tail = "BACKLOG US$31.6 billion"
    doc = Document(page_content=("filler " * 700) + tail, metadata={"filing_type": "20-F", "year": 2025})
    assert tail in _format_alpha_docs([doc], "EMBJ")  # a 500-char cut would have hidden this


# ---- ALPHA: macro figures are dated, local, and authoritative ----------------

def test_macro_snapshot_is_dated():
    from app.utils.macro_utils import get_macro_snapshot_text
    text = get_macro_snapshot_text()
    if text:  # store absent in some environments -> block is simply omitted
        assert "Federal Funds Effective Rate" in text and "as of" in text


# ---- ALPHA prompts: guardrails stay in place ---------------------------------

def _prompt_vars_and_text(chain_factory):
    chain = chain_factory(MagicMock())
    prompt = chain.first
    text = " ".join(m.prompt.template for m in prompt.messages)
    return set(prompt.input_variables), text


def test_horizon_prompt_is_anchored_to_reported_results():
    from rag.prompts.prompts import get_alpha_horizon_chain
    variables, text = _prompt_vars_and_text(get_alpha_horizon_chain)
    assert "reported_fundamentals" in variables
    assert "must not contradict" in text.lower()


def test_liquidity_prompt_takes_rates_only_from_dated_snapshot():
    from rag.prompts.prompts import get_alpha_liquidity_chain
    _, text = _prompt_vars_and_text(get_alpha_liquidity_chain)
    assert "MACRO SNAPSHOT" in text and "ONLY permitted source" in text
    assert "Bank Liquidity & Credit Conditions" not in text


def test_context_budget_is_inside_the_model_window():
    assert _CONTEXT_TOKEN_BUDGET < 128_000


# ---- web fallback: never present another year's quarter as the one asked about ----

def test_period_mismatch_drops_last_years_quarter():
    from rag.graph.nodes import _period_mismatch
    q = "What were Sanofi's Dupixent sales in the third quarter of 2026?"
    assert _period_mismatch(q, "Sanofi reported third-quarter 2025 adjusted earnings of $1.70 per ADS.")
    assert not _period_mismatch(q, "Sanofi reported third-quarter 2026 results; Q3 2025 was lower.")
    assert not _period_mismatch(q, "Sanofi expects continued growth.")  # no quarter label -> kept
    assert not _period_mismatch(q, "Second quarter 2025 sales rose.")   # other quarter -> not this guard's job


def test_period_mismatch_only_fires_for_a_single_named_quarter():
    from rag.graph.nodes import _period_mismatch
    assert not _period_mismatch("Compare Q2 2026 and Q3 2026 sales", "third-quarter 2025 sales")
    assert not _period_mismatch("What is Embraer's backlog?", "third-quarter 2025 sales")
    assert _period_mismatch("Embraer net income Q2 2026", "In Q2 2025 net income was $78.6 million")


# ---- full-year questions: same guard, plus the period reaches the search query ----

def test_period_mismatch_full_year():
    from rag.graph.nodes import _period_mismatch
    q = "What was Embraer's net income in FY2025?"
    assert _period_mismatch(q, "For fiscal year 2024, Embraer net income was $0.3 billion; year ended December 31, 2024.")
    assert not _period_mismatch(q, "Embraer's FY2025 net income rose; fiscal year 2024 was lower.")
    assert not _period_mismatch(q, "Embraer net income rose sharply.")          # no year label -> kept
    assert not _period_mismatch("How has Embraer net income grown since 2020?", "FY2025 results")  # range -> untouched
    assert not _period_mismatch("Compare Embraer 2024 and 2025 net income", "fiscal year 2023 results")


def test_period_label_for_search_queries():
    from rag.graph.nodes import _period_label
    assert _period_label("Dupixent sales in the third quarter of 2026") == "Q3 2026"
    assert _period_label("Embraer net income in 2025") == "fiscal year 2025"
    assert _period_label("What is Embraer's backlog?") == ""


# ---- web fallback with SOME filing chunks already retrieved (offline, mocked search) ----

class _FakeTavily:
    """Stands in for TavilySearch; records the query and returns canned articles."""
    last_query = None
    canned = []

    def __init__(self, *a, **kw):
        pass

    def invoke(self, payload):
        _FakeTavily.last_query = payload["query"]
        return {"results": [{"url": u, "title": t, "content": c} for u, t, c in _FakeTavily.canned]}


def _fallback_state(question, existing, missing):
    from langchain_core.messages import HumanMessage
    return {
        "messages": [HumanMessage(content=question)],
        "documents": existing,
        "company_filter": ["sny"], "ticker": "sny",
        "sub_query_analysis": {"companies_detected": ["Sanofi"]},
        "financial_grading": {"missing_data_summary": missing},
    }


def test_fallback_keeps_filing_chunks_drops_wrong_year_and_states_period(monkeypatch):
    from rag.graph import nodes
    monkeypatch.setattr(nodes, "TavilySearch", _FakeTavily)
    _FakeTavily.canned = [
        ("https://x/q3-2025", "Sanofi Q3 earnings", "Sanofi reported third-quarter 2025 Dupixent sales of EUR 4.2 billion."),
        ("https://x/q3-2026", "Sanofi Q3 2026 results", "Third quarter 2026 Dupixent sales were EUR 5.9 billion."),
        ("https://x/profile", "Sanofi profile", "Sanofi is a global healthcare company."),
    ]
    filing_chunk = Document(page_content="FY2025 20-F text", metadata={"filing_type": "20-F", "year": 2025})
    out = nodes.integrate_web_search(_fallback_state(
        "What were Sanofi's Dupixent sales in the third quarter of 2026?", [filing_chunk],
        "Sanofi Dupixent product sales"))
    urls = [d.metadata.get("url") for d in out["documents"][1:]]
    assert out["documents"][0] is filing_chunk                     # existing filing chunks are kept, first
    assert "https://x/q3-2025" not in urls                          # last year's quarter is dropped
    assert "https://x/q3-2026" in urls and "https://x/profile" in urls
    assert "Q3 2026" in _FakeTavily.last_query                      # the period reaches the search


def test_fallback_full_year_wrong_year_dropped(monkeypatch):
    from rag.graph import nodes
    monkeypatch.setattr(nodes, "TavilySearch", _FakeTavily)
    _FakeTavily.canned = [
        ("https://x/fy24", "Embraer FY2024", "For fiscal year 2024 Embraer net income was $0.3 billion."),
        ("https://x/fy25", "Embraer FY2025", "Fiscal year 2025 net income was $0.6 billion."),
    ]
    out = nodes.integrate_web_search(_fallback_state(
        "What was Embraer's net income in 2025?", [], "Embraer net income"))
    assert [d.metadata["url"] for d in out["documents"]] == ["https://x/fy25"]
    assert "fiscal year 2025" in _FakeTavily.last_query


def test_scenario_retrieval_drops_wrong_year_sources(monkeypatch):
    from langchain_core.messages import HumanMessage
    from rag.graph import nodes
    monkeypatch.setattr(nodes, "TavilySearch", _FakeTavily)
    _FakeTavily.canned = [
        ("https://x/old", "Old", "Third-quarter 2025 earnings beat; analysts raised targets."),
        ("https://x/new", "New", "Third quarter 2026 earnings; analysts reiterate buy."),
    ]
    out = nodes.scenario_data_retrieve({
        "ticker": "sny",
        "messages": [HumanMessage(content="Bull bear base case for Sanofi after Q3 2026 results")],
    })
    for bucket in out["scenario_data"].values():
        assert [s["url"] for s in bucket] and all(s["url"] == "https://x/new" for s in bucket)


def test_scenario_retrieval_without_a_period_keeps_everything(monkeypatch):
    from langchain_core.messages import HumanMessage
    from rag.graph import nodes
    monkeypatch.setattr(nodes, "TavilySearch", _FakeTavily)
    _FakeTavily.canned = [("https://x/old", "Old", "Third-quarter 2025 earnings beat.")]
    out = nodes.scenario_data_retrieve({"ticker": "sny", "messages": [HumanMessage(content="Bull bear base case for Sanofi")]})
    assert all(len(b) >= 1 for b in out["scenario_data"].values())
    # and a state with no messages at all must not crash
    nodes.scenario_data_retrieve({"ticker": "sny"})
