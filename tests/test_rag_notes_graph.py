"""
Offline tests for how analyst notes move through the RAG graph: classification, the conditional prompt rule, grading
that judges filings only, and routing that ignores notes. All model calls are stubbed. No network.

Run:  ./venv/bin/python -m pytest -q tests/test_rag_notes_graph.py
"""
from types import SimpleNamespace

from langchain_core.documents import Document
from langchain_core.runnables import RunnableLambda

from rag.graph import edges, nodes
from rag.graph.doc_kinds import ANALYST_NOTE, is_analyst_note, split_notes
from rag.prompts import prompts
from app.services import analyst_notes as svc


def _filing(text="Revenue was $100M", **meta):
    return Document(page_content=text, metadata={"source_file": "AAPL_10-K_2025.pdf", "filing_type": "10-K", "company": "aapl", **meta})


def _note(text="[ANALYST NOTE — Ana's note] EPS est 1.31"):
    return Document(page_content=text, metadata={"source_type": ANALYST_NOTE, "filing_type": ANALYST_NOTE, "company": "AAPL",
                                                 "source_file": "Analyst note: Q3 call"})


# ---- classification ----------------------------------------------------------

def test_constant_matches_what_the_notes_service_writes():
    assert ANALYST_NOTE == svc.SOURCE_TYPE


def test_is_analyst_note_and_split_keep_order_and_tolerate_odd_inputs():
    f1, n1, f2, n2 = _filing("a"), _note("n1"), _filing("b"), _note("n2")
    assert [is_analyst_note(d) for d in (f1, n1)] == [False, True]
    rest, notes = split_notes([f1, n1, f2, n2])
    assert rest == [f1, f2] and notes == [n1, n2]
    assert split_notes(None) == ([], []) and split_notes([]) == ([], [])
    odd = [{"page_content": "x"}, "plain string", SimpleNamespace(metadata=None)]
    assert split_notes(odd) == (odd, [])  # never raises, treats unknown shapes as non-notes


# ---- prompt ------------------------------------------------------------------

def _system_prompt(**kw):
    chain = prompts.get_rag_chain(RunnableLambda(lambda x: x), **kw)
    return chain.first.format_messages(documents="D", question="Q")[0].content


def test_prompt_is_identical_without_notes_and_only_gains_the_rule_with_them():
    for qt in ("general", "multi_company", "segment", "financial_calculation"):
        base = _system_prompt(query_type=qt)
        assert "ANALYST NOTE" not in base
        assert _system_prompt(query_type=qt, analyst_notes=False) == base
        with_notes = _system_prompt(query_type=qt, analyst_notes=True)
        assert with_notes.replace(prompts.ANALYST_NOTE_RULES, "", 1) == base  # nothing else changed
        assert "NOT company disclosures" in with_notes and "filing governs" in with_notes


# ---- grade_documents ---------------------------------------------------------

class _Grader:
    def __init__(self, fail=False):
        self.seen, self.fail = [], fail

    def invoke(self, inputs):
        self.seen.append(inputs)
        if self.fail:
            raise RuntimeError("grader down")
        return SimpleNamespace(is_sufficient=True, missing_data_summary="")


def _state(docs):
    return {"messages": [SimpleNamespace(content="What is AAPL revenue?")], "documents": docs,
            "sub_query_analysis": {"companies_detected": ["aapl"], "sub_queries": []}, "web_searched": False}


def _grade(monkeypatch, docs, grader):
    monkeypatch.setattr(nodes, "ChatOpenAI", lambda *a, **k: object())
    monkeypatch.setattr(nodes, "get_financial_analyst_grader_chain", lambda llm: grader)
    return nodes.grade_documents(_state(docs))


def test_grader_never_sees_notes_but_generation_still_gets_them(monkeypatch):
    grader = _Grader()
    f1, n1, f2 = _filing("rev A"), _note("SECRET-NOTE-TEXT"), _filing("rev B")
    out = _grade(monkeypatch, [f1, n1, f2], grader)
    assert "SECRET-NOTE-TEXT" not in grader.seen[0]["doc_content"] and "rev A" in grader.seen[0]["doc_content"]
    assert out["documents"] == [f1, f2, n1]  # filings first, notes kept for generation
    assert out["financial_grading"]["overall_grade"] == "sufficient" and out["financial_grading"]["documents_graded_count"] == 2


def test_only_notes_means_insufficient_without_calling_the_grader(monkeypatch):
    grader = _Grader()
    n = _note()
    out = _grade(monkeypatch, [n], grader)
    assert grader.seen == [] and out["documents"] == [n]
    assert out["financial_grading"]["overall_grade"] == "insufficient" and out["financial_grading"]["can_answer"] is False


def test_grader_failure_keeps_filings_and_notes(monkeypatch):
    f, n = _filing(), _note()
    out = _grade(monkeypatch, [f, n], _Grader(fail=True))
    assert out["documents"] == [f, n] and "error" in out["financial_grading"]


def test_grading_without_notes_is_unchanged(monkeypatch):
    grader = _Grader()
    docs = [_filing("a"), _filing("b")]
    out = _grade(monkeypatch, docs, grader)
    assert out["documents"] == docs and out["financial_grading"]["documents_graded_count"] == 2


def test_no_documents_at_all_is_still_insufficient(monkeypatch):
    out = _grade(monkeypatch, [], _Grader())
    assert out["documents"] == [] and out["financial_grading"]["overall_grade"] == "insufficient"


# ---- generate ----------------------------------------------------------------

def _generate(monkeypatch, docs):
    captured = {}

    class Chain:
        def invoke(self, inputs):
            captured["inputs"] = inputs
            return "answer"

    def fake_chain(llm, **kw):
        captured["kwargs"] = kw
        return Chain()

    monkeypatch.setattr(nodes, "ChatOpenAI", lambda *a, **k: object())
    monkeypatch.setattr(nodes, "get_rag_chain", fake_chain)
    out = nodes.generate({"messages": [SimpleNamespace(content="q")], "documents": docs, "sub_query_analysis": {}})
    return out, captured


def test_generate_adds_the_note_rule_only_when_a_note_is_in_context(monkeypatch):
    out, cap = _generate(monkeypatch, [_filing(), _note()])
    assert cap["kwargs"]["analyst_notes"] is True and len(cap["inputs"]["documents"]) == 2 and out["Intermediate_message"] == "answer"
    _, cap = _generate(monkeypatch, [_filing(), _filing("x")])
    assert cap["kwargs"]["analyst_notes"] is False


# ---- routing -----------------------------------------------------------------

def _decide(docs, **state):
    return edges.decide_to_generate({"documents": docs, **state})


def test_routing_ignores_notes_when_counting_chunks():
    notes = [_note(), _note(), _note()]
    assert _decide(notes) == "integrate_web_search"  # nothing but notes = no filing data
    assert _decide([_filing()] + notes) == "integrate_web_search"  # ungraded: 1 filing chunk is still < 3
    assert _decide([_filing(), _filing(), _filing()] + notes) == "generate"


def test_routing_without_notes_is_unchanged():
    assert _decide([]) == "integrate_web_search"
    assert _decide([_filing()] * 3) == "generate"
    assert _decide([_filing()] * 2) == "integrate_web_search"
    sufficient = {"financial_grading": {"overall_grade": "sufficient", "can_answer": True}}
    assert _decide([_filing()], **sufficient) == "generate"
    assert _decide([_filing()], web_searched=True) == "generate"
    assert _decide([_filing()], financial_grading={"overall_grade": "partial", "can_answer": False}) == "integrate_web_search"


def test_notes_do_not_block_the_web_search_decision_when_grade_is_insufficient():
    insufficient = {"financial_grading": {"overall_grade": "insufficient", "can_answer": False}}
    assert _decide([_filing(), _note()], **insufficient) == "integrate_web_search"


# ---- the user's notes are never the part dropped by the token budget ----------

def test_notes_survive_a_budget_overflow_that_drops_filing_chunks(monkeypatch):
    big1, big2 = _filing("word " * 40000, page_num=1), _filing("word " * 40000, page_num=2)  # ~80k tokens > 70k budget
    note = _note()
    _, cap = _generate(monkeypatch, [big1, big2, note])  # the note is LAST: the old order would have dropped it first
    kept = cap["inputs"]["documents"]
    assert note in kept and len(kept) == 2  # one filing chunk went, the note stayed
    assert cap["kwargs"]["analyst_notes"] is True


def test_filing_only_budget_behaviour_is_unchanged(monkeypatch):
    web = Document(page_content="word " * 40000, metadata={"source": "web_search"})
    f1, f2 = _filing("word " * 40000, page_num=1), _filing("word " * 30000, page_num=2)
    _, cap = _generate(monkeypatch, [web, f1, f2])
    assert web not in cap["inputs"]["documents"] and f1 in cap["inputs"]["documents"]  # web results still go first


# ---- how many note chunks a question can bring in ------------------------------

def test_note_limit_scales_with_tickers_and_is_capped(monkeypatch):
    seen = {}

    async def fake_search(user_id, query, tickers, limit=3):
        seen["limit"], seen["tickers"] = limit, tickers
        return []

    monkeypatch.setattr(svc, "search_notes", fake_search)
    import asyncio
    run = lambda tickers: asyncio.run(nodes._analyst_note_documents({"configurable": {"user_id": "7"}}, tickers, "q"))
    run(["aapl"])
    assert seen == {"limit": 3, "tickers": ["AAPL"]}
    run(["aapl", "msft"])
    assert seen["limit"] == 6
    run(["a", "b", "c", "d"])
    assert seen["limit"] == 6  # capped
    seen.clear()
    assert asyncio.run(nodes._analyst_note_documents({"configurable": {}}, ["aapl"], "q")) == [] and seen == {}  # no user: inert


# ---- ALPHA: notes reach only the combiner, and only when they exist ------------

def test_alpha_combiner_prompt_is_unchanged_without_notes():
    plain = prompts.get_alpha_report_combiner_chain(RunnableLambda(lambda x: x))
    explicit = prompts.get_alpha_report_combiner_chain(RunnableLambda(lambda x: x), analyst_notes=False)
    render = lambda c, **kw: [m.content for m in c.first.format_messages(company="C", ticker="T", alignment="a", liquidity="l",
                                                                          performance="p", horizon="h", action="x", **kw)]
    assert "analyst_notes" not in plain.first.input_variables and render(plain) == render(explicit)
    with_notes = prompts.get_alpha_report_combiner_chain(RunnableLambda(lambda x: x), analyst_notes=True)
    sys_p, human = render(with_notes, analyst_notes="NOTE-BODY")
    assert "analyst_notes" in with_notes.first.input_variables and "NOTE-BODY" in human
    assert "Your notes:" in sys_p and sys_p.startswith(render(plain)[0])  # the original prompt is intact, the rule only appended
    assert human.startswith(render(plain)[1].rsplit("\n\nRender the full", 1)[0])


def _alpha_state(notes):
    docs = {k: {"documents": []} for k in ("alignment", "liquidity", "performance", "horizon", "action")}
    return {"ticker": "AAPL", "alpha_dimensions": docs, "analyst_notes": notes}


def _run_alpha(monkeypatch, notes):
    seen = {}
    dim_chain = lambda *a, **k: SimpleNamespace(invoke=lambda kw: SimpleNamespace(analysis="dim analysis", key_points=[], recommendation=""))

    def combiner(llm, analyst_notes=False):
        seen["flag"] = analyst_notes
        return SimpleNamespace(invoke=lambda kw: seen.setdefault("inputs", kw) and "REPORT")

    for name in ("alignment", "liquidity", "performance", "horizon", "action"):
        monkeypatch.setattr(prompts, f"get_alpha_{name}_chain", dim_chain)
    monkeypatch.setattr(prompts, "get_alpha_report_combiner_chain", combiner)
    monkeypatch.setattr(nodes, "_fetch_verified_market_data", lambda t: "")
    import langchain_openai
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", lambda *a, **k: object())
    out = nodes.alpha_generate_report(_alpha_state(notes))
    return out, seen


def test_alpha_report_passes_the_notes_to_the_combiner_only_when_present(monkeypatch):
    out, seen = _run_alpha(monkeypatch, [_note("[ANALYST NOTE] my thesis is X")])
    assert seen["flag"] is True and "my thesis is X" in seen["inputs"]["analyst_notes"] and out["alpha_report"] == "REPORT"
    out, seen = _run_alpha(monkeypatch, [])
    assert seen["flag"] is False and "analyst_notes" not in seen["inputs"]  # exactly the call it made before


# ---- scenario: its own tiny async node, prepended outside the web budget -------

def test_scenario_load_notes_always_sets_the_key(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    got = AsyncMock(return_value=[_note()])
    monkeypatch.setattr(nodes, "_analyst_note_documents", got)
    out = asyncio.run(nodes.scenario_load_notes({"ticker": "aapl"}, {"configurable": {"user_id": "7"}}))
    assert out == {"analyst_notes": [got.return_value[0]]} and got.await_args.args[1] == ["AAPL"]
    got.return_value = []
    assert asyncio.run(nodes.scenario_load_notes({}, {"configurable": {"user_id": "7"}})) == {"analyst_notes": []}  # no ticker: no lookup target
    assert got.await_args.args[1] == []


def _run_scenario(monkeypatch, notes):
    seen = {}

    def case_chain(llm):
        def invoke(kw):
            seen.setdefault("analyst_data", kw["analyst_data"])
            raise RuntimeError("stop here: the case result is not what this test is about")
        return SimpleNamespace(invoke=invoke)

    for name in ("bull", "bear", "base"):
        monkeypatch.setattr(prompts, f"get_scenario_{name}_chain", case_chain)
    monkeypatch.setattr(prompts, "get_scenario_report_combiner_chain", lambda llm: SimpleNamespace(invoke=lambda kw: "REPORT"))
    import langchain_openai
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", lambda *a, **k: object())
    state = {"ticker": "aapl", "scenario_data": {"analyst_data": [{"title": "Broker view", "url": "u", "content": "PT 230"}]}, "analyst_notes": notes}
    nodes.scenario_generate_report(state)
    return seen["analyst_data"]


def test_scenario_cases_see_the_notes_labelled_and_before_the_web_data(monkeypatch):
    text = _run_scenario(monkeypatch, [_note("[ANALYST NOTE] I think PT is 250")])
    assert "THE USER'S OWN ANALYST NOTES" in text and "I think PT is 250" in text and "Broker view" in text
    assert text.index("I think PT is 250") < text.index("Broker view")


def test_scenario_without_notes_is_unchanged(monkeypatch):
    text = _run_scenario(monkeypatch, [])
    assert "ANALYST NOTES" not in text and text.startswith("**Broker view**")


def test_graph_wires_the_scenario_notes_node_between_retrieve_and_generate():
    import asyncio
    from rag.graph.builder import BuildingGraph
    graph = asyncio.run(BuildingGraph().get_graph()).get_graph()
    edges_ = {(e.source, e.target) for e in graph.edges}
    assert ("scenario_retrieve", "scenario_notes") in edges_ and ("scenario_notes", "scenario_generate") in edges_
    assert ("scenario_retrieve", "scenario_generate") not in edges_
