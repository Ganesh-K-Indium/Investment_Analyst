"""Market-data providers users connect: request building, provider error handling, the /data-sources API, and the agent's tools."""
import asyncio
import json

import httpx
import pytest

pytest.importorskip("aiosqlite")
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import data_sources as api
from app.auth.deps import get_current_user
from app.database.connection import get_db_session
from app.database.models import Base, User, UserDataSource
from app.services import data_sources as svc, user_mcp


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-for-fernet")
    monkeypatch.delenv("MCP_ALLOW_PRIVATE_URLS", raising=False)


def test_every_catalog_operation_builds_a_valid_request():
    for pid, spec in svc.CATALOG.items():
        for op, o in spec["ops"].items():
            args = {"symbol": "AAPL", "path": "/v1/x"} if pid == "custom" else {"symbol": "AAPL", "query": "Apple"}
            url, params, headers = svc.build_request(pid, op, args, {"value": "K", "name": "X-Key", "in": "header"}, "https://api.example.com")
            assert url.startswith("http") and "$" not in url and all("$" not in str(v) for v in params.values()), (pid, op, url, params)
            assert "K" in list(params.values()) + list(headers.values())              # the key is sent, in the provider's own way
    url, params, headers = svc.build_request("finnhub", "quote", {"symbol": "AAPL"}, {"value": "K"}, None)
    assert headers["X-Finnhub-Token"] == "K" and "token" not in params
    with pytest.raises(svc.SourceError, match="needs 'symbol'"):
        svc.build_request("fmp", "quote", {}, {"value": "K"}, None)
    with pytest.raises(svc.SourceError, match="no operation"):
        svc.build_request("fmp", "teleport", {"symbol": "A"}, {"value": "K"}, None)
    with pytest.raises(svc.SourceError, match="ticker"):
        svc.build_request("fmp", "quote", {"symbol": "A; DROP"}, {"value": "K"}, None)
    with pytest.raises(svc.SourceError, match="JSON object"):
        svc.build_request("custom", "get", {"path": "/x", "query": "not json"}, {"value": "K"}, "https://api.example.com")
    for bad in ("v1/x", "/../etc", "http://evil/x", "//evil/x", "/a@b"):
        with pytest.raises(svc.SourceError):
            svc.build_request("custom", "get", {"path": bad}, {"value": "K"}, "https://api.example.com")


def _mock(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(svc.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_run_handles_data_errors_and_trims(monkeypatch):
    series = {"Time Series (Daily)": {f"2026-{m:02d}-{d:02d}": {"4. close": "1"} for m in range(1, 7) for d in range(1, 29)}}
    answers = iter([httpx.Response(200, json={"Global Quote": {"05. price": "9"}}), httpx.Response(200, json={"Information": "premium endpoint"}),
                    httpx.Response(200, json={"Error Message": "Invalid API call"}), httpx.Response(401), httpx.Response(429), httpx.Response(200, json=series)])
    _mock(monkeypatch, lambda req: next(answers))
    run = lambda op="quote": asyncio.run(svc.run("alpha_vantage", op, {"symbol": "IBM"}, {"value": "K"}))
    assert "05. price" in run()
    for msg in ("premium endpoint", "Invalid API call", "rejected the key", "rate limit"):
        with pytest.raises(svc.SourceError, match=msg):
            run()
    out = run("history")
    assert len(json.loads(out)["Time Series (Daily)"]) == 60                              # newest 60 days, not the whole series


@pytest.fixture
def world(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/d.db")

    async def setup():
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(sc, tables=[UserDataSource.__table__, User.__table__]))
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
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.params.get("apikey") == "bad" or req.headers.get("x-finnhub-token") == "bad":
            return httpx.Response(401)
        return httpx.Response(200, json={"price": 1})
    _mock(monkeypatch, handler)
    return TestClient(app), me, Session, seen


def test_api_connect_test_and_isolation(world):
    c, me, _, _ = world
    cat = c.get("/data-sources").json()["providers"]
    assert {p["id"] for p in cat} >= {"alpha_vantage", "polygon", "finnhub", "twelve_data", "fmp", "custom"}
    assert c.post("/data-sources", json={"provider": "fmp", "name": "F", "api_key": "bad"}).status_code == 422          # not saved: the key does not work
    assert c.get("/data-sources").json()["sources"] == []
    r = c.post("/data-sources", json={"provider": "fmp", "name": "My FMP", "api_key": "good-key"})
    assert r.status_code == 201 and r.json()["has_key"] and "good-key" not in r.text
    assert c.post("/data-sources", json={"provider": "fmp", "name": "my fmp", "api_key": "good-key"}).status_code == 409
    assert c.post("/data-sources", json={"provider": "custom", "name": "Gw", "base_url": "http://127.0.0.1:9/x", "api_key": "k"}).status_code == 422   # private address
    sid = r.json()["id"]
    assert c.patch(f"/data-sources/{sid}", json={"enabled": False}).json()["enabled"] is False
    assert c.post(f"/data-sources/{sid}/test").json()["last_error"] is None
    me["u"] = User(id=8, email="b@x", username="b", role="analyst", hashed_password="x", is_active=True, token_version=0)
    assert c.get("/data-sources").json()["sources"] == [] and c.delete(f"/data-sources/{sid}").status_code == 404


def test_agent_tools_use_the_users_key_server_side(world, monkeypatch):
    c, _, Session, seen = world
    from app.database import connection
    monkeypatch.setattr(connection, "SessionLocal", Session)
    assert c.post("/data-sources", json={"provider": "finnhub", "name": "Fin", "api_key": "secret-1", "description": "my feed"}).status_code == 201
    from quant.stock_agent.stock_exchange_agent.subagents.data_sources_agent import langgraph_agent as ag
    cfg = lambda uid: {"configurable": {"user_id": uid}}

    async def go():
        listing = await ag.list_data_sources.ainvoke({}, config=cfg(7))
        assert "Fin" in listing and "secret-1" not in listing                              # the model never sees the key
        assert "not connected any" in await ag.list_data_sources.ainvoke({}, config=cfg(9))
        out = await ag.query_data_source.ainvoke({"source": "fin", "operation": "quote", "arguments_json": '{"symbol":"AAPL"}'}, config=cfg(7))
        assert "<result" in out and seen[-1].headers["x-finnhub-token"] == "secret-1"
        assert "no enabled data source" in await ag.query_data_source.ainvoke({"source": "Fin", "operation": "quote", "arguments_json": '{"symbol":"AAPL"}'}, config=cfg(9))
        assert "no operation" in await ag.query_data_source.ainvoke({"source": "Fin", "operation": "nope"}, config=cfg(7))
    asyncio.run(go())
