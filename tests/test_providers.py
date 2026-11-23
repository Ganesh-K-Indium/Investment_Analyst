"""Bring-your-own LLM providers: catalog, credentials, factory, preflight, URL guard, and the /providers API."""
import asyncio

import pytest

pytest.importorskip("aiosqlite")
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import providers as providers_api
from app.auth.deps import get_current_user
from app.database.connection import get_db_session
from app.database.models import Base, ProviderCredential, User
from app.services import app_settings, llm_providers as L
from app.services.app_settings import resolver
from app.services.llm_providers import context, factory, service, urls


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OLLAMA_BASE_URL", "LLM_CUSTOM_BASE_URL", "LLM_CUSTOM_API_KEY", "LOCAL_ONLY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-for-fernet")


def test_spec_parsing_and_defaults():
    assert L.split_spec("gpt-4o") == ("openai", "gpt-4o") and L.split_spec("anthropic:claude-haiku-5-5") == ("anthropic", "claude-haiku-5-5")
    assert L.split_spec("llama3.1:8b") == ("openai", "llama3.1:8b")                          # an Ollama tag is not a provider
    assert L.split_spec("ollama:llama3.1:8b") == ("ollama", "llama3.1:8b")
    assert not L.valid_spec("anthropic:") and not L.valid_spec("a b") and not L.valid_spec("") and L.valid_spec("custom:org/model-1")
    assert L.defaults_for("gemini")["strong"] == "gemini:gemini-2.5-pro" and L.defaults_for("openai")["light"] == "gpt-4o-mini" and L.defaults_for("ollama") is None


def test_factory_builds_each_provider_and_refuses_without_credentials(monkeypatch):
    with pytest.raises(factory.ProviderNotConfigured, match="Anthropic Claude is not connected"):
        factory.build("anthropic:claude-sonnet-5-5")
    creds = {"OPENAI_API_KEY": "sk-openai-test-1234", "ANTHROPIC_API_KEY": "sk-ant-test-1234", "GEMINI_API_KEY": "gem-test-key-1234",
             "OLLAMA_BASE_URL": "http://localhost:11434", "LLM_CUSTOM_BASE_URL": "http://localhost:1234/v1"}
    with context.use(creds):
        kinds = {spec: type(factory.build(spec)).__name__ for spec in ("gpt-4o", "anthropic:claude-sonnet-5-5", "gemini:gemini-2.5-flash", "ollama:llama3.1:8b", "custom:my-model")}
        assert kinds == {"gpt-4o": "ChatOpenAI", "anthropic:claude-sonnet-5-5": "ChatAnthropic", "gemini:gemini-2.5-flash": "ChatGoogleGenerativeAI",
                         "ollama:llama3.1:8b": "ChatOllama", "custom:my-model": "ChatOpenAI"}
        assert factory.build("custom:my-model").openai_api_base == "http://localhost:1234/v1"
    monkeypatch.setenv("LOCAL_ONLY", "1")
    with context.use(creds), pytest.raises(factory.ProviderNotConfigured, match="LOCAL_ONLY"):
        factory.build("anthropic:claude-sonnet-5-5")
    with context.use(creds):
        assert type(factory.build("ollama:llama3.1:8b")).__name__ == "ChatOllama"        # local providers still work


def test_get_llm_routes_by_spec_and_usage_is_labelled(monkeypatch):
    with context.use({"ANTHROPIC_API_KEY": "sk-ant-test-1234"}), app_settings.use(resolver.resolve({"models": {"generator_model": "anthropic:claude-sonnet-5-5"}})):
        llm = app_settings.get_llm("generator")
        assert type(llm).__name__ == "ChatAnthropic" and llm.callbacks[0].role == "generator" and llm.callbacks[0].model == "anthropic:claude-sonnet-5-5"
        with pytest.raises(factory.ProviderNotConfigured):
            app_settings.get_llm("router")                                                   # still points at OpenAI, which is not connected here


def test_preflight_names_the_jobs_and_providers(monkeypatch):
    from fastapi import HTTPException
    from app.services import run_context
    s = resolver.resolve({"models": {"generator_model": "anthropic:claude-sonnet-5-5"}})
    with pytest.raises(HTTPException) as e:
        run_context.preflight(s, {"ANTHROPIC_API_KEY": "sk-ant-test-1234"}, run_context.RAG_ROLES)
    assert e.value.status_code == 400 and "OpenAI" in e.value.detail and "router" in e.value.detail and "Anthropic" not in e.value.detail
    run_context.preflight(s, {"ANTHROPIC_API_KEY": "sk-ant-test-1234", "OPENAI_API_KEY": "sk-openai-test-1234"}, run_context.RAG_ROLES)


def test_url_guard(monkeypatch):
    for bad in ("ftp://x/y", "not a url", "http://169.254.169.254/latest/meta-data", "file:///etc/passwd"):
        with pytest.raises(urls.UnsafeURL):
            urls.check(bad)
    assert urls.check("http://localhost:11434/") == "http://localhost:11434"
    monkeypatch.setenv("LLM_ALLOW_PRIVATE_URLS", "0")
    with pytest.raises(urls.UnsafeURL):
        urls.check("http://127.0.0.1:11434")


def test_cost_estimates_only_for_known_prices():
    from app.services import usage_ledger
    assert usage_ledger.cost("gpt-4o", 1_000_000, 0) == 2.5 and usage_ledger.cost("anthropic:claude-sonnet-5-5", 1000, 1000) == 0.0


# ---------------------------------------------------------------- API + storage

@pytest.fixture
def client(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/p.db")

    async def setup():
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(sc, tables=[ProviderCredential.__table__, User.__table__]))
    asyncio.run(setup())
    Session = async_sessionmaker(eng, expire_on_commit=False)
    app = FastAPI()
    app.include_router(providers_api.router)

    async def db():
        async with Session() as s:
            yield s
    me = {"u": User(id=7, email="a@x", username="a", role="analyst", hashed_password="x", is_active=True, token_version=0)}
    app.dependency_overrides[get_db_session] = db
    app.dependency_overrides[get_current_user] = lambda: me["u"]
    c = TestClient(app)
    c.me, c.Session = me, Session
    return c


def prov(c, pid):
    return next(p for p in c.get("/providers").json()["providers"] if p["id"] == pid)


def test_save_mask_remove_and_never_leak(client):
    assert not prov(client, "anthropic")["configured"]
    r = client.put("/providers/anthropic", json={"values": {"ANTHROPIC_API_KEY": "sk-ant-secret-ABCD1234"}})
    assert r.status_code == 200
    body = r.text
    assert "sk-ant-secret" not in body and "ABCD1234" not in body.replace("1234", "")                    # only the last four may show
    p = r.json()
    f = p["fields"][0]
    assert p["configured"] and p["source"] == "yours" and f["set"] and f["preview"] == "••••••••1234" and f["secret"]
    assert "sk-ant-secret" not in client.get("/providers").text

    async def stored():
        async with client.Session() as s:
            from sqlalchemy import select
            return (await s.execute(select(ProviderCredential))).scalars().all()
    rows = asyncio.run(stored())
    assert len(rows) == 1 and "sk-ant-secret" not in rows[0].value_enc and rows[0].user_id == "7"          # encrypted at rest
    assert client.delete("/providers/anthropic").json()["configured"] is False
    assert asyncio.run(stored()) == []


def test_validation_and_scoping(client):
    assert client.put("/providers/anthropic", json={"values": {"ANTHROPIC_API_KEY": "short"}}).status_code == 422
    assert client.put("/providers/anthropic", json={"values": {"OPENAI_API_KEY": "sk-whatever-12345"}}).status_code == 422       # not this provider's field
    assert client.put("/providers/ollama", json={"values": {"OLLAMA_BASE_URL": "ftp://x"}}).status_code == 422
    assert client.put("/providers/ollama", json={"values": {"OLLAMA_BASE_URL": "http://169.254.169.254"}}).status_code == 422
    assert client.put("/providers/nope", json={"values": {}}).status_code == 404
    assert client.put("/providers/openai?scope=workspace", json={"values": {"OPENAI_API_KEY": "sk-shared-key-9999"}}).status_code == 403
    client.me["u"] = User(id=1, email="adm@x", username="adm", role="admin", hashed_password="x", is_active=True, token_version=0)
    assert client.put("/providers/openai?scope=workspace", json={"values": {"OPENAI_API_KEY": "sk-shared-key-9999"}}).json()["source"] == "workspace"
    client.me["u"] = User(id=7, email="a@x", username="a", role="analyst", hashed_password="x", is_active=True, token_version=0)
    assert prov(client, "openai")["source"] == "workspace"                                              # a user with no key of their own gets the shared one
    client.put("/providers/openai", json={"values": {"OPENAI_API_KEY": "sk-mine-key-00001111"}})
    assert prov(client, "openai")["source"] == "yours"
    client.me["u"] = User(id=8, email="b@x", username="b", role="analyst", hashed_password="x", is_active=True, token_version=0)
    assert prov(client, "openai")["source"] == "workspace" and prov(client, "openai")["fields"][0]["preview"].endswith("9999")    # not user 7's key


def test_locked_deployment_is_read_only(client, monkeypatch):
    monkeypatch.setenv("LOCK_SETTINGS", "1")
    assert client.put("/providers/openai", json={"values": {"OPENAI_API_KEY": "sk-whatever-12345"}}).status_code == 403
    assert client.get("/providers").json()["locked"] is True


def test_environment_keys_show_as_server_and_listing_has_embeddings_note(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-environment-5555")
    p = prov(client, "openai")
    assert p["configured"] and p["source"] == "server" and p["fields"][0]["preview"].endswith("5555")
    full = client.get("/providers").json()
    assert full["embeddings"]["model"] == "text-embedding-3-large" and {x["id"] for x in full["providers"]} == {"openai", "anthropic", "gemini", "ollama", "custom"}
    assert all(x["installed"] for x in full["providers"])


def test_connection_test_uses_unsaved_values_and_scrubs_errors(client, monkeypatch):
    seen = {}

    def fake_build(spec, **kw):
        seen["spec"], seen["key"] = spec, context.get("ANTHROPIC_API_KEY")

        class M:
            def invoke(self, prompt):
                if seen["key"] == "sk-ant-bad-key-0000":
                    raise RuntimeError("401 invalid x-api-key sk-ant-bad-key-0000")
                return type("R", (), {"content": "ok"})()
        return M()
    monkeypatch.setattr(factory, "build", fake_build)
    ok = client.post("/providers/anthropic/test", json={"values": {"ANTHROPIC_API_KEY": "sk-ant-good-key-1111"}, "model": ""}).json()
    assert ok["ok"] and seen["spec"] == "anthropic:claude-haiku-5-5" and seen["key"] == "sk-ant-good-key-1111"
    bad = client.post("/providers/anthropic/test", json={"values": {"ANTHROPIC_API_KEY": "sk-ant-bad-key-0000"}}).json()
    assert not bad["ok"] and "sk-ant-bad-key-0000" not in bad["message"] and "••••" in bad["message"]
    assert client.post("/providers/anthropic/test", json={"values": {"OPENAI_API_KEY": "x" * 20}}).status_code == 422
    nothing = client.post("/providers/ollama/test", json={}).json()
    assert not nothing["ok"] and "model" in nothing["message"].lower()


def test_discovery_of_models(client, monkeypatch):
    class Resp:
        def __init__(self, j): self._j = j
        def json(self): return self._j

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None):
            return Resp({"models": [{"name": "qwen2.5:14b"}, {"name": "llama3.1:8b"}]} if url.endswith("/api/tags") else {"data": [{"id": "m-b"}, {"id": "m-a"}]})
    monkeypatch.setattr(service.httpx, "AsyncClient", FakeClient)
    assert client.get("/providers/ollama/models").json()["models"] == []                                  # no address saved yet
    client.put("/providers/ollama", json={"values": {"OLLAMA_BASE_URL": "http://localhost:11434"}})
    assert client.get("/providers/ollama/models").json()["models"] == ["llama3.1:8b", "qwen2.5:14b"]
    client.put("/providers/custom", json={"values": {"LLM_CUSTOM_BASE_URL": "http://localhost:1234/v1"}})
    assert client.get("/providers/custom/models").json()["models"] == ["m-a", "m-b"]
    assert client.get("/providers/anthropic/models").json()["models"] == []


def test_usage_is_not_double_counted_when_roles_report():
    from app.services import usage_ledger
    from app.utils.usage_tracker import UsageTracker
    t = UsageTracker(run_label="x")
    t.record("generator", "gpt-4o-mini", 1000, 200)
    t.record("router", "gpt-4o-mini", 500, 50)
    t.record("graph_total", "all", 1500, 250)                       # the aggregate must not be added on top of the roles

    class Db:
        def add(self, *_): pass
        async def commit(self): pass
    out = asyncio.run(usage_ledger.persist(Db(), "1", t))
    assert out["total_tokens"] == 1750 and out["prompt_tokens"] == 1500 and out["completion_tokens"] == 250
