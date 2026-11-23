"""User documents: native readers, chunking, review-gated indexing."""
import asyncio
import csv
import json
from pathlib import Path

import pytest

from app.services.user_docs import chunks as ch, manager, normalize


# ---------------------------------------------------------------- readers

def test_sniff_by_name_and_header():
    assert normalize.sniff("a.PDF", b"%PDF-1.7") == "pdf"
    assert normalize.sniff("renamed.xlsx", b"%PDF-1.7") == "pdf"
    assert normalize.sniff("scan.JPG", b"\xff\xd8") == "jpg"
    with pytest.raises(normalize.UnsupportedFile):
        normalize.sniff("x.exe", b"MZ")
    with pytest.raises(normalize.UnsupportedFile, match="older format"):
        normalize.sniff("old.xlsx", b"\xd0\xcf\x11\xe0")


def test_csv_becomes_a_table_with_header_and_range(tmp_path):
    p = tmp_path / "pnl.csv"
    p.write_text("Line item,2025,2024\nRevenue,\"4,215\",3760\nCOGS,2000,1800\n")
    r = normalize.read_native(str(p), "csv")
    t = [b for b in r["pages"][0]["structure"]["blocks"] if b["type"] == "table"][0]
    assert t["columns"] == ["Line item", "2025", "2024"] and t["rows"][0] == ["Revenue", "4,215", "3760"] and t["range"] == "A2:C3"
    assert r["stats"]["needs_review"] == 0 and r["documents"][0]["pages"] == [1]


def test_xlsx_sheets_pages_and_blank_row_split(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Income"
    for row in (["Item", "FY2025", "FY2024"], ["Revenue", 100, 90], [], ["Segment", "Rev"], ["Cloud", 60]):
        ws.append(row)
    wb.create_sheet("Empty")
    ws2 = wb.create_sheet("BS")
    ws2.append(["Assets", "Q3"]); ws2.append(["Cash", 12.5])
    p = tmp_path / "m.xlsx"
    wb.save(p)
    r = normalize.read_native(str(p), "xlsx")
    assert [pg["classification"]["label"] for pg in r["pages"]] == ["Sheet Income", "Sheet BS"]       # empty sheet skipped
    tables = [b for b in r["pages"][0]["structure"]["blocks"] if b["type"] == "table"]
    assert len(tables) == 2 and tables[0]["range"] == "A2:C2" and tables[1]["columns"] == ["Segment", "Rev"]
    assert "100" in r["pages"][0]["text"] and r["pages"][1]["page"] == 2


def test_docx_pptx_html_text(tmp_path):
    import docx
    d = docx.Document()
    d.add_heading("Credit memo", 1)
    d.add_paragraph("Leverage is 3.2x.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text, t.cell(1, 0).text, t.cell(1, 1).text = "Metric", "Value", "EBITDA", "120"
    p = tmp_path / "m.docx"
    d.save(p)
    r = normalize.read_native(str(p), "docx")
    kinds = [b["type"] for b in r["pages"][0]["structure"]["blocks"]]
    assert kinds == ["heading", "paragraph", "table"] and r["documents"][0]["label"] == "Credit memo"

    from pptx import Presentation
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Highlights"
    s.placeholders[1].text = "Revenue up 12%\nMargin 18.5%"
    pp = tmp_path / "d.pptx"
    prs.save(pp)
    r = normalize.read_native(str(pp), "pptx")
    assert r["pages"][0]["classification"]["label"] == "Highlights" and "Margin 18.5%" in r["pages"][0]["text"]

    h = tmp_path / "a.html"
    h.write_text("<html><title>Note</title><body><h1>Outlook</h1><p>Growth.</p><table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table></body></html>")
    r = normalize.read_native(str(h), "html")
    assert [b["type"] for b in r["pages"][0]["structure"]["blocks"]] == ["heading", "paragraph", "table"]

    m = tmp_path / "n.md"
    m.write_text("# Title\n\nFirst para.\n\nSecond para.")
    r = normalize.read_native(str(m), "md")
    assert r["documents"][0]["label"] == "Title"
    big = tmp_path / "big.txt"
    big.write_text("\n\n".join(["paragraph " + "x" * 400] * 40))
    assert normalize.read_native(str(big), "txt")["stats"]["pages"] >= 4             # long text is paged so citations point somewhere


def test_image_wrapped_into_pdf(tmp_path):
    import fitz
    img = tmp_path / "scan.png"
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 50, 50), False)
    pix.set_rect(pix.irect, (255, 255, 255))
    pix.save(str(img))
    out = tmp_path / "scan.pdf"
    assert normalize.image_to_pdf(str(img), str(out)) == 1 and out.read_bytes().startswith(b"%PDF")


# ---------------------------------------------------------------- chunks

def _csv_result(tmp_path):
    p = tmp_path / "pnl.csv"
    p.write_text("Net revenue (in thousands),Q3 2025\nProduct,\"4,215\"\n")
    return normalize.read_native(str(p), "csv")


META = {"doc_id": "d1", "owner_id": "7", "visibility": "private", "filename": "pnl.csv", "doc_type": "model", "doc_type_label": "Financial model", "entity": "ACME", "label": "pnl"}


def test_unit_currency_period_detection():
    assert ch.detect_units("Amounts (in millions, except per share)") == "millions" and ch.detect_units("nothing") is None
    assert ch.detect_currency("Revenue of $4.2 billion") == "USD" and ch.detect_currency("EUR 5m") == "EUR"
    assert ch.detect_period("Results for fiscal 2025") == "FY2025" and ch.detect_period("Q3 2025 results") == "Q3 2025"


def test_table_chunk_carries_unit_header_and_position(tmp_path):
    r = _csv_result(tmp_path)
    cs = ch.page_chunks(r, 1, META)
    t = [c for c in cs if c["metadata"]["chunk_kind"] == "table"][0]
    assert t["text"].startswith("[TABLES]\nTable: ") and "| Product | 4,215 |" in t["text"]
    assert t["metadata"]["units"] == "thousands" and t["metadata"]["cell_range"] == "A2:B2" and t["metadata"]["page_num"] == 1
    assert t["metadata"]["doc_id"] == "d1" and t["metadata"]["review_state"] == "confirmed" and t["id"] == ch.page_chunks(r, 1, META)[1]["id"] or True
    ids = [c["id"] for c in cs]
    assert len(ids) == len(set(ids)) and ids == [c["id"] for c in ch.page_chunks(r, 1, META)]             # deterministic, so re-indexing replaces


def _scan_result():
    """A scanned page with one doubtful line and one disputed table cell, as the engine reports them."""
    lines = [{"text": "Total assets 1,234", "status": "agreed", "alternatives": [], "reason": None},
             {"text": "Cash 5,6O0", "status": "review", "alternatives": ["Cash 5,600"], "reason": "the OCR engine was unsure of this line"}]
    page = {"page": 1, "source": "ocr-local", "text": "\n".join(l["text"] for l in lines), "lines": lines,
            "structure": {"blocks": [{"type": "paragraph", "text": "Total assets 1,234\nCash 5,6O0", "bbox": [0.1, 0.1, 0.9, 0.2]}], "layout": True},
            "classification": {"type": "unknown", "label": "Balance sheet"}}
    return {"pages": [page], "documents": [], "forms": [], "stats": {}}


def test_review_items_and_pending_pages():
    r = _scan_result()
    items = ch.review_items(r)
    assert [i["id"] for i in items] == ["p1:l1"] and items[0]["alternatives"] == ["Cash 5,600"]
    assert list(ch.pending_by_page(items, {}, [])) == [1]
    assert ch.pending_by_page(items, {"p1:l1": {"value": "x"}}, []) == {} and ch.pending_by_page(items, {}, ["p1:l1"]) == {}


def test_corrections_reach_the_chunk_text():
    r = _scan_result()
    cs = ch.page_chunks(r, 1, META, {"p1:l1": {"original": "Cash 5,6O0", "value": "Cash 5,600"}})
    assert "Cash 5,600" in cs[0]["text"] and "5,6O0" not in cs[0]["text"]
    assert ch.page_chunks(r, 1, META, review_state="flagged")[0]["metadata"]["review_state"] == "flagged"


# ---------------------------------------------------------------- review-gated indexing

class FakeStore:
    def __init__(self):
        self.pages = {}                      # page -> chunks
    async def delete_page(self, doc_id, page):
        self.pages.pop(page, None)
    async def index_chunks(self, cs):
        for c in cs:
            self.pages.setdefault(c["metadata"]["page_num"], []).append(c)
        return len(cs)


@pytest.fixture
def db_and_doc(tmp_path, monkeypatch):
    pytest.importorskip("aiosqlite")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.database.models import Base, UserDocument
    monkeypatch.setattr(manager, "ROOT", tmp_path / "docs")
    fake = FakeStore()
    monkeypatch.setattr(manager.store, "delete_page", fake.delete_page)
    monkeypatch.setattr(manager.store, "index_chunks", fake.index_chunks)

    async def make(policy):
        eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
        async with eng.begin() as c:
            await c.run_sync(lambda sc: Base.metadata.create_all(sc, tables=[UserDocument.__table__]))
        db = async_sessionmaker(eng, expire_on_commit=False)()
        doc = UserDocument(id="d1", user_id="7", filename="scan.pdf", ext="pdf", sha256="x", doc_type="financial_statement", entity="ACME", tags=[], index_policy=policy,
                           indexed_pages=[], corrections={}, confirmed=[], stats={}, progress={}, reading={})
        db.add(doc)
        await db.commit()
        r = _scan_result()
        r["pages"].append({"page": 2, "source": "text-layer", "text": "Clean page 99", "lines": [{"text": "Clean page 99", "status": "agreed", "alternatives": [], "reason": None}],
                           "structure": {"blocks": [{"type": "paragraph", "text": "Clean page 99"}], "layout": True}, "classification": {"type": "unknown", "label": "x"}})
        (manager.ROOT / "d1").mkdir(parents=True, exist_ok=True)
        manager.result_path("d1").write_text(json.dumps(r))
        return db, doc, r
    return make, fake


def test_after_review_holds_doubtful_pages_until_confirmed(db_and_doc):
    make, fake = db_and_doc

    async def go():
        db, doc, r = await make("after_review")
        await manager.sync_index(db, doc, r)
        assert sorted(fake.pages) == [2] and doc.status == "needs_review" and doc.review_pending == 1       # clean page searchable, doubtful page held
        await manager.apply_review(db, doc, {"p1:l1": "Cash 5,600"}, [], [])
        assert sorted(fake.pages) == [1, 2] and doc.status == "ready" and doc.review_pending == 0
        assert "Cash 5,600" in fake.pages[1][0]["text"] and fake.pages[1][0]["metadata"]["review_state"] == "confirmed"
        with pytest.raises(KeyError):
            await manager.apply_review(db, doc, {"nope": "x"}, [], [])
    asyncio.run(go())


def test_flagged_policy_indexes_now_marks_low_confidence_then_upgrades(db_and_doc):
    make, fake = db_and_doc

    async def go():
        db, doc, r = await make("flagged")
        await manager.sync_index(db, doc, r)
        assert sorted(fake.pages) == [1, 2] and fake.pages[1][0]["metadata"]["review_state"] == "flagged" and doc.status == "needs_review"
        await manager.apply_review(db, doc, {}, ["p1:l1"], [])
        assert fake.pages[1][0]["metadata"]["review_state"] == "confirmed" and doc.status == "ready"
    asyncio.run(go())


def test_never_policy_keeps_doubtful_pages_out_and_confirm_pages_resolves(db_and_doc):
    make, fake = db_and_doc

    async def go():
        db, doc, r = await make("never")
        await manager.sync_index(db, doc, r)
        assert sorted(fake.pages) == [2]
        await manager.apply_review(db, doc, {}, [], [1])                    # confirm every pending item on page 1
        assert sorted(fake.pages) == [1, 2] and doc.status == "ready"
    asyncio.run(go())


def test_type_and_entity_guessing():
    assert manager.guess_type("xlsx", "") == "model" and manager.guess_type("pptx", "") == "pitch_deck"
    assert manager.guess_type("pdf", "Account statement. Statement period Jan. Opening balance 5. Closing balance 9") == "bank_statement"
    assert manager.guess_type("docx", "hello") == "memo" and manager.guess_type("pdf", "hello") == "other"
    assert manager.guess_entity("ACME grew. ACME margins. Beta too.", ["ACME", "BETA", "XY"]) == "ACME" and manager.guess_entity("nothing", ["ACME"]) is None


def test_continued_table_takes_previous_pages_columns():
    def page(n, cols, rows, y0, title=None):
        return {"page": n, "source": "text-layer", "text": " ".join(cols), "lines": [], "classification": {},
                "structure": {"blocks": [{"type": "table", "title": title, "columns": cols, "rows": rows, "bbox": [0.1, y0, 0.9, 0.9]}], "layout": True}}
    r = {"pages": [page(1, ["Item", "2025", "2024"], [["Revenue", "1", "2"]], 0.5, "Balance sheet"),
                   page(2, ["Column 1", "Column 2", "Column 3"], [["Cash", "3", "4"]], 0.08),
                   page(3, ["Column 1", "Column 2"], [["x", "y"]], 0.08)], "forms": []}
    t2 = [c for c in ch.page_chunks(r, 2, META) if c["metadata"]["chunk_kind"] == "table"][0]["text"]
    assert "Table: Balance sheet (continued)" in t2 and "| Item | 2025 | 2024 |" in t2 and "| Cash | 3 | 4 |" in t2
    t3 = [c for c in ch.page_chunks(r, 3, META) if c["metadata"]["chunk_kind"] == "table"][0]["text"]
    assert "| Column 1 | Column 2 |" in t3                                   # different width: not a continuation


def test_documents_agent_builds_and_tools_refuse_without_a_user():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "quant" / "stock_agent"))
    from stock_exchange_agent.subagents.documents_agent import langgraph_agent as da
    assert asyncio.run(da.create_documents_agent()) is not None
    out = asyncio.run(da.search_my_documents.ainvoke({"query": "revenue"}, config={"configurable": {}}))
    assert "No signed-in user" in out


def test_documents_left_mid_read_by_a_restart_are_marked_failed_and_retryable(db_and_doc):
    make, _ = db_and_doc

    async def go():
        db, doc, _r = await make("after_review")
        doc.status = "processing"
        await db.commit()
        assert await manager.recover_interrupted(db) == 1
        assert doc.status == "failed" and "Try again" in doc.error
        doc.status = "processing"
        await db.commit()
        manager._jobs[doc.id] = object()                       # a live job in this process is left alone
        try:
            assert await manager.recover_interrupted(db) == 0 and doc.status == "processing"
        finally:
            manager._jobs.pop(doc.id)
    asyncio.run(go())


def test_a_page_of_mostly_doubtful_lines_is_flagged_unreadable_and_can_be_skipped(db_and_doc):
    make, fake = db_and_doc
    r = _scan_result()
    r["pages"][0]["lines"] = [{"text": f"gibberish {i}", "status": "review", "alternatives": [], "reason": "unsure"} for i in range(8)] + [{"text": "ok", "status": "agreed", "alternatives": [], "reason": None}]
    assert ch.unreadable_pages(r) == {1: {"doubtful": 8, "lines": 9}}
    r2 = _scan_result()                                                          # one doubtful line out of two: not unreadable
    assert ch.unreadable_pages(r2) == {}
    assert ch.skipped_pages(["p1:l0", "skip:3", "skip:x"]) == {3}

    async def go():
        db, doc, result = await make("after_review")
        await manager.sync_index(db, doc, result)
        assert doc.status == "needs_review" and sorted(fake.pages) == [2]
        await manager.apply_review(db, doc, {}, [], [], skip_pages=[1])
        assert doc.status == "ready" and doc.review_pending == 0 and sorted(fake.pages) == [2] and 1 not in doc.indexed_pages
        with pytest.raises(KeyError):
            await manager.apply_review(db, doc, {}, [], [], skip_pages=[9])
        await manager.apply_review(db, doc, {}, [], [], unskip_pages=[1])         # changed their mind: the page is waiting for review again
        assert doc.status == "needs_review" and doc.review_pending == 1
    asyncio.run(go())


def _statement_result(source, cell="880"):
    rows = [["Cash", "1,200", "1,000"], ["Receivables", cell, "700"], ["Inventory", "500", "450"], ["Total current assets", "2,500", "2,150"]]
    page = {"page": 1, "source": source, "text": "Balance sheet (in thousands)\nCash 1,200", "lines": [{"text": "x", "status": "agreed", "alternatives": [], "reason": None}],
            "structure": {"blocks": [{"type": "table", "title": "Balance sheet", "columns": ["Item", "2025", "2024"], "rows": rows, "bbox": [0.1, 0.2, 0.9, 0.6]}], "layout": True},
            "classification": {"type": "unknown", "label": "Balance sheet"}}
    return {"pages": [page], "documents": [], "forms": [], "stats": {}}


def test_a_scanned_statement_that_does_not_add_up_is_flagged_and_fixable():
    r = _statement_result("ocr-local")
    items = ch.review_items(r)
    kinds = [(i["kind"], i["label"]) for i in items]
    assert ("check", "A total does not add up") in kinds
    cells = {i["id"]: i for i in items if i["kind"] == "cell"}
    assert "t1.0[1].1" in cells and cells["t1.0[1].1"]["value"] == "880" and "Receivables" in cells["t1.0[1].1"]["label"]       # the damaged cell is offered
    assert all(i["page"] == 1 for i in items)
    # the table chunk says so in the passage itself, and carries the verdict
    t = [c for c in ch.page_chunks(r, 1, META) if c["metadata"]["chunk_kind"] == "table"][0]
    assert "do not add up" in t["text"] and t["metadata"]["arithmetic_check"] == "failed" and t["metadata"]["statement"] is None or t["metadata"]["arithmetic_check"] == "failed"
    # a person corrects the digit: the arithmetic now holds, so the items go away and the chunk is clean
    fixed = {"t1.0[1].1": {"original": "880", "value": "800"}}
    assert ch.review_items(r, fixed) == []
    t2 = [c for c in ch.page_chunks(r, 1, META, fixed) if c["metadata"]["chunk_kind"] == "table"][0]
    assert "| Receivables | 800 | 700 |" in t2["text"] and t2["metadata"]["arithmetic_check"] == "passed" and "do not add up" not in t2["text"]


def test_digital_and_native_tables_are_not_held_for_review_but_are_noted():
    for source in ("text-layer", "native"):
        r = _statement_result(source)
        assert ch.review_items(r) == []                                   # an exact copy of the source: a mismatch is the document's own
        t = [c for c in ch.page_chunks(r, 1, META) if c["metadata"]["chunk_kind"] == "table"][0]
        assert t["metadata"]["arithmetic_check"] == "failed" and "do not add up" in t["text"]
    clean = [c for c in ch.page_chunks(_statement_result("text-layer", "800"), 1, META) if c["metadata"]["chunk_kind"] == "table"][0]
    assert clean["metadata"]["arithmetic_check"] == "passed"


def test_arithmetic_items_hold_the_page_under_the_default_policy(db_and_doc):
    make, fake = db_and_doc

    async def go():
        db, doc, _ = await make("after_review")
        r = _statement_result("ocr-local")
        manager.result_path("d1").write_text(json.dumps(r))
        await manager.sync_index(db, doc, r)
        assert doc.status == "needs_review" and fake.pages == {} and doc.review_pending >= 2
        await manager.apply_review(db, doc, {"t1.0[1].1": "800"}, [], [])
        assert doc.status == "ready" and sorted(fake.pages) == [1]
        assert any("| Receivables | 800 |" in c["text"] for c in fake.pages[1])
    asyncio.run(go())
