from rag.eval import metrics as m

CASE = {"id": "c", "question": "q", "expected": {"facts": ["$4.2 billion", "Services segment", "18.5%"], "sources": ["10-K"], "must_not_include": ["restated"]}}


def test_scoring_matches_figures_by_value_and_phrases_by_text():
    resp = {"answer": "Revenue was 4,200 million, led by the Services segment. Margin 18.5%.", "documents": [{"metadata": {"filing_type": "10-K"}}],
            "verification": {"status": "verified", "issues": []}, "usage": {"total_tokens": 900, "cost": {"estimated_cost_usd": 0.01}}}
    r = m.score_case(CASE, resp, 2.0)
    assert r["fact_recall"] == 1.0 and r["retrieval_hit"] == 1.0 and not r["forbidden_found"] and r["tokens"] == 900


def test_missing_and_forbidden():
    r = m.score_case(CASE, {"answer": "Revenue was $5 billion after it was restated.", "documents": []}, 1.0)
    assert r["fact_recall"] == 0.0 and r["forbidden_found"] == ["restated"] and r["retrieval_hit"] == 0.0 and len(r["missing_facts"]) == 3


def test_unscored_case_and_summary_and_regressions():
    r = m.score_case({"id": "u", "question": "q"}, {"answer": "x"}, 0.5)
    assert r["scored"] is False and r["fact_recall"] is None
    base = m.summarize([{**m.score_case(CASE, {"answer": "$4.2 billion Services segment 18.5%", "documents": [{"metadata": "10-K"}]}, 1)}])
    worse = m.summarize([{**m.score_case(CASE, {"answer": "nothing", "documents": []}, 1)}])
    assert m.regressions(base, base) == [] and any("fact_recall" in x for x in m.regressions(base, worse))
