"""The one place every ingestion path (SEC filings, user documents, analyst notes) embeds and stores chunks."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx

from rag.vectordb import client as vdb


def _db(upsert):
    db = vdb.load_vector_database.__new__(vdb.load_vector_database)
    db.collection_name = "c"
    db.async_qdrant_client = NS(upsert=upsert)
    db.generate_embeddings_for_ingestion = AsyncMock(side_effect=lambda texts, label="": {"dense": [[0.1, 0.2]] * len(texts), "sparse": [None] * len(texts)})
    return db


def test_embeds_the_header_but_stores_clean_text_in_batches_and_passes_wait():
    calls = []

    async def upsert(collection_name, points, wait):
        calls.append((collection_name, [p.id for p in points], wait, points[0].payload))

    db = _db(upsert)
    chunks = [{"id": f"id{i}", "text": f"text {i}", "embed": f"[ACME, 10-K]\n\ntext {i}", "metadata": {"n": i}} for i in range(5)]
    assert asyncio.run(db.upsert_texts(chunks, label="x", batch_size=2, wait=True)) == 5
    assert db.generate_embeddings_for_ingestion.await_args.args[0][0] == "[ACME, 10-K]\n\ntext 0"        # embedded with the header
    assert [c[1] for c in calls] == [["id0", "id1"], ["id2", "id3"], ["id4"]] and all(c[2] is True for c in calls)
    assert calls[0][3] == {"page_content": "text 0", "metadata": {"n": 0}}                              # stored text stays clean


def test_a_dropped_connection_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr(vdb.asyncio, "sleep", AsyncMock())
    seen = []

    async def upsert(collection_name, points, wait):
        seen.append(1)
        if len(seen) < 3:
            raise httpx.ConnectError("reset")

    assert asyncio.run(_db(upsert).upsert_texts([{"id": "a", "text": "t", "metadata": {}}])) == 1
    assert len(seen) == 3


def test_gives_up_after_the_last_attempt(monkeypatch):
    monkeypatch.setattr(vdb.asyncio, "sleep", AsyncMock())

    async def upsert(collection_name, points, wait):
        raise httpx.ConnectError("down")

    try:
        asyncio.run(_db(upsert).upsert_texts([{"id": "a", "text": "t", "metadata": {}}], max_attempts=2))
        raise AssertionError("expected the error to surface")
    except httpx.ConnectError:
        pass


def test_nothing_to_store_does_not_embed():
    db = _db(AsyncMock())
    assert asyncio.run(db.upsert_texts([])) == 0
    db.generate_embeddings_for_ingestion.assert_not_awaited()
