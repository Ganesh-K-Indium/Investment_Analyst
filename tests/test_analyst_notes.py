"""
Offline tests for analyst notes: chunking, the owner/visibility filter (against a real in-memory Qdrant), the
/notes API (ownership, sharing, failure handling), the RAG hook, and hybrid_search's backward compatibility.
No network, no embeddings.

Run:  ./venv/bin/python -m pytest -q tests/test_analyst_notes.py
"""
import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient, models
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import notes as notes_api
from app.auth.deps import get_current_user
from app.database.connection import get_db_session
from app.database.models import AnalystNote, AnalystNoteInclusion, Base
from app.services import analyst_notes as svc

_real_included_note_ids = svc.included_note_ids  # the autouse fixture stubs the module attribute


# ---- pure helpers ------------------------------------------------------------

def test_chunk_note_packs_paragraphs_and_never_returns_empty():
    text = "AAPL call\n\nPT $215\n\n\n\nwatch services margin"
    assert svc.chunk_note(text) == ["AAPL call\n\nPT $215\n\nwatch services margin"]
    assert svc.chunk_note("   \n\n  ") == []


def test_chunk_note_splits_on_budget_and_wraps_long_paragraphs():
    paras = ["p%d " % i + "x" * 40 for i in range(6)]
    chunks = svc.chunk_note("\n\n".join(paras), max_chars=100)
    assert len(chunks) > 1 and all(len(c) <= 100 for c in chunks)
    assert "".join(c.replace("\n\n", "") for c in chunks) == "".join(paras)
    long = svc.chunk_note("word " * 100, max_chars=50)
    assert all(0 < len(c) <= 50 for c in long)


def test_normalize_ticker():
    assert svc.normalize_ticker(" aapl ") == "AAPL"
    assert svc.normalize_ticker("brk.b") == "BRK.B"
    for bad in ("", "  ", "TOO LONG TICKER", "A$B", "-X"):
        with pytest.raises(ValueError):
            svc.normalize_ticker(bad)


def test_point_ids_are_stable_and_distinct():
    assert svc.point_id("n1", 0) == svc.point_id("n1", 0)
    assert svc.point_id("n1", 0) != svc.point_id("n1", 1) != svc.point_id("n2", 1)


# ---- the visibility filter, against a real (in-memory) Qdrant ----------------

def _seed_qdrant():
    client = QdrantClient(":memory:")
    client.create_collection("analyst_notes", vectors_config={"dense": models.VectorParams(size=3, distance=models.Distance.COSINE)})
    rows = [  # (id, owner, visibility, ticker)
        (1, "alice", "private", "AAPL"), (2, "alice", "shared", "AAPL"),
        (3, "bob", "private", "AAPL"), (4, "bob", "shared", "MSFT"), (5, "alice", "private", "MSFT"),
    ]
    client.upsert("analyst_notes", points=[
        models.PointStruct(id=i, vector={"dense": [1.0, 0.0, 0.0]},
                           payload={"page_content": f"n{i}", "metadata": {"owner_id": o, "visibility": v, "ticker": t, "note_id": f"n{i}"}})
        for i, o, v, t in rows])
    return client


def _visible(client, user, tickers=None, included=()):
    pts, _ = client.scroll("analyst_notes", scroll_filter=svc.read_filter(user, tickers, included), limit=50)
    return sorted(p.payload["page_content"] for p in pts)


def test_filter_defaults_to_own_notes_only():
    c = _seed_qdrant()
    assert _visible(c, "alice") == ["n1", "n2", "n5"]  # others' shared notes are NOT used unless opted in
    assert _visible(c, "bob") == ["n3", "n4"]
    assert _visible(c, "carol") == []


def test_filter_includes_only_opted_in_shared_notes():
    c = _seed_qdrant()
    assert _visible(c, "bob", included=["n2"]) == ["n2", "n3", "n4"]  # alice's shared n2 opted in
    assert _visible(c, "carol", included=["n2", "n4"]) == ["n2", "n4"]
    assert _visible(c, "carol", included=["n1"]) == []  # opting in to a PRIVATE note exposes nothing
    assert _visible(c, "carol", included=["n3"]) == []


def test_filter_ticker_restriction_and_case():
    c = _seed_qdrant()
    assert _visible(c, "alice", ["aapl"]) == ["n1", "n2"]
    assert _visible(c, "bob", ["MSFT", "AAPL"], included=["n2"]) == ["n2", "n3", "n4"]
    assert _visible(c, "carol", ["TSLA"], included=["n2", "n4"]) == []


def test_paused_notes_are_excluded_for_everyone_but_legacy_chunks_still_count():
    c = _seed_qdrant()  # these seed chunks have no `active` field at all: they must still count as active
    c.set_payload("analyst_notes", payload={"active": False}, key="metadata", points=[1, 2])
    assert _visible(c, "alice") == ["n5"]  # the owner's paused notes drop out of her own analysis
    assert _visible(c, "bob", included=["n2"]) == ["n3", "n4"]  # and out of an opted-in reader's
    c.set_payload("analyst_notes", payload={"active": True}, key="metadata", points=[1])
    assert _visible(c, "alice") == ["n1", "n5"]


def test_user_id_is_compared_as_string():
    c = _seed_qdrant()
    c.upsert("analyst_notes", points=[models.PointStruct(
        id=9, vector={"dense": [1.0, 0.0, 0.0]},
        payload={"page_content": "n9", "metadata": {"owner_id": "42", "visibility": "private", "ticker": "AAPL"}})])
    assert "n9" in _visible(c, 42) and "n9" not in _visible(c, 4)


# ---- retrieval service + RAG hook --------------------------------------------

@pytest.fixture(autouse=True)
def _no_inclusions(monkeypatch):
    """No test touches a real DB for opt-ins; tests that care override svc.included_note_ids."""
    monkeypatch.setattr(svc, "included_note_ids", AsyncMock(return_value=[]))

class FakeDb:
    def __init__(self, count=1, points=()):
        self.async_qdrant_client = SimpleNamespace(count=AsyncMock(return_value=SimpleNamespace(count=count)))
        self.hybrid_search = AsyncMock(return_value=list(points))


def _payload(**meta):
    base = {"author": "Ana", "visibility": "private", "ticker": "AAPL", "note_date": "2026-04-06", "title": "Q3 call", "page_num": 1}
    return SimpleNamespace(payload={"page_content": "PT $215 (was 200)", "metadata": {**base, **meta}})


def test_search_notes_filters_by_user_and_labels_chunks(monkeypatch):
    db = FakeDb(points=[_payload()])
    monkeypatch.setattr(svc, "_get_db", lambda: db)
    docs = asyncio.run(svc.search_notes("u1", "what is the price target", ["AAPL"], limit=3))
    assert len(docs) == 1
    assert docs[0].page_content.startswith("[ANALYST NOTE") and "not a company disclosure" in docs[0].page_content
    assert docs[0].page_content.endswith("PT $215 (was 200)")
    kwargs = db.hybrid_search.await_args.kwargs
    assert kwargs["content_type"] == "text" and kwargs["limit"] == 3
    assert kwargs["extra_conditions"][0].model_dump() == svc.read_filter("u1", ["AAPL"]).model_dump()


def test_search_notes_applies_the_users_opt_ins(monkeypatch):
    db = FakeDb(points=[_payload()])
    monkeypatch.setattr(svc, "_get_db", lambda: db)
    svc.included_note_ids.return_value = ["n7"]
    asyncio.run(svc.search_notes("u1", "q", ["AAPL"]))
    assert db.hybrid_search.await_args.kwargs["extra_conditions"][0].model_dump() == svc.read_filter("u1", ["AAPL"], ["n7"]).model_dump()
    svc.included_note_ids.assert_awaited_with("u1")


def test_included_note_ids_reads_the_users_rows_and_fails_closed(monkeypatch):
    from app.database import connection

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            s.add_all([AnalystNoteInclusion(user_id="7", note_id="a"), AnalystNoteInclusion(user_id="7", note_id="b"),
                       AnalystNoteInclusion(user_id="8", note_id="c")])
            await s.commit()
        monkeypatch.setattr(connection, "SessionLocal", maker)
        assert sorted(await _real_included_note_ids(7)) == ["a", "b"]  # int user id is compared as a string
        assert await _real_included_note_ids("9") == []

        def broken():
            raise RuntimeError("db down")
        monkeypatch.setattr(connection, "SessionLocal", broken)
        assert await _real_included_note_ids("7") == []  # fails closed: shared notes just drop out of the answer

    asyncio.run(run())


def test_search_notes_skips_embedding_when_nothing_readable(monkeypatch):
    db = FakeDb(count=0)
    monkeypatch.setattr(svc, "_get_db", lambda: db)
    assert asyncio.run(svc.search_notes("u1", "q", ["AAPL"])) == []
    db.hybrid_search.assert_not_awaited()


def test_search_notes_requires_user_and_tickers(monkeypatch):
    monkeypatch.setattr(svc, "_get_db", lambda: pytest.fail("must not touch Qdrant"))
    assert asyncio.run(svc.search_notes(None, "q", ["AAPL"])) == []
    assert asyncio.run(svc.search_notes("u1", "q", [])) == []


def test_search_notes_never_raises(monkeypatch):
    db = FakeDb()
    db.hybrid_search = AsyncMock(side_effect=RuntimeError("qdrant down"))
    monkeypatch.setattr(svc, "_get_db", lambda: db)
    assert asyncio.run(svc.search_notes("u1", "q", ["AAPL"])) == []
    db2 = FakeDb()
    db2.async_qdrant_client.count = AsyncMock(side_effect=RuntimeError("Collection `analyst_notes` doesn't exist"))
    monkeypatch.setattr(svc, "_get_db", lambda: db2)
    assert asyncio.run(svc.has_visible_notes("u1", ["AAPL"])) is False


def test_rag_hook_is_inert_without_a_user_and_swallows_errors(monkeypatch):
    from rag.graph import nodes
    monkeypatch.setattr(svc, "search_notes", AsyncMock(side_effect=AssertionError("must not be called")))
    for config in (None, {}, {"configurable": {"thread_id": "t"}}):
        assert asyncio.run(nodes._analyst_note_documents(config, {"aapl"}, "q")) == []
    cfg = {"configurable": {"user_id": "7"}}
    assert asyncio.run(nodes._analyst_note_documents(cfg, set(), "q")) == []

    monkeypatch.setattr(svc, "search_notes", AsyncMock(side_effect=RuntimeError("boom")))
    assert asyncio.run(nodes._analyst_note_documents(cfg, {"aapl"}, "q")) == []

    search = AsyncMock(return_value=["doc"])
    monkeypatch.setattr(svc, "search_notes", search)
    assert asyncio.run(nodes._analyst_note_documents(cfg, {"aapl", "msft"}, "q")) == ["doc"]
    assert search.await_args.args == ("7", "q", ["AAPL", "MSFT"])


# ---- hybrid_search stays backward compatible ---------------------------------

def _bare_db():
    from rag.vectordb.client import load_vector_database
    db = object.__new__(load_vector_database)
    db.embeddings = SimpleNamespace(aembed_query=AsyncMock(return_value=[0.1, 0.2]))
    db.sparse_model = None
    db.collection_name = svc.NOTES_COLLECTION
    db.async_qdrant_client = SimpleNamespace(query_points=AsyncMock(return_value=SimpleNamespace(points=[])))
    return db


def test_hybrid_search_without_extra_conditions_is_unchanged():
    db = _bare_db()
    asyncio.run(db.hybrid_search("q"))
    assert db.async_qdrant_client.query_points.await_args.kwargs["query_filter"] is None
    asyncio.run(db.hybrid_search("q", content_type="text", years=[2024]))
    flt = db.async_qdrant_client.query_points.await_args.kwargs["query_filter"]
    assert [c.key for c in flt.must] == ["metadata.content_type", "metadata.year"]


def test_hybrid_search_ands_extra_conditions_onto_existing_ones():
    db = _bare_db()
    extra = svc.read_filter("u1", ["AAPL"])
    asyncio.run(db.hybrid_search("q", content_type="text", extra_conditions=[extra]))
    flt = db.async_qdrant_client.query_points.await_args.kwargs["query_filter"]
    assert flt.must[0].key == "metadata.content_type" and flt.must[1] is extra


# ---- /notes API --------------------------------------------------------------

@pytest.fixture
def api(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _db():
        async with maker() as s:
            yield s

    who = {"user": SimpleNamespace(id=1, username="ana", full_name="Ana Analyst", role="analyst")}
    calls = SimpleNamespace(index=AsyncMock(side_effect=lambda note, previous_chunk_count=0: 2),
                            delete=AsyncMock(), visibility=AsyncMock(), active=AsyncMock())
    monkeypatch.setattr(svc, "index_note", calls.index)
    monkeypatch.setattr(svc, "delete_note_points", calls.delete)
    monkeypatch.setattr(svc, "set_points_visibility", calls.visibility)
    monkeypatch.setattr(svc, "set_points_active", calls.active)

    app = FastAPI()
    app.include_router(notes_api.router)
    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_current_user] = lambda: who["user"]
    return SimpleNamespace(client=TestClient(app), calls=calls, as_user=lambda uid, name="x", role="analyst": who.update(
        user=SimpleNamespace(id=uid, username=name, full_name=None, role=role)), maker=maker)


def _create_note(api, **over):
    body = {"ticker": "aapl", "title": "Q3 call", "content": "PT $215 (was 200)", "note_date": "2026-04-06"}
    return api.client.post("/notes", json={**body, **over})


def test_create_sets_owner_from_token_and_normalizes(api):
    r = _create_note(api, owner_id="999", author="Mallory")  # unknown/forged fields are ignored
    assert r.status_code == 201, r.text
    n = r.json()
    assert (n["ticker"], n["visibility"], n["author"], n["is_owner"], n["chunk_count"]) == ("AAPL", "private", "Ana Analyst", True, 2)
    saved = api.calls.index.await_args.args[0]
    assert saved.owner_id == "1" and saved.author == "Ana Analyst"


def test_create_validation(api):
    assert _create_note(api, ticker="not a ticker").status_code == 422
    assert _create_note(api, content="   ").status_code == 422
    assert _create_note(api, title="").status_code == 422
    assert _create_note(api, visibility="public").status_code == 422
    assert _create_note(api, content="x" * (svc.MAX_CONTENT_CHARS + 1)).status_code == 422
    assert api.calls.index.await_count == 0


def test_create_defaults_date_to_today(api):
    r = api.client.post("/notes", json={"ticker": "MSFT", "title": "t", "content": "c"})
    assert r.status_code == 201 and r.json()["note_date"] == date.today().isoformat()


def test_index_failure_saves_nothing_and_cleans_up(api):
    api.calls.index.side_effect = RuntimeError("openai down")
    assert _create_note(api).status_code == 502
    assert api.client.get("/notes").json() == []
    api.calls.delete.assert_awaited_once()


def test_list_shows_own_plus_others_shared_never_others_private(api):
    _create_note(api, title="mine private")
    _create_note(api, title="mine shared", visibility="shared")
    api.as_user(2, "bob")
    _create_note(api, title="bob private")
    _create_note(api, title="bob shared", visibility="shared")
    titles = {n["title"]: n["is_owner"] for n in api.client.get("/notes").json()}
    assert titles == {"mine shared": False, "bob private": True, "bob shared": True}
    api.as_user(3, "carol")
    assert {n["title"] for n in api.client.get("/notes").json()} == {"mine shared", "bob shared"}


def test_list_ticker_filter(api):
    _create_note(api, ticker="AAPL")
    _create_note(api, ticker="MSFT", title="m")
    assert [n["ticker"] for n in api.client.get("/notes?ticker=msft").json()] == ["MSFT"]


def test_get_private_note_is_404_for_others_and_shared_is_readable(api):
    private = _create_note(api).json()["note_id"]
    shared = _create_note(api, visibility="shared", title="s").json()["note_id"]
    api.as_user(2, "bob")
    assert api.client.get(f"/notes/{private}").status_code == 404
    r = api.client.get(f"/notes/{shared}")
    assert r.status_code == 200 and r.json()["is_owner"] is False
    assert api.client.get("/notes/does-not-exist").status_code == 404


def test_only_owner_can_edit_or_delete_even_when_shared(api):
    nid = _create_note(api, visibility="shared").json()["note_id"]
    api.as_user(2, "bob")
    assert api.client.patch(f"/notes/{nid}", json={"visibility": "private"}).status_code == 404
    assert api.client.patch(f"/notes/{nid}", json={"content": "hijack"}).status_code == 404
    assert api.client.delete(f"/notes/{nid}").status_code == 404
    api.calls.delete.assert_not_awaited()
    api.as_user(1)
    assert api.client.get(f"/notes/{nid}").json()["content"] == "PT $215 (was 200)"


def test_visibility_flip_updates_chunks_without_reindexing(api):
    nid = _create_note(api).json()["note_id"]
    api.calls.index.reset_mock()
    r = api.client.patch(f"/notes/{nid}", json={"visibility": "shared"})
    assert r.status_code == 200 and r.json()["visibility"] == "shared"
    api.calls.visibility.assert_awaited_once_with(nid, "shared")
    api.calls.index.assert_not_awaited()


def test_content_edit_reindexes_with_previous_chunk_count(api):
    nid = _create_note(api).json()["note_id"]
    api.calls.index.reset_mock()
    r = api.client.patch(f"/notes/{nid}", json={"content": "new text", "title": "renamed"})
    assert r.status_code == 200 and r.json()["title"] == "renamed"
    assert api.calls.index.await_args.kwargs == {"previous_chunk_count": 2}


def test_noop_patch_touches_nothing(api):
    nid = _create_note(api).json()["note_id"]
    api.calls.index.reset_mock()
    assert api.client.patch(f"/notes/{nid}", json={"title": "Q3 call", "visibility": "private"}).status_code == 200
    api.calls.index.assert_not_awaited()
    api.calls.visibility.assert_not_awaited()


def test_failed_update_rolls_back(api):
    nid = _create_note(api).json()["note_id"]
    api.calls.index.side_effect = RuntimeError("openai down")
    assert api.client.patch(f"/notes/{nid}", json={"content": "changed"}).status_code == 502
    assert api.client.get(f"/notes/{nid}").json()["content"] == "PT $215 (was 200)"


def test_delete_removes_chunks_then_row_and_keeps_row_on_failure(api):
    nid = _create_note(api).json()["note_id"]
    api.calls.delete.side_effect = RuntimeError("qdrant down")
    assert api.client.delete(f"/notes/{nid}").status_code == 502
    assert api.client.get(f"/notes/{nid}").status_code == 200  # still there, can retry
    api.calls.delete.side_effect = None
    assert api.client.delete(f"/notes/{nid}").status_code == 204
    assert api.client.get(f"/notes/{nid}").status_code == 404
    api.calls.delete.assert_awaited_with(nid)


# ---- write path + hybrid search, end to end against an in-memory Qdrant -------

def test_index_visibility_flip_reindex_and_delete_end_to_end(monkeypatch):
    from qdrant_client import AsyncQdrantClient

    async def run():
        aclient = AsyncQdrantClient(":memory:")
        await aclient.create_collection(svc.NOTES_COLLECTION, vectors_config={"dense": models.VectorParams(size=3, distance=models.Distance.COSINE)})
        db = _bare_db()
        db.embeddings = SimpleNamespace(aembed_query=AsyncMock(return_value=[1.0, 0.0, 0.0]))
        db.async_qdrant_client = aclient
        db.generate_embeddings_for_ingestion = AsyncMock(
            side_effect=lambda texts, label="": {"dense": [[1.0, 0.0, 0.0]] * len(texts), "sparse": [None] * len(texts)})
        monkeypatch.setattr(svc, "_get_db", lambda: db)
        monkeypatch.setattr(svc, "_collection_ready", True)

        async def count():
            return (await aclient.count(svc.NOTES_COLLECTION, exact=True)).count

        note = AnalystNote(note_id="n1", owner_id="1", author="Ana", ticker="AAPL", title="Call", note_date=date(2026, 4, 6),
                           visibility="private", content="\n\n".join(f"para{i} " + "x" * 600 for i in range(3)))
        assert await svc.index_note(note) == 3 and await count() == 3

        # owner finds it (nested owner/ticker filter inside real hybrid_search); a stranger does not
        mine = await svc.search_notes("1", "q", ["aapl"], limit=5)
        assert len(mine) == 3 and mine[0].metadata["owner_id"] == "1" and mine[0].metadata["source_type"] == "analyst_note"
        assert await svc.search_notes("2", "q", ["AAPL"]) == []
        assert await svc.search_notes("1", "q", ["MSFT"]) == []  # wrong ticker

        # sharing alone does not put it into the stranger's analysis: they must opt in too
        await svc.set_points_visibility("n1", "shared")
        assert await svc.search_notes("2", "q", ["AAPL"], limit=5) == []
        svc.included_note_ids.return_value = ["n1"]
        assert len(await svc.search_notes("2", "q", ["AAPL"], limit=5)) == 3
        db.generate_embeddings_for_ingestion.assert_awaited_once()  # none of this re-embedded anything

        # the owner pausing the note removes it from her own AND the opted-in reader's analysis, without deleting it
        await svc.set_points_active("n1", False)
        assert await svc.search_notes("1", "q", ["AAPL"]) == [] and await svc.search_notes("2", "q", ["AAPL"]) == []
        assert await count() == 3
        await svc.set_points_active("n1", True)
        assert len(await svc.search_notes("1", "q", ["AAPL"], limit=5)) == 3

        # unsharing hides it from the reader even though their opt-in row still said "included"
        await svc.set_points_visibility("n1", "private")
        assert await svc.search_notes("2", "q", ["AAPL"]) == []
        svc.included_note_ids.return_value = []

        # shrinking an edited note removes the stale trailing chunks
        note.content = "just one paragraph"
        assert await svc.index_note(note, previous_chunk_count=3) == 1 and await count() == 1

        # other notes are untouched by deleting this one
        other = AnalystNote(note_id="n2", owner_id="2", author="Bob", ticker="AAPL", title="B", note_date=date(2026, 4, 7),
                            visibility="private", content="bob note")
        await svc.index_note(other)
        await svc.delete_note_points("n1")
        assert await count() == 1 and len(await svc.search_notes("2", "q", ["AAPL"])) == 1

    asyncio.run(run())


# ---- include / hide controls ---------------------------------------------------

def _shared_note(api, **over):
    """A shared note written by user 1; leaves the API acting as user 1."""
    api.as_user(1, "ana")
    return _create_note(api, visibility="shared", **over).json()["note_id"]


def test_new_notes_are_active_and_flags_describe_the_caller(api):
    nid = _create_note(api).json()["note_id"]
    (own,) = api.client.get("/notes").json()
    assert own["active"] is True and own["used_in_analysis"] is True and own["included"] is False


def test_others_shared_notes_start_excluded_until_the_reader_opts_in(api):
    nid = _shared_note(api)
    api.as_user(2, "bob")
    (note,) = api.client.get("/notes").json()
    assert note["note_id"] == nid and note["is_owner"] is False
    assert note["included"] is False and note["used_in_analysis"] is False  # visible, but NOT in bob's analysis

    r = api.client.put(f"/notes/{nid}/inclusion", json={"included": True})
    assert r.status_code == 200 and r.json()["included"] is True and r.json()["used_in_analysis"] is True
    assert api.client.get(f"/notes/{nid}").json()["included"] is True
    assert api.client.put(f"/notes/{nid}/inclusion", json={"included": True}).status_code == 200  # idempotent

    r = api.client.put(f"/notes/{nid}/inclusion", json={"included": False})
    assert r.json()["included"] is False and r.json()["used_in_analysis"] is False
    assert api.client.put(f"/notes/{nid}/inclusion", json={"included": False}).status_code == 200


def test_inclusion_is_per_user(api):
    nid = _shared_note(api)
    api.as_user(2, "bob")
    api.client.put(f"/notes/{nid}/inclusion", json={"included": True})
    api.as_user(3, "carol")
    assert api.client.get("/notes").json()[0]["included"] is False


def test_inclusion_rules(api):
    nid = _shared_note(api)
    assert api.client.put(f"/notes/{nid}/inclusion", json={"included": True}).status_code == 400  # own note: use Hide/Show
    private = _create_note(api, title="mine").json()["note_id"]
    api.as_user(2, "bob")
    assert api.client.put(f"/notes/{private}/inclusion", json={"included": True}).status_code == 404  # not readable
    assert api.client.put("/notes/nope/inclusion", json={"included": True}).status_code == 404
    assert api.client.put(f"/notes/{nid}/inclusion", json={}).status_code == 422


def test_owner_can_pause_and_resume_without_reindexing(api):
    nid = _create_note(api).json()["note_id"]
    api.calls.index.reset_mock()
    r = api.client.patch(f"/notes/{nid}", json={"active": False})
    assert r.status_code == 200 and r.json()["active"] is False and r.json()["used_in_analysis"] is False
    api.calls.active.assert_awaited_once_with(nid, False)
    api.calls.index.assert_not_awaited()
    assert api.client.patch(f"/notes/{nid}", json={"active": False}).status_code == 200
    assert api.calls.active.await_count == 1  # unchanged value: nothing written
    assert api.client.patch(f"/notes/{nid}", json={"active": True}).json()["used_in_analysis"] is True
    assert api.calls.active.await_count == 2


def test_paused_shared_note_disappears_for_others_and_returns_on_resume(api):
    nid = _shared_note(api)
    api.client.patch(f"/notes/{nid}", json={"active": False})
    assert api.client.get("/notes").json()[0]["active"] is False  # the owner still sees it, marked paused
    api.as_user(2, "bob")
    assert api.client.get("/notes").json() == []
    assert api.client.get(f"/notes/{nid}").status_code == 404
    assert api.client.put(f"/notes/{nid}/inclusion", json={"included": True}).status_code == 404
    api.as_user(1, "ana")
    api.client.patch(f"/notes/{nid}", json={"active": True})
    api.as_user(2, "bob")
    assert [n["note_id"] for n in api.client.get("/notes").json()] == [nid]


def test_only_the_owner_can_pause(api):
    nid = _shared_note(api)
    api.as_user(2, "bob")
    assert api.client.patch(f"/notes/{nid}", json={"active": False}).status_code == 404
    api.calls.active.assert_not_awaited()


def test_unsharing_withdraws_opt_ins_so_resharing_needs_fresh_consent(api):
    nid = _shared_note(api)
    api.as_user(2, "bob")
    api.client.put(f"/notes/{nid}/inclusion", json={"included": True})
    api.as_user(1, "ana")
    api.client.patch(f"/notes/{nid}", json={"visibility": "private"})
    api.client.patch(f"/notes/{nid}", json={"visibility": "shared"})
    api.as_user(2, "bob")
    assert api.client.get(f"/notes/{nid}").json()["included"] is False


def test_edit_that_reindexes_keeps_the_pause_flag(api):
    nid = _create_note(api).json()["note_id"]
    api.client.patch(f"/notes/{nid}", json={"active": False})
    api.client.patch(f"/notes/{nid}", json={"content": "new text"})
    assert api.calls.index.await_args.args[0].active is False


def test_deleting_a_note_removes_everyones_opt_in(api):
    import asyncio as aio
    from sqlalchemy import select
    nid = _shared_note(api)
    api.as_user(2, "bob")
    api.client.put(f"/notes/{nid}/inclusion", json={"included": True})
    api.as_user(1, "ana")
    assert api.client.delete(f"/notes/{nid}").status_code == 204

    async def rows():
        async with api.maker() as s:
            return list((await s.execute(select(AnalystNoteInclusion))).scalars())
    assert aio.run(rows()) == []


def test_chunk_payload_carries_the_active_flag():
    note = AnalystNote(note_id="n", owner_id="1", author="A", ticker="AAPL", title="t", note_date=date(2026, 1, 1),
                       visibility="private", content="c")
    assert svc.chunk_metadata(note, 0, 1)["active"] is True  # unset (None) counts as active
    note.active = False
    assert svc.chunk_metadata(note, 0, 1)["active"] is False
