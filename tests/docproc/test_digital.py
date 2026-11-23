"""Digital (text-layer) pages are read by code and verified against the page; a model steps in only when code cannot
prove it read the page completely. Offline; fixtures are generated, generic tables."""
import asyncio
import json
from pathlib import Path

import pymupdf as fitz
import pytest

from app.docproc.services.extraction import digital
from app.docproc.services.reading import ocr, pdf_pages, router
from app.docproc import pipeline
from tests.docproc.api_stub import H, client, mk, up, wait  # noqa: F401  (client is a fixture)

SAMPLE = Path(__file__).resolve().parents[2] / "sample_data" / "MSFT_10-Q_2026-04-29_000119312526191507.pdf"


def _table_pdf(path, rows, title="QUARTERLY RESULTS"):
    d = fitz.open()
    p = d.new_page(width=612, height=792)
    p.insert_text((50, 60), title, fontsize=14)
    for i, r in enumerate(rows):
        for x, t in zip((50, 300, 400), r):
            p.insert_text((x, 100 + 20 * i), t, fontsize=11)
    d.save(path)


ROWS = [("Item", "2026", "2025"), ("Revenue", "1,200", "1,000"), ("Costs", "(300)", "(250)"), ("Net income", "900", "750"), ("Margin", "75%", "75%")]


def _read(path, **kw):
    info = pdf_pages.analyze_page(path, 1)
    lines = [l for l in info["text"].splitlines() if l.strip()]
    return asyncio.run(digital.read_page(path, 1, lines, info["tables"], **kw)), info


def test_a_digital_table_is_copied_exactly_with_no_model(tmp_path):
    pdf = str(tmp_path / "t.pdf")
    _table_pdf(pdf, ROWS)
    res, _ = _read(pdf)
    (key, rows), = res["tables"].items()
    assert key == "quarterly_results" and res["readers"] == ["text-layer"] and not res["unverified"]
    cells = {r["item"]["value"]: (r["2026"]["value"], r["2025"]["value"]) for r in rows}
    assert cells["Costs"] == ("(300)", "(250)") and cells["Net income"] == ("900", "750")      # signs and parentheses kept verbatim
    assert all(c["status"] == "agreed" and c["method"] == "text-layer" for r in rows for c in r.values() if c["value"])


def test_coverage_notices_a_number_the_table_lost(tmp_path):
    pdf = str(tmp_path / "t.pdf")
    _table_pdf(pdf, ROWS)
    info = pdf_pages.analyze_page(pdf, 1)
    t = info["tables"][0]
    full, _ = digital.coverage(pdf, 1, t["bbox"], t["rows"])
    cut = [r[:] for r in t["rows"]]
    cut[1][1] = ""                                                    # drop "1,200"
    partial, missing = digital.coverage(pdf, 1, t["bbox"], cut)
    assert full == 1.0 and partial < digital.COVERAGE_OK and missing == ["1200"]


def test_an_incomplete_table_is_flagged_and_escalates(tmp_path, monkeypatch):
    pdf = str(tmp_path / "t.pdf")
    _table_pdf(pdf, ROWS)
    info = pdf_pages.analyze_page(pdf, 1)
    broken = [{**info["tables"][0], "rows": [[("" if c == "1,200" else c) for c in r] for r in info["tables"][0]["rows"]]}]
    lines = [l for l in info["text"].splitlines() if l.strip()]
    monkeypatch.setenv("DISCOVER_DIGITAL_MODEL", "auto")
    res = asyncio.run(digital.read_page(pdf, 1, lines, broken))
    assert res["unverified"] and any(c["status"] == "review" and "not captured" in c["reason"]
                                     for rows in res["tables"].values() for r in rows for c in r.values())
    assert digital.needs_model(lines, res) is True


def test_header_spanning_rows_uses_one_cheap_call_per_distinct_header():
    table = {"rows": [["", "Three", "Months Ended"], ["", "", "March 31,"], ["", "2026", "2025"], ["Revenue", "10", "9"]]}
    header, body = digital.split_header(table)
    assert len(header) == 3 and body == [["Revenue", "10", "9"]]
    calls = []

    async def ask(model, prompt):
        calls.append(prompt)
        return json.dumps({"columns": ["Three Months Ended March 31, 2026", "Three Months Ended March 31, 2025"]})
    memo = {}
    got = [asyncio.run(digital.header_labels(header, 3, "m", ask, memo)) for _ in range(3)]
    assert got[0] == ["Item", "Three Months Ended March 31, 2026", "Three Months Ended March 31, 2025"] and len(calls) == 1


def test_header_labels_degrade_without_a_model_or_on_a_bad_reply():
    header = [["", "Three", "Months Ended"], ["", "2026", "2025"]]
    assert asyncio.run(digital.header_labels(header, 3, None, None, {})) == ["Item", "Three 2026", "Months Ended 2025"]

    async def bad(model, prompt):
        return '{"columns": ["only one"]}'
    assert asyncio.run(digital.header_labels(header, 3, "m", bad, {}))[1] == "Three 2026"          # wrong count: raw fallback
    one_row = [["", "2026", "2025"]]

    async def never(*a):
        raise AssertionError("a single-row header needs no model")
    assert asyncio.run(digital.header_labels(one_row, 3, "m", never, {})) == ["Item", "2026", "2025"]


def test_model_gate(monkeypatch):
    monkeypatch.setenv("DISCOVER_DIGITAL_MODEL", "auto")
    prose = ["This is a long sentence of ordinary running text that goes on and on for quite a while."] * 10
    form = ["Name", "Address", "City", "State", "Phone", "Email", "Notes"]
    empty = {"tables": {}, "fields": {}}
    assert digital.needs_model(prose, empty) is False                       # a paragraph has no fields to extract
    assert digital.needs_model(form, empty) is True                         # form-like, code found nothing
    assert digital.needs_model(form, {"tables": {"t": []}, "fields": {}}) is False
    assert digital.needs_model(form, {"tables": {}, "fields": {}, "unverified": ["x"]}) is True
    monkeypatch.setenv("DISCOVER_DIGITAL_MODEL", "never")
    assert digital.needs_model(form, empty) is False
    monkeypatch.setenv("DISCOVER_DIGITAL_MODEL", "always")
    assert digital.needs_model(prose, empty) is True


def test_router_reads_all_digital_documents_without_a_model():
    got = router.decide_extractor("unknown", llm_available=False, all_digital=True)
    assert got["extractor"] == "discover"
    assert router.decide_extractor("unknown", llm_available=False, all_digital=False)["extractor"] == "kv"


# ---- unreadable text layers -----------------------------------------------------------------------------------------

def test_garbled_text_layers_are_recognised_but_real_text_and_number_tables_are_not():
    assert ocr.text_is_garbled("(cid:12)(cid:45)(cid:3)(cid:99) " * 12)
    assert ocr.text_is_garbled("��□■ " * 30)
    assert not ocr.text_is_garbled("Revenue 1,200 1,000 (300) (250) 75% 75% 900 750 12.5 33.1 " * 3)
    assert not ocr.text_is_garbled("The quick brown fox jumps over the lazy dog. " * 5)
    assert not ocr.text_is_garbled("short")


def test_a_page_with_a_garbled_text_layer_is_treated_as_a_scan(tmp_path):
    pdf = str(tmp_path / "g.pdf")
    d = fitz.open()
    d.new_page(width=612, height=792).insert_text((50, 60), "(cid:12)(cid:45)(cid:3)(cid:99) " * 6, fontsize=8)
    d.save(pdf)
    assert 1 in pdf_pages.scanned_pages(pdf)
    assert pdf_pages.analyze_page(pdf, 1)["scanned"] is True


# ---- a long untyped document does not cost a classifier call per page ------------------------------------------------

def test_classifier_gives_up_after_a_streak_of_unknowns(tmp_path):
    pdf = str(tmp_path / "long.pdf")
    d = fitz.open()
    for i in range(10):
        d.new_page(width=612, height=792).insert_text((50, 60), f"Some free-form paragraph number {i} with ordinary words in it.", fontsize=11)
    d.save(pdf)
    calls = []

    async def ask(model, prompt):
        calls.append(1)
        return '{"type": "unknown", "title": null, "continues_previous": true}'

    async def ocr_unused(model, jpeg):
        raise AssertionError("digital pages are not read by OCR")
    asyncio.run(pipeline.process(pdf, ocr_reader=ocr_unused, model_ask=ask))
    assert len(calls) == 3


# ---- the real thing (skipped when the sample is not present) ----------------------------------------------------------

@pytest.mark.skipif(not SAMPLE.exists(), reason="sample filing not present")
def test_financial_statements_are_read_exactly_by_code_alone(tmp_path):
    pdf = str(tmp_path / "fs.pdf")
    src, out = fitz.open(str(SAMPLE)), fitz.open()
    for n in (3, 5):                                                       # income statement, balance sheet
        out.insert_pdf(src, from_page=n - 1, to_page=n - 1)
    out.save(pdf)
    r = asyncio.run(pipeline.process(pdf))                                 # LLM_ENABLED=0 in tests: no model could be called
    form = r["forms"][0]
    assert form["discovered"] and form["readers"] == ["text-layer"]
    by_label = {}
    for tn, rows in form["tables"].items():
        for row in rows:
            first = next(iter(row.values()))["value"]
            by_label.setdefault((tn, first), [c["value"] for c in list(row.values())[1:]])
    assert by_label[("income_statements", "Total revenue")] == ["82,886", "70,066", "241,832", "205,283"]
    assert by_label[("balance_sheets", "Cash and cash equivalents")] == ["$32,105", "$30,242"]
    assert form["table_columns"]["income_statements"][1] == "Three Months Ended March 31, 2026" or form["table_columns"]["income_statements"][1].startswith("Three")
    assert not any(c["status"] == "review" for rows in form["tables"].values() for row in rows for c in row.values())


# ---- what the Documents page shows ---------------------------------------------------------------------------------

def test_summary_says_how_the_file_was_read_and_what_it_cost(client, tmp_path):
    pdf = tmp_path / "digital.pdf"
    _table_pdf(str(pdf), ROWS)
    wid = mk(client)
    eid = up(client, wid, pdf).json()["id"]
    got = wait(client, wid, eid)
    stats = got["stats"]
    assert stats["digital_pages"] == 1 and stats["scanned_pages"] == 0 and stats["handwritten_pages"] == 0
    assert stats["usage"]["calls"] == 0 and stats["usage"]["tokens"] == 0          # read entirely by code: nothing was spent
    assert [l.title() for l in got["labels"]] == ["Quarterly Results"]       # named as the page prints its title
    listed = client.get(f"/api/workspaces/{wid}/extractions", headers=H).json()
    assert [l.title() for l in listed[0]["labels"]] == ["Quarterly Results"] and listed[0]["stats"]["usage"]["calls"] == 0


def test_a_documents_title_prefers_a_form_name_over_a_letterhead():
    assert digital.page_title(["UNITED STATES", "SECURITIES AND EXCHANGE COMMISSION", "Washington, D.C. 20549", "FORM 10-Q"]) == "FORM 10-Q"
    assert digital.page_title(["ACME CORPORATION", "INVOICE", "Invoice No: 7"]) == "Acme Corporation"      # no form name: the longest heading
    assert digital.page_title(["just some prose that is mixed case and has no heading at all"]) is None


def test_table_titles_skip_running_headers_and_never_guess_a_footer():
    lines = ["PART I", "Item 1", "BALANCE SHEETS", "(In millions)", "Assets", "Cash 5", "SIGNATURE"]
    assert digital.table_title(lines, "Assets") == "Balance Sheets"                  # the section marker is skipped, the real heading is used
    assert digital.table_title(["PART I", "Item 1", "Cash 5", "SIGNATURE"], "Unfound label") is None   # cannot place the table: no title, not "Signature"
