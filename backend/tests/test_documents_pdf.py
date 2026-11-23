"""
Offline tests for PDF serving, re-download state machine and alignment check of the Document Inspector.
No network: SecEdgarFetcher download is mocked, Qdrant is a tiny stub, DB is in-memory SQLite.

Run:  ./venv/bin/python -m pytest -q tests/test_documents_pdf.py
"""
import asyncio
import time
from datetime import date
from types import SimpleNamespace

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import documents as documents_api
from app.auth.deps import get_current_user
from app.database.connection import get_db_session
from app.database.models import Base, Document
from app.services.documents import pdf as pdf_service
from app.services.documents import registry

NAME = "ZM_10-K_2022-03-07_000158552122000037.pdf"
PAGES = ["Zoom Video Communications annual report page one", "Risk factors for the fiscal year page two"]


def _make_pdf(path, pages=PAGES):
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for text in pages:
        doc.new_page().insert_text((72, 72), text)
    doc.save(path)
    doc.close()


class StubQdrant:
    """Returns the indexed page text for scroll(); only what indexed_fingerprints() needs."""
    def __init__(self, pages):
        self.pages = pages

    def scroll(self, collection, **_):
        pts = [SimpleNamespace(payload={"page_content": t + "\n\n[TABLES]\n| a |", "metadata": {"page_num": i + 1}})
               for i, t in enumerate(self.pages)]
        return pts, None


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "SEC_FILINGS_DIR", tmp_path)
    monkeypatch.setattr(registry, "_last_sync", time.monotonic())  # skip lazy Qdrant sync
    monkeypatch.setattr(registry, "_get_client", lambda: StubQdrant(PAGES))
    registry.ACTIVE_DOWNLOADS.clear()
    pdf_service._jobs.clear()

    # file-backed (not shared in-memory): a request session closing must not roll back the job's connection
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            s.add(Document(doc_id="h1", ticker="ZM", company="Zoom", form_type="10-K", fiscal_year=2022,
                           filing_date=date(2022, 3, 7), accession="000158552122000037", source_file=NAME,
                           collection="ticker_zm", page_count=2, pdf_status="missing", removed=False))
            s.add(Document(doc_id="h2", ticker="ZM", source_file="ZM_8-K.htm", collection="ticker_zm",
                           pdf_status="unsupported", removed=False))
            await s.commit()

    asyncio.run(_setup())
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _db():
        async with maker() as s:
            yield s

    # the background job opens its own session through SessionLocal
    import app.database.connection as conn_mod
    monkeypatch.setattr(conn_mod, "SessionLocal", maker)

    app = FastAPI()
    app.include_router(documents_api.router)
    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1")
    with TestClient(app) as client:  # one event loop for the whole test, so background jobs survive between requests
        yield SimpleNamespace(client=client, dir=tmp_path, maker=maker)


# ---- fingerprints / alignment -------------------------------------------------

def test_fingerprint_ignores_case_punctuation_and_tables():
    assert pdf_service.fingerprint("Item 1.  Business\n\nWe make X") == pdf_service.fingerprint("ITEM 1 business we make x")
    assert pdf_service.fingerprint("   \n") is None


def test_check_alignment():
    idx = {1: ["a"], 2: ["b", "b2"], 3: ["c"]}
    assert pdf_service.check_alignment(idx, ["a", "b", "c"])
    assert not pdf_service.check_alignment(idx, ["a", "b"])  # fewer pages than indexed
    assert not pdf_service.check_alignment(idx, ["a", "x", "y"])  # paginates differently
    assert not pdf_service.check_alignment({}, ["a"])


# ---- /pdf ---------------------------------------------------------------------

def test_pdf_streams_with_range(env):
    _make_pdf(env.dir / "ZM" / NAME)
    full = env.client.get("/documents/h1/pdf")
    assert full.status_code == 200 and full.headers["content-type"] == "application/pdf"
    assert full.content.startswith(b"%PDF") and "etag" in full.headers
    part = env.client.get("/documents/h1/pdf", headers={"Range": "bytes=0-9"})
    assert part.status_code == 206 and part.content == full.content[:10]


def test_pdf_missing_and_unsupported_and_unknown(env):
    r = env.client.get("/documents/h1/pdf")
    assert r.status_code == 404 and r.json()["detail"]["pdf_status"] == "missing"
    assert env.client.get("/documents/h2/pdf").json()["detail"]["pdf_status"] == "unsupported"
    assert env.client.get("/documents/nope/pdf").status_code == 404


def test_doc_id_is_never_a_path(env):
    assert env.client.get("/documents/..%2F..%2Fetc%2Fpasswd/pdf").status_code == 404


# ---- ensure-pdf state machine --------------------------------------------------

def _wait(env, want, tries=50):
    for _ in range(tries):
        s = env.client.get("/documents/h1/pdf-status").json()
        # a poll can read the row just before the job commits; wait for the alignment verdict too
        if s["status"] == want and (want != "local" or s["aligned"] is not None):
            return s
        time.sleep(0.05)
    raise AssertionError(f"never reached {want}: {s}")


def test_ensure_pdf_downloads_and_checks_alignment(env, monkeypatch):
    async def fake_download(doc):
        _make_pdf(env.dir / "ZM" / NAME)
    monkeypatch.setattr(pdf_service, "_download", fake_download)

    first = env.client.post("/documents/h1/ensure-pdf").json()
    assert first["status"] == "downloading"
    status = _wait(env, "local")
    assert status["aligned"] is True
    assert env.client.get("/documents/h1").json()["aligned"] is True
    assert env.client.get("/documents/h1/pdf").status_code == 200
    # already local: no new job
    assert env.client.post("/documents/h1/ensure-pdf").json()["status"] == "local"


def test_ensure_pdf_flags_misaligned_redownload(env, monkeypatch):
    async def fake_download(doc):
        _make_pdf(env.dir / "ZM" / NAME, ["Completely different layout", "Another page", "Third page"])
    monkeypatch.setattr(pdf_service, "_download", fake_download)
    env.client.post("/documents/h1/ensure-pdf")
    assert _wait(env, "local")["aligned"] is False


def test_ensure_pdf_failure_is_reported_and_retryable(env, monkeypatch):
    async def boom(doc):
        raise RuntimeError("EDGAR has no such filing")
    monkeypatch.setattr(pdf_service, "_download", boom)
    env.client.post("/documents/h1/ensure-pdf")
    status = _wait(env, "failed")
    assert "EDGAR has no such filing" in status["error"]
    assert "h1" not in registry.ACTIVE_DOWNLOADS  # lock released, so a retry is allowed
    assert env.client.post("/documents/h1/ensure-pdf").json()["status"] == "downloading"


def test_ensure_pdf_single_flight(env, monkeypatch):
    calls = []

    async def slow(doc):
        calls.append(1)
        await asyncio.sleep(0.3)
        _make_pdf(env.dir / "ZM" / NAME)
    monkeypatch.setattr(pdf_service, "_download", slow)
    a = env.client.post("/documents/h1/ensure-pdf").json()
    b = env.client.post("/documents/h1/ensure-pdf").json()
    assert a["status"] == b["status"] == "downloading"
    _wait(env, "local")
    assert len(calls) == 1


def test_ensure_pdf_unsupported_source(env):
    assert env.client.post("/documents/h2/ensure-pdf").json()["status"] == "unsupported"
