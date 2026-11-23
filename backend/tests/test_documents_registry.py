"""
Offline tests for the documents registry (Qdrant -> SQL sync, filters, facets, endpoints).
Uses a stubbed Qdrant client and in-memory SQLite. No network.

Run:  ./venv/bin/python -m pytest -q tests/test_documents_registry.py
"""
import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import documents as documents_api
from app.database.connection import get_db_session
from app.database.models import Base
from app.auth.deps import get_current_user
from app.services.documents import registry


# ---- stub Qdrant -------------------------------------------------------------

def _pt(doc, page, ctype="text"):
    meta = dict(doc["meta"], content_type=ctype, page_num=page)
    return SimpleNamespace(payload={"page_content": "x", "metadata": meta})


class StubQdrant:
    def __init__(self, collections):
        self.collections = collections  # name -> list of points
        self.facet_calls = 0

    def get_collections(self):
        return SimpleNamespace(collections=[SimpleNamespace(name=n) for n in self.collections])

    @staticmethod
    def _match(point, flt):
        meta = point.payload["metadata"]
        return all(meta.get(c.key.removeprefix("metadata.")) == c.match.value for c in (flt.must if flt else []))

    def scroll(self, collection, scroll_filter=None, limit=10, offset=None, order_by=None, **_):
        pts = [p for p in self.collections[collection] if self._match(p, scroll_filter)]
        if order_by is not None:
            pts.sort(key=lambda p: p.payload["metadata"]["page_num"], reverse=order_by.direction == "desc")
        start = offset or 0
        page = pts[start:start + limit]
        return page, (start + limit if start + limit < len(pts) else None)

    def count(self, collection, count_filter=None, exact=True):
        return SimpleNamespace(count=sum(self._match(p, count_filter) for p in self.collections[collection]))

    def facet(self, collection, key, facet_filter=None, limit=10):
        self.facet_calls += 1
        hashes = sorted({p.payload["metadata"]["content_hash"] for p in self.collections[collection]
                         if self._match(p, facet_filter)})
        return SimpleNamespace(hits=[SimpleNamespace(value=h) for h in hashes])


def _doc(h, ticker, form, d, acc, year, quarter=None, company=None, ext="pdf", **extra):
    meta = {"content_hash": h, "ticker": ticker, "company": company or f"{ticker} Inc", "filing_type": form,
            "year": year, "fiscal_quarter": quarter, "source_file": f"sec_filings/{ticker}/{ticker}_{form}_{d}_{acc}.{ext}",
            "ingestion_timestamp": "2026-05-01T10:00:00+00:00"}
    meta.update(extra)
    return {"meta": meta}


ZM22 = _doc("h1", "ZM", "10-K", "2022-03-07", "000158552122000037", 2022)
ZM21 = _doc("h2", "ZM", "10-K", "2021-03-18", "000158552121000048", 2021)
JNJQ = _doc("h3", "JNJ", "10-Q", "2025-04-20", "000020040625000001", 2025, quarter=1)
LEGACY_ONLY = _doc("h4", "AAPL", "8-K", "2024-01-02", "000032019324000001", 2024, ext="htm")


def _stub():
    return StubQdrant({
        "ticker_zm": [_pt(ZM22, 1), _pt(ZM22, 2), _pt(ZM22, 3), _pt(ZM22, 2, "image"), _pt(ZM21, 1), _pt(ZM21, 2)],
        # JNJ page 1 has no text chunk (image only), so it must come through the facet fallback
        "ticker_jnj": [_pt(JNJQ, 1, "image"), _pt(JNJQ, 2), _pt(JNJQ, 3)],
        "other_collection": [_pt(ZM22, 1)],  # ignored: not ticker_* nor legacy
        "unified_rag_db_hybrid": [_pt(ZM22, 1), _pt(ZM22, 2), _pt(LEGACY_ONLY, 1)],
    })


# ---- fixtures ----------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "ZM").mkdir()
    (tmp_path / "ZM" / "ZM_10-K_2022-03-07_000158552122000037.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(registry, "SEC_FILINGS_DIR", tmp_path)
    monkeypatch.setattr(registry, "_last_sync", None)
    stub = _stub()
    monkeypatch.setattr(registry, "_get_client", lambda: stub)

    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    asyncio.run(_create(engine))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _db():
        async with maker() as s:
            yield s

    app = FastAPI()
    app.include_router(documents_api.router)
    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1")
    return SimpleNamespace(client=TestClient(app), stub=stub, maker=maker)


async def _create(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ---- filename parsing --------------------------------------------------------

def test_parse_filename():
    p = registry.parse_filename("sec_filings/ZM/ZM_10-K_2022-03-07_000158552122000037.pdf")
    assert p == {"ticker": "ZM", "form_type": "10-K", "filing_date": date(2022, 3, 7), "accession": "000158552122000037"}
    assert registry.parse_filename("metadata_sharepoint_TESLA.pdf") == {}
    assert registry.parse_filename("") == {}


# ---- scan / sync -------------------------------------------------------------

def test_scan_merges_collections_and_fills_counts(env):
    scan = registry.scan_qdrant(env.stub)
    assert scan["scanned"] == ["ticker_jnj", "ticker_zm", "unified_rag_db_hybrid"]
    rows = {r["doc_id"]: r for r in scan["rows"]}
    assert set(rows) == {"h1", "h2", "h3", "h4"}  # h1 only once despite 3 collections
    assert rows["h1"]["collection"] == "ticker_zm"
    assert (rows["h1"]["page_count"], rows["h1"]["text_pages"], rows["h1"]["image_chunks"]) == (3, 3, 1)
    assert rows["h1"]["filing_date"] == date(2022, 3, 7) and rows["h1"]["accession"] == "000158552122000037"
    assert rows["h1"]["pdf_status"] == "local" and rows["h2"]["pdf_status"] == "missing"
    assert rows["h3"]["page_count"] == 3 and rows["h3"]["fiscal_quarter"] == 1  # facet fallback
    assert rows["h4"]["collection"] == "unified_rag_db_hybrid" and rows["h4"]["pdf_status"] == "unsupported"


def test_sync_marks_removed_and_is_idempotent(env):
    async def run():
        async with env.maker() as db:
            assert await registry.sync_registry(db, env.stub) == {"documents": 4, "added": 4, "removed": 0}
            assert (await registry.sync_registry(db, env.stub))["added"] == 0
            env.stub.collections["ticker_zm"] = [p for p in env.stub.collections["ticker_zm"]
                                                 if p.payload["metadata"]["content_hash"] != "h2"]
            assert (await registry.sync_registry(db, env.stub))["removed"] == 1
            assert await registry.get_document(db, "h2") is None
    asyncio.run(run())


# ---- endpoints ---------------------------------------------------------------

def test_list_filters_and_facets(env):
    r = env.client.get("/documents", params={"ticker": "ZM", "form": "10-K"})
    assert r.status_code == 200
    body = r.json()
    assert [i["doc_id"] for i in body["items"]] == ["h1", "h2"]  # filing_date desc
    assert body["total"] == 2 and body["next_cursor"] is None
    # tickers facet ignores the ticker filter; forms facet ignores the form filter
    assert {f["value"]: f["count"] for f in body["facets"]["tickers"]} == {"ZM": 2}
    assert {f["value"]: f["count"] for f in body["facets"]["forms"]} == {"10-K": 2}
    assert [f["value"] for f in body["facets"]["years"]] == [2022, 2021]


def test_facets_computed_against_other_filters(env):
    f = env.client.get("/documents/facets", params={"ticker": "ZM"}).json()
    assert {x["value"]: x["count"] for x in f["tickers"]} == {"AAPL": 1, "JNJ": 1, "ZM": 2}  # own filter ignored
    assert {x["value"]: x["count"] for x in f["forms"]} == {"10-K": 2}
    f = env.client.get("/documents/facets", params={"form": "10-Q"}).json()
    assert {x["value"]: x["count"] for x in f["tickers"]} == {"JNJ": 1}
    assert {x["value"]: x["count"] for x in f["forms"]} == {"10-K": 2, "10-Q": 1, "8-K": 1}


def test_other_filters(env):
    ids = lambda **p: [i["doc_id"] for i in env.client.get("/documents", params=p).json()["items"]]
    assert ids(year_from=2022, year_to=2024) == ["h4", "h1"]
    assert ids(quarter=1) == ["h3"]
    assert ids(pdf_status="local") == ["h1"]
    assert ids(has_images=True) == ["h3", "h1"]
    assert ids(has_images=False) == ["h4", "h2"]
    assert ids(q="jnj") == ["h3"]
    assert ids(q="000158552121000048") == ["h2"]
    assert ids(ticker=["ZM", "JNJ"], sort="pages", order="asc")[0] in ("h2", "h3")
    assert ids(sort="ticker", order="asc") == ["h4", "h3", "h1", "h2"]


def test_cursor_paging(env):
    first = env.client.get("/documents", params={"limit": 3}).json()
    assert len(first["items"]) == 3 and first["next_cursor"] == "3" and first["total"] == 4
    second = env.client.get("/documents", params={"limit": 3, "cursor": first["next_cursor"]}).json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert env.client.get("/documents", params={"cursor": "abc"}).status_code == 400


def test_detail_and_sync(env):
    assert env.client.get("/documents/nope").status_code == 404
    d = env.client.get("/documents/h1").json()
    assert d["ticker"] == "ZM" and d["pdf_status"] == "local" and d["page_count"] == 3
    assert env.client.post("/documents/sync").json() == {"documents": 4, "added": 0, "removed": 0}


def test_lazy_sync_respects_ttl(env):
    env.client.get("/documents")
    calls = env.stub.facet_calls
    env.client.get("/documents")
    assert env.stub.facet_calls == calls  # second request inside the TTL does not rescan
