"""
Offline tests for the Document Inspector page JSON (text/tables/images from Qdrant chunks + PDF geometry).

Run:  ./venv/bin/python -m pytest -q tests/test_documents_pages.py -W ignore
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
from app.services.documents import pages as pg
from app.services.documents import registry

NAME = "ZM_10-K_2022-03-07_000158552122000037.pdf"
TABLE_MD = "| a | b |\n| --- | --- |\n| 1 | 2 |"
IMG_TEXT = "**SEARCHABLE SUMMARY:** A line chart of TSR.\n\n**Image Type:** Line Chart\n\nDATA..."


def _pt(pid, page, ctype, content, **meta):
    m = {"content_hash": "h1", "page_num": page, "content_type": ctype, **meta}
    return SimpleNamespace(id=pid, payload={"page_content": content, "metadata": m})


POINTS = [
    _pt("p1", 1, "text", "Page one text"),
    _pt("p2", 2, "text", "Page two text" + pg.TABLES_MARKER + TABLE_MD),
    _pt("p3", 2, "image", IMG_TEXT, image_content_hash="imgh", image_source_in_file="x-page2-7"),
]


class StubQdrant:
    def scroll(self, collection, scroll_filter=None, limit=10, offset=None, **_):
        conds = {c.key.removeprefix("metadata."): c.match.value for c in scroll_filter.must}
        pts = [p for p in POINTS if all(p.payload["metadata"].get(k) == v for k, v in conds.items())]
        return pts, None


def _make_pdf(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    d = fitz.open()
    d.new_page().insert_text((72, 72), "Page one text")
    d.new_page().insert_text((72, 72), "Page two text")
    d.save(path)
    d.close()


def _doc(**kw):
    base = dict(doc_id="h1", ticker="ZM", form_type="10-K", fiscal_year=2022, filing_date=date(2022, 3, 7),
                source_file=NAME, collection="ticker_zm", page_count=2, pdf_status="missing", removed=False,
                page_fingerprints=None, ingested_at=None)
    return SimpleNamespace(**{**base, **kw})


@pytest.fixture(autouse=True)
def sec_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "SEC_FILINGS_DIR", tmp_path)
    pg._cache.clear()
    return tmp_path


def test_split_page_content():
    assert pg.split_page_content("just text") == ("just text", [])
    text, tables = pg.split_page_content("t" + pg.TABLES_MARKER + TABLE_MD + "\n\n" + TABLE_MD)
    assert text == "t" and tables == [TABLE_MD, TABLE_MD]


def test_parse_image_chunk():
    img = pg.parse_image_chunk({"image_content_hash": "imgh", "image_source_in_file": "x-page44-238"}, IMG_TEXT)
    assert (img["image_id"], img["type"], img["xref"]) == ("imgh", "Line Chart", 238)
    assert img["summary"] == "A line chart of TSR."


def test_build_page_without_pdf_has_text_but_no_geometry():
    page = pg.build_page(StubQdrant(), _doc(), 2)
    assert page["text"] == "Page two text" and page["size"] is None and page["blocks"] == []
    assert page["tables"] == [{"markdown": TABLE_MD, "headers": None, "rows": None, "bbox": None, "verified": None, "source": "index"}]
    assert page["images"][0]["bbox"] is None and page["images"][0]["type"] == "Line Chart"
    assert "xref" not in page["images"][0]
    assert page["meta"]["pdf_available"] is False
    assert {c["point_id"] for c in page["chunks"]} == {"p2", "p3"}


def test_build_page_with_pdf_adds_blocks_and_is_cached(sec_dir):
    _make_pdf(sec_dir / "ZM" / NAME)
    page = pg.build_page(StubQdrant(), _doc(), 1)
    assert page["size"] == {"width": 595.28, "height": 841.89} or page["size"]["width"] > 500
    assert page["blocks"] and page["blocks"][0]["text"] == "Page one text"
    assert page["meta"]["pdf_available"] is True
    assert pg.build_page(StubQdrant(), _doc(), 1) is page  # served from cache


def test_page_index():
    idx = pg.build_page_index(StubQdrant(), _doc(page_count=3))
    assert idx[0] == {"page": 1, "chars": 13, "has_tables": False, "has_images": False}
    assert idx[1]["has_tables"] and idx[1]["has_images"]
    assert idx[2] == {"page": 3, "chars": 0, "has_tables": False, "has_images": False}  # page with no chunks


def test_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "_last_sync", time.monotonic())
    monkeypatch.setattr(registry, "_get_client", lambda: StubQdrant())
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")

    async def setup():
        async with engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            s.add(Document(doc_id="h1", ticker="ZM", source_file=NAME, collection="ticker_zm", page_count=2, removed=False))
            await s.commit()
    asyncio.run(setup())
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _db():
        async with maker() as s:
            yield s

    app = FastAPI()
    app.include_router(documents_api.router)
    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u")
    with TestClient(app) as client:
        r = client.get("/documents/h1/pages/2")
        assert r.status_code == 200 and r.json()["text"] == "Page two text" and len(r.json()["images"]) == 1
        assert client.get("/documents/h1/pages/3").status_code == 404
        assert client.get("/documents/h1/pages/0").status_code == 404
        assert client.get("/documents/nope/pages/1").status_code == 404
        idx = client.get("/documents/h1/pages").json()
        assert idx["page_count"] == 2 and len(idx["pages"]) == 2


def test_locate_exact_fuzzy_and_missing(tmp_path):
    import fitz
    from app.services.documents import pages as pg
    path = tmp_path / "t.pdf"
    d = fitz.open()
    page = d.new_page()
    page.insert_text((72, 100), "Revenue increased by twelve percent compared to the prior year.")
    page.insert_text((72, 120), "Operating margin expanded due to lower costs.")
    d.save(path)
    pg._locate.cache_clear()
    exact = pg._locate(str(path), 1.0, 1, "Operating margin expanded due to lower costs.")
    assert exact["method"] == "exact" and exact["rects"]
    fuzzy = pg._locate(str(path), 1.0, 1, "revenue increased by 12 percent compared to prior year")
    assert fuzzy["method"] == "fuzzy" and fuzzy["rects"]
    none = pg._locate(str(path), 1.0, 1, "completely unrelated sentence about satellites orbiting")
    assert none["method"] == "not_found"


def test_financial_table_rebuilt_from_word_positions(tmp_path):
    """Row labels, wrapped labels and a spanning header survive; numbers land in the right columns."""
    import fitz
    import sys
    sys.path.insert(0, "ingestion")
    from table_extractor import reconstruct_table_by_words

    d = fitz.open()
    page = d.new_page()
    page.insert_text((200, 60), "Three Months Ended", fontsize=9)
    page.insert_text((400, 75), "2025", fontsize=9)
    page.insert_text((470, 75), "2026", fontsize=9)
    rows = [("Net income", "28,196", "112,193"), ("Change in translation adjustment,", None, None),
            ("net of tax", "2,610", "(9)"), ("Comprehensive income", "30,155", "112,088")]
    y = 100
    for label, a, b in rows:
        page.insert_text((40, y), label, fontsize=9)
        if a:
            page.insert_text((440 - 6 * len(a), y), a, fontsize=9)
            page.insert_text((510 - 6 * len(b), y), b, fontsize=9)
        y += 16
    built = reconstruct_table_by_words(page, (40, 90, 520, y), 0.0, 1)
    assert built is not None
    table, _ = built
    assert table["rows"][0][0] == "Net income" and table["rows"][0][1:] == ["28,196", "112,193"]
    assert table["rows"][1][0] == "Change in translation adjustment, net of tax" and table["rows"][1][1:] == ["2,610", "(9)"]
    assert "2025" in table["headers"][1] and "2026" in table["headers"][2]


def _equity_style_page():
    import fitz
    d = fitz.open()
    page = d.new_page()
    page.insert_text((40, 40), "The following tables present fair values and gross unrealized gains and losses by category.", fontsize=9)
    page.insert_text((40, 70), "As of December 31, 2025", fontsize=9)
    page.insert_text((300, 85), "Cost", fontsize=9)
    page.insert_text((400, 85), "Fair Value", fontsize=9)
    page.insert_text((470, 85), "Gains", fontsize=9)
    y = 105
    for label, a, b, c in [("Time deposits", "3,353", "3,353", "0"), ("Government bonds", "49,087", "49,504", "443"),
                           ("Total investments", "85,123", "85,796", "859")]:
        page.insert_text((40, y), label, fontsize=9)
        for x_right, v in ((330, a), (440, b), (500, c)):
            page.insert_text((x_right - 5 * len(v), y), v, fontsize=9)
        y += 16
    return page


def test_totals_row_below_detected_edge_and_caption_title(tmp_path):
    import sys
    sys.path.insert(0, "ingestion")
    from table_extractor import reconstruct_table_by_words, _loses_numbers
    page = _equity_style_page()
    # find_tables' bottom edge stops above the last ("Total") row
    built = reconstruct_table_by_words(page, (40, 95, 520, 128), 0.0, 1)
    assert built is not None
    table, box = built
    assert table["rows"][-1][0] == "Total investments" and table["rows"][-1][1:] == ["85,123", "85,796", "859"]
    assert "December 31, 2025" in table["title"] and "fair values" not in table["title"]  # caption yes, paragraph no
    assert not _loses_numbers(page, table, box)
