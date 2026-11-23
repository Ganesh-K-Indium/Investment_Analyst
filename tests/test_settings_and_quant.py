"""Settings schema/resolution, model registry, and the quant security + runtime pieces."""
import asyncio
import time

import pytest

from app.services import mcp_auth, mcp_registry, quant_runtime
from app.services import app_settings
from app.services.app_settings import schema, resolver


def test_defaults_cover_every_declared_field():
    for sid, sec in schema.SECTIONS.items():
        assert set(schema.SYSTEM_DEFAULTS[sid]) == set(sec["fields"])


def test_validate_accepts_good_and_rejects_bad():
    ok = schema.validate({"retrieval": {"top_k": 20, "rerank_provider": "off"}, "models": {"generator_model": "gpt-4.1"}})
    assert ok["retrieval"]["top_k"] == 20
    for bad in ({"nope": {}}, {"retrieval": {"nope": 1}}, {"retrieval": {"top_k": 999}}, {"retrieval": {"top_k": True}},
                {"models": {"generator_model": "gpt-5.2"}}, {"retrieval": {"sources": ["filings", "bogus"]}}, {"answers": {"verify_mode": "maybe"}}):
        with pytest.raises(schema.SettingsError):
            schema.validate(bad)


def test_cost_rule_blocks_non_gpt4_models():
    assert all(m.startswith("gpt-4") for m in schema.ALLOWED_MODELS)


def test_custom_framework_validation():
    fw = {"id": "credit", "name": "Credit review", "triggers": ["credit"], "sections": ["Leverage"], "prompt": "x"}
    assert schema.validate({"modules": {"custom": [fw]}})["modules"]["custom"][0]["id"] == "credit"
    with pytest.raises(schema.SettingsError):
        schema.validate({"modules": {"custom": [{**fw, "id": "alpha"}]}})           # collides with a built-in
    with pytest.raises(schema.SettingsError):
        schema.validate({"modules": {"custom": [fw, fw]}})                           # duplicate


def test_resolution_order_and_context():
    eff = resolver.resolve({"retrieval": {"top_k": 5}}, {"retrieval": {"top_k": 7}})
    assert eff["retrieval"]["top_k"] == 7 and eff["retrieval"]["rerank_enabled"] is True
    assert app_settings.get("retrieval", "top_k") == 12
    with app_settings.use(eff):
        assert app_settings.get("retrieval", "top_k") == 7
    assert app_settings.get("retrieval", "top_k") == 12


def test_model_registry_follows_settings():
    with app_settings.use(resolver.resolve({"models": {"generator_model": "gpt-4.1", "temperature": 0.3}})):
        llm = app_settings.get_llm("generator")
        assert llm.model_name == "gpt-4.1" and llm.temperature == 0.3
        assert app_settings.get_llm("router").temperature == 0      # planning roles stay deterministic
    assert app_settings.get_llm("generator").model_name == "gpt-4o"


def test_integration_token_binds_user_and_expires():
    t = mcp_auth.sign_integration(7, 42)
    assert mcp_auth.verify_integration(t) == ("7", 42)
    body, sig = t.split(".")
    with pytest.raises(mcp_auth.AuthError):
        mcp_auth.verify_integration(body + "." + sig[:-2] + "AA")
    with pytest.raises(mcp_auth.AuthError):
        mcp_auth.verify_integration("42")                                          # a bare id is no longer accepted
    with pytest.raises(mcp_auth.AuthError):
        mcp_auth.verify_integration(mcp_auth.sign_integration(7, 42, ttl=-1))


@pytest.mark.parametrize("sql", ["SELECT 1; DROP TABLE x", "DELETE FROM t", "select * from t into outfile", "WITH a AS (DELETE FROM t RETURNING *) SELECT * FROM a",
                                 "SELECT pg_sleep(100)", "/* hi */ UPDATE t SET a=1", "SET ROLE admin", "select 1 -- ok\n; select 2"])
def test_sql_guard_rejects(sql):
    with pytest.raises(mcp_auth.AuthError):
        mcp_auth.check_select_only(sql)


def test_sql_guard_accepts_and_limits():
    assert mcp_auth.check_select_only("SELECT a FROM t WHERE b = 'x; DROP'") .endswith("LIMIT 100")
    assert mcp_auth.check_select_only("with a as (select 1) select * from a limit 5;") == "with a as (select 1) select * from a limit 5"


def test_registry_uses_loopback_and_env(monkeypatch):
    assert mcp_registry.server("options")["url"] == "http://127.0.0.1:8568/mcp"
    monkeypatch.setenv("MCP_RESEARCH_URL", "http://research.internal:9000/mcp")
    assert mcp_registry.server("research")["port"] == 9000
    monkeypatch.setenv("MCP_POSTGRES_ENABLED", "0")
    assert not mcp_registry.server("postgres")["enabled"]


def test_request_preamble_names_disabled_agents():
    eff = resolver.resolve({"quant": {"enabled_agents": ["stock_information", "technical_analysis"], "benchmark": "QQQ"}})
    p = quant_runtime.request_preamble(eff)
    assert "QQQ" in p and "options_intelligence_agent" in p and "stock_information_agent" not in p


class _FakeTool:
    def __init__(self, fn, name="get_price"):
        self.coroutine, self.name = fn, name


def test_harden_tools_caches_and_times_out():
    calls = []

    async def fast(x):
        calls.append(x)
        return f"r{x}"

    async def slow(x):
        await asyncio.sleep(2)

    fast_t, slow_t = _FakeTool(fast), _FakeTool(slow, "slow")
    quant_runtime.harden_tools([fast_t, slow_t], "t")
    cfg = resolver.resolve({"quant": {"cache_ttl": 60, "tool_timeout": 5}})

    async def go():
        with app_settings.use(cfg):
            assert await fast_t.coroutine(1) == "r1" and await fast_t.coroutine(1) == "r1"
        with app_settings.use(resolver.resolve({"quant": {"tool_timeout": 5}})):
            pass
        with app_settings.use({**cfg, "quant": {**cfg["quant"], "tool_timeout": 0.05, "cache_ttl": 0}}):
            with pytest.raises(RuntimeError, match="did not answer"):
                await slow_t.coroutine(1)
    asyncio.run(go())
    assert calls == [1]                                                             # second call came from the cache


def test_role_chat_model_resolves_per_call(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage
    seen = []

    class Fake:
        def __init__(self, model):
            self.model = model
        def bind_tools(self, tools, **kw):
            return self
        async def ainvoke(self, messages, **kw):
            seen.append(self.model)
            return AIMessage(content=self.model)

    monkeypatch.setattr(app_settings, "get_llm", lambda role, **kw: Fake(app_settings.model_for(role)))
    m = quant_runtime.RoleChatModel(role="quant_agent").bind_tools([])

    async def go():
        with app_settings.use(resolver.resolve({"models": {"quant_agent_model": "gpt-4.1"}})):
            a = await m.ainvoke([HumanMessage(content="hi")])
        b = await m.ainvoke([HumanMessage(content="hi")])
        return a.content, b.content
    assert asyncio.run(go()) == ("gpt-4.1", "gpt-4o")
