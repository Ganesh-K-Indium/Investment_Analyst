"""Spend is counted for every provider and every path: prices, nesting without double counting, background work, tool servers."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.models import Base, UsageEvent
from app.services import pricing, usage_ledger
from app.utils.usage_tracker import get_current_tracker, record_usage, track_usage


@pytest.mark.parametrize("spec,expected", [
    ("gpt-4o", (2.5, 10.0)),
    ("gpt-4o-2024-08-06", (2.5, 10.0)),                 # dated release
    ("gpt-4o-mini", (0.15, 0.60)),                     # not mistaken for gpt-4o
    ("anthropic:claude-opus-5-5", (4.0, 20.0)),
    ("anthropic:claude-sonnet-4-5-20250929", (3.0, 15.0)),
    ("gemini:gemini-2.5-flash", (0.30, 2.50)),
    ("custom:anthropic/claude-sonnet-5-5", (2.0, 10.0)),  # OpenRouter-style name through a gateway
    ("ollama:llama3.1:8b", (0.0, 0.0)),                 # your own hardware
    ("text-embedding-3-large", (0.13, 0.0)),
])
def test_prices_cover_every_provider(spec, expected):
    assert pricing.price(spec) == expected


def test_unknown_models_are_unpriced_and_overrides_win(monkeypatch):
    assert pricing.price("custom:my-finetune") is None
    from app.core import config
    real = config.get_settings()
    monkeypatch.setattr(pricing, "_overrides", lambda: {"custom:*": (0.0, 0.0), "gpt-4o": (1.0, 2.0)})
    assert pricing.price("custom:my-finetune") == (0.0, 0.0) and pricing.price("gpt-4o") == (1.0, 2.0)
    assert real is not None


def test_a_self_hosted_server_on_this_machine_costs_nothing(monkeypatch):
    from app.services.llm_providers import context
    with context.use({"LLM_CUSTOM_BASE_URL": "http://localhost:8000/v1"}):
        assert pricing.price("custom:qwen2.5-72b") == (0.0, 0.0)
    with context.use({"LLM_CUSTOM_BASE_URL": "https://api.example-gateway.com/v1"}):
        assert pricing.price("custom:qwen2.5-72b") is None


def test_nested_trackers_roll_up_unless_already_stored():
    with track_usage("request") as outer:
        with track_usage("ingest:file.pdf"):                   # ingestion's own tracker inside a metered request
            record_usage("embeddings.dense", "text-embedding-3-large", prompt_tokens=1000)
        with track_usage("ask") as answered:                   # a flow that stores its own usage
            record_usage("generator", "gpt-4o", prompt_tokens=10, completion_tokens=5)
            answered.persisted = True
        with track_usage("job", detached=True):                # background work storing its own
            record_usage("vision", "gpt-4o", prompt_tokens=7)
    assert [(e.stage, e.prompt_tokens) for e in outer.entries] == [("embeddings.dense", 1000)]
    assert get_current_tracker() is None


def _db():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_metered_stores_every_provider_once_and_marks_unpriced(monkeypatch):
    async def run():
        engine, maker = _db()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        from app.database import connection
        monkeypatch.setattr(connection, "SessionLocal", maker)
        async with usage_ledger.metered("7", "POST /notes", portfolio_id=3):
            record_usage("handwriting_ocr", "anthropic:claude-sonnet-5-5", prompt_tokens=2000, completion_tokens=500)
            record_usage("embeddings.dense", "text-embedding-3-large", prompt_tokens=1000)
            record_usage("vision", "custom:my-finetune", prompt_tokens=100, completion_tokens=10)
            record_usage("text_extraction", "n/a")                  # timing only: no row
            async with usage_ledger.metered("7", "inner"):           # nested: adds nothing of its own
                record_usage("router", "gpt-4o-mini", prompt_tokens=100)
        async with maker() as db:
            rows = {r.model: r for r in (await db.execute(select(UsageEvent))).scalars().all()}
            assert set(rows) == {"anthropic:claude-sonnet-5-5", "text-embedding-3-large", "custom:my-finetune", "gpt-4o-mini"}
            assert rows["anthropic:claude-sonnet-5-5"].cost_usd == pytest.approx(0.009) and rows["anthropic:claude-sonnet-5-5"].priced
            assert rows["custom:my-finetune"].priced is False and rows["custom:my-finetune"].cost_usd == 0.0
            assert all(r.portfolio_id == 3 and r.user_id == "7" for r in rows.values())
            summary = await usage_ledger.month_summary(db, "7", 0)
            assert summary["unpriced_tokens"] == 110 and summary["tokens"] == 2000 + 500 + 1000 + 110 + 100
        await engine.dispose()
    asyncio.run(run())


def test_tool_server_usage_is_recorded_and_removed_from_the_result():
    from app.services.quant_runtime import _take_usage
    payload = json.dumps({"rating": "Buy", "_usage": [{"model": "gpt-4o", "prompt_tokens": 1200, "completion_tokens": 300}]})
    with track_usage("quant") as t:
        out = _take_usage(payload, "search_analyst_ratings")
        again = _take_usage(([{"type": "text", "text": payload}], None), "x")
    assert json.loads(out) == {"rating": "Buy"} and "_usage" not in again[0][0]["text"]
    assert [(e.stage, e.model, e.prompt_tokens) for e in t.entries][:1] == [("tool.search_analyst_ratings", "gpt-4o", 1200)]
    assert _take_usage("plain text", "x") == "plain text"


def test_request_meter_reads_the_user_from_the_access_token():
    from app.auth.jwt import create_access_token
    from app.core.usage_middleware import _user_id
    token = create_access_token(42, "a@b.c", "analyst", 0)
    assert _user_id({"headers": [(b"authorization", f"Bearer {token}".encode())]}) == "42"
    assert _user_id({"headers": [(b"authorization", b"Bearer junk")]}) is None and _user_id({"headers": []}) is None
