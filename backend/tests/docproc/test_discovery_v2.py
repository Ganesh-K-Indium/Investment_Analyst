"""Template-free extraction: stable keys, two-pass reading, text mode for digital pages, long documents, multi-page merge,
recurring-shape suggestions, cache versioning. Offline; data is generic."""
import asyncio

import pymupdf as fitz
import pytest

from app.docproc.services.extraction import discover, labels
from app.docproc.services.reading import pdf_pages
from app.docproc.services.documents import suggest


# ---- stable keys ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("variants,key", [
    (["Invoice No.", "Inv #", "Invoice Number", "invoice no"], "invoice_number"),
    (["Phone", "Telephone", "Phone Number", "Tel."], "phone"),
    (["ZIP", "Zip Code", "Postal Code"], "zip"),
    (["DOB", "Date of Birth", "Birth Date"], "date_of_birth"),
    (["Qty", "Quantity"], "quantity"),
])
def test_label_variants_share_one_key(variants, key):
    assert {labels.key(v) for v in variants} == {key}


def test_different_things_stay_different_keys():
    assert labels.key("Total Due") != labels.key("Amount Due")
    assert labels.key("First Name") != labels.key("Last Name")
    assert labels.key("") == "field" and labels.key("???", "col_1") == "col_1"


def test_section_only_joins_the_key_when_the_label_repeats():
    items = discover.parse({"fields": [{"label": "Tax ID", "value": "1", "section": "Provider"}, {"label": "Tax ID", "value": "2", "section": "Facility"},
                                       {"label": "Name", "value": "x", "section": "Provider"}]})["fields"]
    assert discover._field_keys(items) == ["provider.tax_id", "facility.tax_id", "name"]
    moved = discover.parse({"fields": [{"label": "Name", "value": "x", "section": "Somewhere else"}]})["fields"]
    assert discover._field_keys(moved) == ["name"]                         # same key whatever heading the reader used


# ---- two-pass reading -----------------------------------------------------------------------------------------------

def _page_pdf(path, lines):
    d = fitz.open()
    p = d.new_page(width=612, height=792)
    for i, t in enumerate(lines):
        p.insert_text((50, 60 + 20 * i), t, fontsize=11)
    d.save(path)


def test_second_reader_gets_labels_not_values_and_pairs_exactly(tmp_path):
    pdf = str(tmp_path / "a.pdf")
    _page_pdf(pdf, ["Widget order", "Order ref: 7731", "Weight: 4.5 kg"])
    prompts = {}

    async def reader(model, jpeg, prompt):
        prompts[model] = prompt
        label_b = "Reference" if model.endswith("mini") else "Order ref"       # the second reader would name it differently...
        return {"title": "Widget order", "fields": [{"label": "Order ref" if "ITEMS ALREADY" in prompt else label_b, "value": "7731"},
                                                    {"label": "Weight", "value": "4.5 kg"}]}
    res = asyncio.run(discover.discover_page(pdf, 1, ["m-big", "m-mini"], reader))
    assert "Order ref" in prompts["m-mini"] and "7731" not in prompts["m-mini"].split("ITEMS ALREADY LISTED:")[1]
    assert "ITEMS ALREADY" not in prompts["m-big"]
    assert [c["status"] for c in res["fields"].values()] == ["agreed", "agreed"]


def test_a_failed_first_reader_hands_the_lead_to_the_second(tmp_path):
    pdf = str(tmp_path / "a.pdf")
    _page_pdf(pdf, ["Order ref: 7731"])

    async def reader(model, jpeg, prompt):
        if model == "m-big":
            raise RuntimeError("down")
        assert "ITEMS ALREADY" not in prompt
        return {"fields": [{"label": "Order ref", "value": "7731"}]}
    res = asyncio.run(discover.discover_page(pdf, 1, ["m-big", "m-mini"], reader))
    assert res["fields"]["order_reference"]["value"] == "7731" and res["fields"]["order_reference"]["status"] == "single"
    assert res["readers"] == ["m-mini"]


# ---- text mode (digital pages) ---------------------------------------------------------------------------------------

def test_digital_page_is_read_from_text_with_no_image(tmp_path):
    pdf = str(tmp_path / "a.pdf")
    _page_pdf(pdf, ["Order ref: 7731"])
    seen = []

    async def reader(model, jpeg, prompt):
        seen.append((jpeg, "PAGE TEXT:" in prompt and "Order ref" in prompt))
        return {"fields": [{"label": "Order ref", "value": "7731"}]}
    res = asyncio.run(discover.discover_page(pdf, 1, ["a", "b"], reader, page_text="Order ref | 7731"))
    assert seen == [(None, True), (None, True)] and res["fields"]["order_reference"]["status"] == "agreed"
    assert res["fields"]["order_reference"]["bbox"] is not None                   # still highlighted: the page has a text layer


def test_layout_text_keeps_table_rows(tmp_path):
    pdf = str(tmp_path / "t.pdf")
    d = fitz.open()
    p = d.new_page(width=612, height=792)
    for x, t in [(50, "Revenue"), (300, "70,066"), (420, "61,858")]:
        p.insert_text((x, 100), t, fontsize=11)
    for x, t in [(50, "Net"), (80, "income"), (300, "(1,234)"), (420, "5")]:
        p.insert_text((x, 120), t, fontsize=11)
    d.save(pdf)
    assert pdf_pages.layout_text(pdf, 1).splitlines() == ["Revenue | 70,066 | 61,858", "Net income | (1,234) | 5"]


def test_long_digital_documents_use_one_reader_per_page(tmp_path, monkeypatch):
    pdf = str(tmp_path / "long.pdf")
    d = fitz.open()
    for i in range(4):
        d.new_page(width=612, height=792).insert_text((50, 60), f"Item {i}: {i}", fontsize=11)
    d.save(pdf)
    monkeypatch.setenv("DISCOVER_LONG_DOC_PAGES", "3")
    calls = []

    async def reader(model, jpeg, prompt):
        calls.append(model)
        return {"fields": []}
    asyncio.run(discover.discover_document(pdf, [1, 2, 3, 4], ["big", "mini"], reader, text_pages={n: "x" for n in range(1, 5)}))
    assert calls.count("mini") == 0 and calls.count("big") == 4
    calls.clear()
    asyncio.run(discover.discover_document(pdf, [1, 2], ["big", "mini"], reader, text_pages={1: "x", 2: "x"}))     # short: both readers
    assert calls.count("mini") == 2


def test_page_reads_are_bounded(tmp_path):
    pdf = str(tmp_path / "many.pdf")
    d = fitz.open()
    for i in range(12):
        d.new_page(width=612, height=792).insert_text((50, 60), f"Row {i}", fontsize=11)
    d.save(pdf)
    live = peak = 0

    async def reader(model, jpeg, prompt):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1
        return {"fields": []}
    asyncio.run(discover.discover_document(pdf, list(range(1, 13)), ["big"], reader, text_pages={n: "x" for n in range(1, 13)}))
    assert peak <= discover.DISCOVER_CONCURRENCY


# ---- multi-page merge: repeated labels must not lose data -----------------------------------------------------------

def test_same_label_with_different_values_on_two_pages_keeps_both(tmp_path):
    pdf = str(tmp_path / "two.pdf")
    d = fitz.open()
    for t in ("Statement A", "Statement B"):
        d.new_page(width=612, height=792).insert_text((50, 60), t, fontsize=11)
    d.save(pdf)

    async def reader(model, jpeg, prompt):
        value = "100" if "Statement A" in prompt else "250"
        return {"fields": [{"label": "Total", "value": value}, {"label": "Currency", "value": "USD"}]}
    res = asyncio.run(discover.discover_document(pdf, [1, 2], ["big"], reader, text_pages={1: "Statement A", 2: "Statement B"}))
    assert {c["value"] for k, c in res["fields"].items() if k.startswith("total")} == {"100", "250"}
    assert [k for k in res["fields"] if k.startswith("currency")] == ["currency"]       # identical value: one field, not two


# ---- recurring shapes -----------------------------------------------------------------------------------------------

def _form(label, keys, idx=0, discovered=True):
    return {"doc_index": idx, "label": label, "discovered": discovered, "fields": {k: {"type": "text"} for k in keys}, "table_columns": {}}


def test_recurring_shapes_are_suggested_once_they_repeat():
    inv = ["invoice_number", "date", "customer", "total_due"]
    rows = [{"extraction_id": "e1", "filename": "a.pdf", "forms": [_form("Invoice", inv)]},
            {"extraction_id": "e2", "filename": "b.pdf", "forms": [_form("Invoice", inv + ["po_number"])]},      # one extra key: same shape
            {"extraction_id": "e3", "filename": "c.pdf", "forms": [_form("Site note", ["author", "weather", "crew"])]},   # only once
            {"extraction_id": "e4", "filename": "d.pdf", "forms": [_form("Invoice", inv, discovered=False)]}]       # templated: ignored
    out = suggest.recurring_shapes(rows)
    assert len(out) == 1 and out[0]["title"] == "Invoice" and out[0]["count"] == 2
    assert {"invoice_number", "po_number"} <= set(out[0]["fields"]) and out[0]["examples"][0]["extraction_id"] == "e1"
    assert suggest.recurring_shapes(rows, existing_titles=["invoice"]) == []          # already a saved type


def test_tiny_or_unrelated_shapes_are_not_suggested():
    rows = [{"extraction_id": f"e{i}", "filename": "x", "forms": [_form("Note", ["a", "b"])]} for i in range(5)]
    assert suggest.recurring_shapes(rows) == []
    rows = [{"extraction_id": "1", "filename": "x", "forms": [_form("A", ["a", "b", "c", "d"])]},
            {"extraction_id": "2", "filename": "x", "forms": [_form("B", ["w", "x", "y", "z"])]}]
    assert suggest.recurring_shapes(rows) == []


# ---- cache versioning -----------------------------------------------------------------------------------------------

def test_the_same_file_twice_or_a_long_filing_is_not_a_recurring_layout():
    inv = ["invoice_number", "date", "customer", "total_due"]
    same = [{"extraction_id": "e1", "filename": "a.pdf", "sha": "x", "forms": [_form("Invoice", inv)]},
            {"extraction_id": "e2", "filename": "a-again.pdf", "sha": "x", "forms": [_form("Invoice", inv)]}]
    assert suggest.recurring_shapes(same) == []                                    # identical bytes: one example, not two
    different = same[:1] + [{"extraction_id": "e3", "filename": "b.pdf", "sha": "y", "forms": [_form("Invoice", inv)]}]
    assert [s["count"] for s in suggest.recurring_shapes(different)] == [2]
    long_form = {**_form("Annual report", [f"k{i}" for i in range(20)]), "pages": list(range(1, 40))}
    assert suggest.recurring_shapes([{"extraction_id": str(i), "filename": "f", "sha": str(i), "forms": [long_form]} for i in range(3)]) == []


def test_prose_with_a_heading_and_colon_is_not_turned_into_fields(tmp_path):
    import asyncio
    from app.docproc.services.extraction import digital
    pdf = str(tmp_path / "p.pdf")
    _page_pdf(pdf, ["Cybersecurity: Our board of directors oversees the risks that arise from cyber threats to the company.",
                    "Invoice No: 1001", "Customer: Acme Corp"])
    lines = ["Cybersecurity: Our board of directors oversees the risks that arise from cyber threats to the company.", "Invoice No: 1001", "Customer: Acme Corp"]
    res = asyncio.run(digital.read_page(pdf, 1, lines, []))
    assert set(res["fields"]) == {"invoice_number", "customer"}


def test_a_table_only_layout_counts_its_columns_as_its_shape():
    def table_form(label, cols, idx=0):
        return {"doc_index": idx, "label": label, "discovered": True, "fields": {}, "pages": [1], "table_columns": {"results": cols}}
    rows = [{"extraction_id": str(i), "filename": f"{i}.pdf", "sha": str(i), "forms": [table_form("Quarterly Results", ["Item", "2026", "2025"])]} for i in range(2)]
    out = suggest.recurring_shapes(rows)
    assert len(out) == 1 and out[0]["count"] == 2 and out[0]["tables"] == ["results"] and out[0]["fields"] == []
    other = [{"extraction_id": "z", "filename": "z.pdf", "sha": "z", "forms": [table_form("Other", ["Name", "Qty", "Price"])]}]
    assert len(suggest.recurring_shapes(rows + other)) == 1                      # a different table does not join the cluster


# ---- a table the readers left rows out of -----------------------------------------------------------------------------------

def _ruled_table_pdf(path, rows=8):
    d = fitz.open()
    p = d.new_page(width=612, height=792)
    p.insert_text((50, 60), "Daily readings", fontsize=14)
    x = [50, 170, 290, 410]
    for r in range(rows + 1):
        p.draw_line((x[0], 90 + r * 22), (x[-1], 90 + r * 22), width=0.7)
    for c in x:
        p.draw_line((c, 90), (c, 90 + rows * 22), width=0.7)
    for c, h in enumerate(["Hour", "Pulse", "Pressure"]):
        p.insert_text((x[c] + 5, 105), h, fontsize=10)
    for r in range(1, rows):
        for c, v in enumerate([f"{6 + r}:00", str(60 + r), f"{110 + r}/7{r}"]):
            p.insert_text((x[c] + 5, 105 + r * 22), v, fontsize=10)
    d.save(path)


def test_rows_the_readers_left_out_are_filled_from_the_printed_rules(tmp_path):
    pdf = str(tmp_path / "t.pdf")
    _ruled_table_pdf(pdf)

    async def reader(model, jpeg, prompt):
        return {"title": "Daily readings", "tables": [{"title": None, "columns": ["Hour", "Pulse", "Pressure"],
                                                       "rows": [["7:00", "61", "111/71"], ["8:00", "62", "112/72"], ["9:00", "63", "113/73"]]}]}
    res = asyncio.run(discover.discover_page(pdf, 1, ["m-big", "m-mini"], reader))
    rows = next(iter(res["tables"].values()))
    assert len(rows) == 7                                                              # the page's seven data rows, not the three the models gave
    assert rows[6]["hour"]["value"] == "13:00" and rows[6]["pulse"]["value"] == "67"
    assert all(c["status"] == "review" and "printed rules" in c["reason"] for r in rows for c in r.values())


def test_a_table_the_readers_read_whole_is_left_as_they_gave_it(tmp_path):
    pdf = str(tmp_path / "t.pdf")
    _ruled_table_pdf(pdf)
    full = [[f"{6 + r}:00", str(60 + r), f"{110 + r}/7{r}"] for r in range(1, 8)]

    async def reader(model, jpeg, prompt):
        return {"title": "Daily readings", "tables": [{"title": None, "columns": ["Hour", "Pulse", "Pressure"], "rows": full}]}
    res = asyncio.run(discover.discover_page(pdf, 1, ["m-big", "m-mini"], reader))
    rows = next(iter(res["tables"].values()))
    assert len(rows) == 7 and all(c["status"] == "agreed" for r in rows for c in r.values())
