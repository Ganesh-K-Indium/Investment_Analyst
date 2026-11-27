"""HTTP-level checks of the settings and user-document routers on a throwaway app (sqlite, no Qdrant, no queue)."""
import asyncio
import io

import pytest

pytest.importorskip("aiosqlite")
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import settings as settings_api, user_documents as docs_api
from app.auth.deps import get_current_user
from app.database.connection import get_db_session
from app.database.models import Base, Portfolio, SettingsRecord, UsageEvent, User, UserDocument
from app.services.user_docs import manager


class Fake(User):
    pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "ROOT", tmp_path / "docs")
    store_calls = []

    async def noop(*a, **k):
        store_calls.append(a)
    monkeypatch.setattr(manager.store, "delete_document", noop)
    monkeypatch.setattr(manager.store, "set_metadata", noop)
    monkeypatch.setattr(manager, "start", lambda *a, **k: None)             # ingestion runs in the background in real life

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/api.db")

    async def setup():
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(sc, tables=[SettingsRecord.__table__, UsageEvent.__table__, UserDocument.__table__, Portfolio.__table__, User.__table__]))
    asyncio.run(setup())
    Session = async_sessionmaker(eng, expire_on_commit=False)

    app = FastAPI()
    app.include_router(settings_api.router)
    app.include_router(docs_api.router)

    async def db():
        async with Session() as s:
            yield s
    me = {"u": User(id=7, email="a@x", username="a", role="analyst", hashed_password="x", is_active=True, token_version=0)}
    app.dependency_overrides[get_db_session] = db
    app.dependency_overrides[get_current_user] = lambda: me["u"]
    c = TestClient(app)
    c.me, c.Session, c.calls = me, Session, store_calls
    return c


def test_schema_get_put_validate_reset(client):
    sch = client.get("/settings/schema").json()
    assert {s["id"] for s in sch["sections"]} >= {"models", "retrieval", "answers", "modules", "prompts", "documents", "quant", "ui"}
    models = {f["key"]: f for sec in sch["sections"] for f in sec["fields"]}
    assert models["generator_model"]["type"] == "model" and "options" not in models["generator_model"] and models["default_provider"]["options"][:2] == ["openai", "anthropic"]
    r = client.get("/settings").json()
    assert r["saved"] == {} and r["effective"]["retrieval"]["top_k"] == 12
    r = client.put("/settings", json={"retrieval": {"top_k": 20}, "models": {"generator_model": "gpt-4.1"}}).json()
    assert r["saved"]["retrieval"]["top_k"] == 20 and r["effective"]["models"]["generator_model"] == "gpt-4.1" and r["effective"]["retrieval"]["rerank_enabled"] is True
    bad = client.put("/settings", json={"retrieval": {"top_k": 9999}})
    assert bad.status_code == 422 and "top_k" in bad.json()["detail"]
    assert client.put("/settings", json={"models": {"generator_model": "anthropic:"}}).status_code == 422          # a provider needs a model
    assert client.put("/settings", json={"models": {"generator_model": "anthropic:claude-sonnet-5-5"}}).status_code == 200
    assert client.put("/settings", json={"nope": {}}).status_code == 422
    assert client.delete("/settings?section=retrieval").json()["effective"]["retrieval"]["top_k"] == 12
    assert client.delete("/settings?section=bogus").status_code == 422
    assert client.delete("/settings").json()["saved"] == {}


def test_frameworks_prompts_usage_and_portfolio_scope(client):
    fw = client.get("/settings/frameworks/template").json()
    assert client.put("/settings", json={"modules": {"custom": [fw]}}).status_code == 200
    assert client.put("/settings", json={"modules": {"custom": [fw, fw]}}).status_code == 422
    assert {p["name"] for p in client.get("/settings/prompts").json()["prompts"]} == {"generator.system", "macro.planner", "macro.synthesis"}
    u = client.get("/settings/usage").json()
    assert u["tokens"] == 0 and u["over_budget"] is False

    async def mk():
        async with client.Session() as s:
            s.add(Portfolio(id=1, user_id="7", name="P", company_names=["Apple"]))
            s.add(Portfolio(id=2, user_id="99", name="Theirs", company_names=["X"]))
            await s.commit()
    asyncio.run(mk())
    client.put("/settings", json={"retrieval": {"top_k": 10}})
    r = client.put("/settings/portfolio/1", json={"retrieval": {"top_k": 30}}).json()
    assert r["effective"]["retrieval"]["top_k"] == 30 and client.get("/settings").json()["effective"]["retrieval"]["top_k"] == 10
    assert client.get("/settings?portfolio_id=1").json()["effective"]["retrieval"]["top_k"] == 30
    assert client.get("/settings/portfolio/2").status_code == 404                      # someone else's portfolio: its existence is not revealed
    assert client.get("/settings/system").status_code == 403                           # admin only


def test_admin_can_narrow_allowed_models(client):
    client.me["u"] = User(id=1, email="adm@x", username="adm", role="admin", hashed_password="x", is_active=True, token_version=0)
    client.put("/settings/system", json={"models": {"allowed_models": ["gpt-4o-mini"], "generator_model": "gpt-4o-mini"}})
    gen = next(f for sec in client.get("/settings/schema").json()["sections"] for f in sec["fields"] if f["key"] == "generator_model")
    assert gen["options"] == ["gpt-4o-mini"]
    assert client.put("/settings", json={"models": {"generator_model": "gpt-4o"}}).status_code == 422
    eff = client.get("/settings").json()["effective"]["models"]
    assert eff["grader_model"] == "gpt-4o-mini"                                         # a default the admin removed falls back to an allowed model


def test_documents_upload_list_review_content_delete(client):
    csv = b"Line item,2025,2024\nRevenue,100,90\n"
    r = client.post("/user-documents", files=[("files", ("pnl.csv", io.BytesIO(csv), "text/csv")), ("files", ("bad.exe", io.BytesIO(b"MZ"), "application/octet-stream"))],
                    data={"entity": "acme", "tags": "q3, model"})
    assert r.status_code == 202
    ok, bad = r.json()["results"]
    assert ok["ok"] and ok["document"]["status"] == "queued" and ok["document"]["entity"] == "ACME" and ok["document"]["tags"] == ["q3", "model"]
    assert not bad["ok"] and "not supported" in bad["error"]
    doc_id = ok["document"]["id"]
    dup = client.post("/user-documents", files=[("files", ("again.csv", io.BytesIO(csv), "text/csv"))]).json()["results"][0]
    assert not dup["ok"] and dup["duplicate_of"] == doc_id

    # run the ingestion the background job would run (inline here), then look at the results through the API
    async def ingest():
        async with client.Session() as s:
            d = await s.get(UserDocument, doc_id)
            from app.services.app_settings import resolver
            await manager._ingest(s, d, resolver.resolve(), ["ACME"], [])
    from app.services.user_docs import chunks as ch
    async def fake_index(cs):
        return len(cs)
    async def fake_delete_page(*a): return None
    manager.store.index_chunks, manager.store.delete_page = fake_index, fake_delete_page
    asyncio.run(ingest())

    d = client.get(f"/user-documents/{doc_id}").json()
    assert d["status"] == "awaiting_approval" and d["pages_indexed"] == 0 and d["approved"] is False       # held until the owner approves
    assert client.post(f"/user-documents/{doc_id}/approve").json()["status"] == "ready"
    d = client.get(f"/user-documents/{doc_id}").json()
    assert d["status"] == "ready" and d["pages"] == 1 and d["pages_indexed"] == 1 and d["doc_type"] == "model" and d["review_pending"] == 0
    assert client.get("/user-documents?status=ready").json()["documents"][0]["id"] == doc_id
    assert client.get("/user-documents?q=pnl").json()["documents"] and not client.get("/user-documents?q=zzz").json()["documents"]
    c = client.get(f"/user-documents/{doc_id}/content").json()
    assert c["pages"][0]["blocks"][1]["columns"] == ["Line item", "2025", "2024"]
    assert client.get(f"/user-documents/{doc_id}/review").json()["items"] == []
    assert client.get(f"/user-documents/{doc_id}/file").content == csv
    assert client.get(f"/user-documents/{doc_id}/pdf").status_code == 404                 # read natively: no PDF view
    p = client.patch(f"/user-documents/{doc_id}", json={"visibility": "workspace", "entity": "msft", "doc_type": "memo"}).json()
    assert p["visibility"] == "workspace" and p["entity"] == "MSFT" and client.patch(f"/user-documents/{doc_id}", json={"doc_type": "nope"}).status_code == 422
    assert client.post(f"/user-documents/{doc_id}/review", json={"edits": {"p1:l0": "x"}}).status_code == 422

    # another user: sees it only because it is shared, cannot change or delete it, and cannot see it once it is private again
    client.me["u"] = User(id=8, email="b@x", username="b", role="analyst", hashed_password="x", is_active=True, token_version=0)
    assert client.get(f"/user-documents/{doc_id}").json()["owner"] is False
    assert client.patch(f"/user-documents/{doc_id}", json={"label": "hijack"}).status_code == 404
    assert client.delete(f"/user-documents/{doc_id}").status_code == 404
    assert client.get(f"/user-documents/{doc_id}/review").status_code == 404
    client.me["u"] = User(id=7, email="a@x", username="a", role="analyst", hashed_password="x", is_active=True, token_version=0)
    client.patch(f"/user-documents/{doc_id}", json={"visibility": "private"})
    client.me["u"] = User(id=8, email="b@x", username="b", role="analyst", hashed_password="x", is_active=True, token_version=0)
    assert client.get(f"/user-documents/{doc_id}").status_code == 404
    client.me["u"] = User(id=7, email="a@x", username="a", role="analyst", hashed_password="x", is_active=True, token_version=0)
    assert client.delete(f"/user-documents/{doc_id}").status_code == 204 and client.get(f"/user-documents/{doc_id}").status_code == 404
    assert not (manager.ROOT / doc_id).exists()
