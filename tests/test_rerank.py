from types import SimpleNamespace as D

from rag.retrieval import rerank as rr


def test_order_is_best_first_and_stable():
    assert rr.order_by_scores([0.1, 0.9, 0.9, 0.3]) == [1, 2, 3, 0]


def test_rerank_orders_and_cuts(monkeypatch):
    docs = [D(page_content=t) for t in ("a", "b", "c", "d")]
    monkeypatch.setattr(rr, "_scores_local", lambda q, texts: [0.1, 0.8, 0.2, 0.9])
    out, ran = rr.rerank("q", docs, "local", top_n=2)
    assert ran and [d.page_content for d in out] == ["d", "b"]


def test_rerank_falls_back_when_unavailable(monkeypatch):
    docs = [D(page_content="a"), D(page_content="b")]
    monkeypatch.setattr(rr, "_scores_local", lambda q, t: None)
    assert rr.rerank("q", docs, "local") == (docs, False)
    monkeypatch.setattr(rr, "_scores_local", lambda q, t: (_ for _ in ()).throw(RuntimeError("boom")))
    assert rr.rerank("q", docs, "local") == (docs, False)
    assert rr.rerank("q", docs, "off") == (docs, False)
