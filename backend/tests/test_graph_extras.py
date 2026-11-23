from types import SimpleNamespace as D

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage

from app.services import app_settings
from app.services.app_settings import resolver
from rag.graph import extras

FW = {"id": "credit", "name": "Credit review", "triggers": ["leverage review"], "sections": ["Leverage", "Liquidity"], "prompt": "p", "enabled": True}


def cfg(**sections):
    return app_settings.use(resolver.resolve(sections))


cfgs = cfg


def st(q="what is revenue?", **kw):
    return {"messages": [HumanMessage(content=q)], **kw}


def test_custom_framework_matching():
    with cfg(modules={"custom": [FW]}):
        assert extras.match_custom_framework("Run a leverage review of ACME")["id"] == "credit"
        assert extras.match_custom_framework("anything", forced_id="credit")["id"] == "credit"
        assert extras.match_custom_framework("revenue?") is None
    with cfg(modules={"custom": [{**FW, "enabled": False}]}):
        assert extras.match_custom_framework("leverage review") is None


def test_route_workflow_priority():
    assert extras.route_workflow({"alpha_mode": True, "custom_module": FW}) == "alpha"
    assert extras.route_workflow({"macro_mode": True}) == "macro"
    assert extras.route_workflow({"custom_module": FW}) == "custom"
    assert extras.route_workflow({}) == "normal"


def good_grade(**kw):
    return st(documents=[D(page_content="x", metadata={})] * 5, financial_grading={"overall_grade": "partial", "can_answer": False}, **kw)


def test_corrective_retry_then_web_then_generate():
    with cfg(retrieval={"corrective_retries": 1, "web_fallback": True}):
        assert extras.decide_after_grade(good_grade(rewrite_count=0)) == "rewrite"
        assert extras.decide_after_grade(good_grade(rewrite_count=1)) == "integrate_web_search"
    with cfg(retrieval={"corrective_retries": 0, "web_fallback": False}):
        assert extras.decide_after_grade(good_grade()) == "generate"
    with cfg(retrieval={"corrective_retries": 2, "sources": ["filings"]}):
        assert extras.decide_after_grade(good_grade(rewrite_count=2)) == "generate"           # web not an allowed source
    sufficient = st(documents=[D(page_content="x", metadata={})] * 5, financial_grading={"overall_grade": "sufficient", "can_answer": True})
    assert extras.decide_after_grade(sufficient) == "generate"


def test_rewrite_sets_direct_query_and_counts(monkeypatch):
    class L:
        def invoke(self, m):
            return D(content='"Net revenues by segment fiscal 2024"')
    monkeypatch.setattr(app_settings, "get_llm", lambda role, **k: L())
    out = extras.rewrite_query(st(financial_grading={"missing_data_summary": "no segment table"}, sub_query_analysis={"query_type": "single_company", "needs_sub_queries": True, "sub_queries": ["a"]}))
    assert out["rewrite_count"] == 1 and out["documents"] == []
    assert out["sub_query_analysis"]["optimized_query"] == "Net revenues by segment fiscal 2024"
    assert out["sub_query_analysis"]["needs_sub_queries"] is False and out["sub_query_analysis"]["query_type"] == "single_company"


def test_verify_modes():
    docs = [D(page_content="Revenue was $4.2 billion in fiscal 2024.")]
    base = st(Intermediate_message="Revenue was $4.2 billion. Net income was $912 million.", documents=docs)
    with cfg(answers={"verify_mode": "off"}):
        assert extras.verify_answer(base) == {}
    with cfg(answers={"verify_mode": "warn"}):
        out = extras.verify_answer(base)
        assert out["Intermediate_message"].startswith("Revenue was") and "double-check" in out["Intermediate_message"] and out["verification"]["status"] == "issues"
    with cfg(answers={"verify_mode": "strict"}):
        assert extras.verify_answer(base)["Intermediate_message"].startswith("**Not verified")
    with cfg(answers={"verify_mode": "strict"}):
        clean = extras.verify_answer(st(Intermediate_message="Revenue was $4.2 billion.", documents=docs))
        assert clean["verification"]["status"] == "verified" and "Intermediate_message" not in clean
        assert extras.verify_answer(st(Intermediate_message="Revenue was 4.2", documents=docs, macro_mode=True)) == {}


def test_clarification_heuristic():
    s = st("what is the revenue?", company_filter=["AAPL", "MSFT"])
    assert "AAPL, MSFT" in extras.needs_clarification(s)
    assert extras.needs_clarification(st("what is the revenue of AAPL?", company_filter=["AAPL", "MSFT"])) is None
    assert extras.needs_clarification(st("compare revenue", company_filter=["AAPL", "MSFT"])) is None
    assert extras.needs_clarification(st("what is the revenue?", company_filter=["AAPL"])) is None
    assert extras.needs_clarification({**s, "clarified": True}) is None
    with cfg(answers={"clarify_ambiguous": False}):
        assert extras.clarify(s) == {}


def test_clarify_interrupts_and_resumes_with_the_answer():
    import asyncio
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command
    from rag.graph.state import GraphState

    g = StateGraph(GraphState)
    g.add_node("clarify", extras.clarify)
    g.add_edge(START, "clarify")
    g.add_edge("clarify", END)
    app = g.compile(checkpointer=MemorySaver())
    conf = {"configurable": {"thread_id": "t1"}}

    async def go():
        with cfg(answers={"clarify_ambiguous": True}):
            first = await app.ainvoke(st("what is the revenue?", company_filter=["AAPL", "MSFT"]), conf)
            assert "AAPL, MSFT" in first["__interrupt__"][0].value["question"]
            second = await app.ainvoke(Command(resume="MSFT please"), conf)
            return second
    out = asyncio.run(go())
    assert out["company_filter"] == ["MSFT"] and out["clarified"] is True
    assert out["messages"][-1].content == "what is the revenue? (regarding: MSFT please)"


def test_prompt_overrides_and_style():
    from rag.prompts import registry, prompts
    default = "BASE"
    assert registry.apply("generator.system", default) == "BASE"
    with cfg(prompts={"overrides": {"generator.system": "  MINE  "}}, answers={"style": "concise"}):
        assert registry.apply("generator.system", default) == "MINE" and "concise" in registry.style_suffix().lower()
    import pytest
    with pytest.raises(KeyError):
        registry.apply("grader.system", default)
    lst = registry.listing()
    assert {p["name"] for p in lst} == set(registry.EDITABLE) and all(p["default"].strip() for p in lst)
    assert "senior Investment Analyst" in prompts._generator_base_prompt(2026)


def test_custom_framework_searches_filings_and_uploads_and_returns_documents(monkeypatch):
    from types import SimpleNamespace as P
    from rag.graph import nodes
    from app.services import vectordb_manager as vm
    from app.services.user_docs import store as udocs
    from app.utils import company_mapping

    async def fake_alpha(db, query, limit, year):
        return [P(payload={"page_content": f"filing: {query[:20]}", "metadata": {"source_file": "AAPL_10-K.pdf"}})]       # raw points, as the real helper returns
    async def fake_uploads(user_id, query, limit=6, **kw):
        return [Document(page_content=f"upload: {query[:20]}", metadata={"source_file": "zeta.xlsx"})]
    monkeypatch.setattr(nodes, "_alpha_vdb_search", fake_alpha)
    monkeypatch.setattr(nodes, "_analyst_note_documents", lambda *a, **k: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(vm, "get_vectordb_manager", lambda: P(get_instance=lambda t, create_if_missing=False: object()))
    monkeypatch.setattr(company_mapping, "get_most_recent_filed_fiscal_year", lambda t: 2025)
    monkeypatch.setattr(udocs, "search", fake_uploads)
    fw = {**FW, "sources": ["filings", "uploads"]}
    state = st("run it", custom_module=fw, ticker="AAPL")
    cfg = {"configurable": {"user_id": "7"}}
    import asyncio as aio
    with cfgs(retrieval={"sources": ["filings", "uploads", "notes"]}):
        out = aio.run(extras.custom_retrieve(state, cfg))
    assert set(out["custom_sections"]) == {"Leverage", "Liquidity"} and all(len(v) == 2 for v in out["custom_sections"].values())
    assert all(isinstance(d, Document) for d in out["documents"]) and {d.metadata["source_file"] for d in out["documents"]} == {"AAPL_10-K.pdf", "zeta.xlsx"}
    with cfgs(retrieval={"sources": ["uploads"]}):                      # the user switched filings off: only uploads are searched
        out = aio.run(extras.custom_retrieve(state, cfg))
    assert {d.metadata["source_file"] for d in out["documents"]} == {"zeta.xlsx"}
    fake_llm = type("L", (), {"invoke": lambda self, m: P(content="REPORT")})()
    monkeypatch.setattr(app_settings, "get_llm", lambda role, **k: fake_llm)
    assert extras.custom_generate({**state, **out})["Intermediate_message"] == "REPORT"
