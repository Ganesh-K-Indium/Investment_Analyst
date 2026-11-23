"""The Reading engines screen's API: what is installed, trying an engine, admin-only model downloads."""
import asyncio

import pytest

pytest.importorskip("aiosqlite")
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import reading_engines as api
from app.auth.deps import get_current_user
from app.database.connection import get_db_session
from app.database.models import Base, ProviderCredential, SettingsRecord, User
from app.docproc.engines.ocr import local, prepare
from app.docproc.services.reading import tess


@pytest.fixture
def client(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/r.db")

    async def setup():
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(sc, tables=[SettingsRecord.__table__, ProviderCredential.__table__, User.__table__]))
    asyncio.run(setup())
    Session = async_sessionmaker(eng, expire_on_commit=False)
    app = FastAPI()
    app.include_router(api.router)

    async def db():
        async with Session() as s:
            yield s
    me = {"u": User(id=7, email="a@x", username="a", role="analyst", hashed_password="x", is_active=True, token_version=0)}
    app.dependency_overrides[get_db_session] = db
    app.dependency_overrides[get_current_user] = lambda: me["u"]
    c = TestClient(app)
    c.me = me
    return c


def test_overview_lists_engines_strategies_and_the_selected_one(client):
    r = client.get("/reading/engines").json()
    ids = [e["id"] for e in r["local_engines"]]
    assert {"tesseract", "rapidocr", "paddleocr", "doctr"} <= set(ids)
    assert r["selected_engine"] == "tesseract" and {s["id"] for s in r["strategies"]} == {"economy", "balanced", "accuracy"}
    assert r["can_prepare"] is False and r["plan"]["strategy"] == "balanced"                      # only an administrator can download models
    for e in r["local_engines"]:
        assert e["status"] in ("ready", "not_installed") and (e["ready"] or e["hint"])            # a missing engine says how to install it


def test_model_downloads_are_admin_only_and_refuse_unknown_engines(client, monkeypatch):
    assert client.post("/reading/engines/tesseract/prepare").status_code == 403
    client.me["u"] = User(id=1, email="adm@x", username="adm", role="admin", hashed_password="x", is_active=True, token_version=0)
    assert client.get("/reading/engines").json()["can_prepare"] is True
    assert client.post("/reading/engines/nope/prepare").status_code == 404
    assert client.post("/reading/engines/tesseract/prepare").status_code == 409                   # Tesseract has no models to download


def test_a_local_engine_can_be_tried_on_the_sample_page(client):
    if not tess.available():
        pytest.skip("Tesseract is not installed here")
    r = client.post("/reading/engines/test", json={"kind": "local", "id": "tesseract"}).json()
    assert r["ok"] and r["score"] >= 0.75 and r["tokens"] == 0 and r["seconds"] >= 0
    assert client.post("/reading/engines/test", json={"kind": "local", "id": "nope"}).status_code == 404
    assert client.post("/reading/engines/test", json={"kind": "model", "id": "anthropic:"}).status_code == 422
    assert client.post("/reading/engines/test", json={"kind": "wat", "id": "x"}).status_code == 422


def test_a_missing_engine_reports_how_to_install_instead_of_failing(client, monkeypatch):
    class Gone(local.Engine):
        id, label, install = "ghost", "Ghost OCR", "pip install ghost-ocr"
        def available(self): return False
    monkeypatch.setitem(local._ENGINES, "ghost", Gone())
    r = client.post("/reading/engines/test", json={"kind": "local", "id": "ghost"}).json()
    assert r["ok"] is False and "pip install ghost-ocr" in r["error"]


def test_the_chosen_engine_reaches_the_reading_plan(client):
    from app.services.app_settings import resolver
    s = resolver.resolve({"documents": {"local_engine": "rapidocr"}})
    assert api._plan_for(s).local_engine in ("rapidocr", "tesseract", None)                      # falls back, with a note, when it is not installed
    assert any("rapidocr" in n for n in api._plan_for(s).notes) or api._plan_for(s).local_engine == "rapidocr"
