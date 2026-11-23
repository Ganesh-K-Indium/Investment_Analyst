"""Documents of other kinds than filings and medical forms: datasheets, agency forms, papers, photos of pages."""
import asyncio
import io

import pymupdf as fitz
import pytest

from app.docproc import pipeline
from app.docproc.services.documents import classify
from app.docproc.services.extraction import digital, structure
from app.docproc.services.reading import pdf_pages, tess


# ---- page names -------------------------------------------------------------------------------------------------

def _head(text, level=2):
    return {"type": "heading", "level": level, "text": text}


def test_a_part_number_in_a_title_does_not_stop_it_being_a_title():
    got = classify.describe([_head("5.5 Electrical Characteristics: LM358B and LM358BA")], "")
    assert got["label"] == "5.5 Electrical Characteristics: LM358B and LM358BA"
    assert classify.describe([_head("xx555 Precision Timers")], "")["label"] == "xx555 Precision Timers"


def test_table_of_contents_is_a_title_but_its_dotted_entries_are_not():
    assert classify.describe([_head("Table of Contents", 3)], "")["label"] == "Table of Contents"
    assert classify.describe([_head("4 Pin Configuration and Functions...................3")], "") == {}


def test_a_label_cut_short_or_a_running_line_is_not_a_title():
    assert classify.describe([_head("Tax and")], "") == {}
    assert classify.describe([_head("Page 2")], "") == {}
    assert classify.describe([_head("Form W-9 (Rev. 3-2024)")], "") == {}
    assert classify.describe([_head("Form")], "") == {}


def test_a_heading_that_is_the_tail_of_a_sentence_gives_way_to_the_next_one():
    got = classify.describe([_head("query with all keys, divide each by the root", 1), _head("Scaled Dot-Product Attention", 2)], "")
    assert got["label"] == "Scaled Dot-Product Attention"


def test_agency_forms_are_named_by_form_number_and_name():
    top = "Form  W-9\n(Rev. March 2024)\nRequest for Taxpayer\nIdentification Number and Certification\nDepartment of the Treasury\nInternal Revenue Service\nGo to www.irs.gov/FormW9"
    got = classify.describe([], top)
    assert got["kind"] == "Form W-9" and got["label"] == "Form W-9: Request for Taxpayer Identification Number and Certification"
    got = classify.describe([], "Form\n1040\n2025\nU.S. Individual Income Tax Return\nDepartment of the Treasury—Internal Revenue Service\nOMB No. 1545-0074")
    assert got["label"] == "Form 1040: U.S. Individual Income Tax Return"


def test_pages_with_no_title_continue_the_document_before_them():
    docs = [{"label": "Attention Is All You Need", "type": "unknown", "pages": [1, 2]}]
    by = {1: {"classification": {"type": "unknown", "label": "Attention Is All You Need"}},
          2: {"classification": {"type": "unknown", "label": "Other document"}}}
    pipeline._label_continuations(docs, by)
    assert by[2]["classification"]["label"] == "Attention Is All You Need (continued)" and by[1]["classification"]["label"] == "Attention Is All You Need"


# ---- what every page repeats --------------------------------------------------------------------------------------

def _page(*blocks):
    return {"blocks": list(blocks)}


PARTS = ['alpha', 'beta', 'gamma', 'delta']


def test_running_headers_and_footers_come_out_of_the_blocks():
    banner = {"type": "heading", "level": 3, "text": "NA555, NE555, SA555, SE555"}
    pages = [_page(dict(banner), {"type": "heading", "level": 2, "text": f"Section {n}"}, *[{"type": "paragraph", "text": f"{w} sentence belonging to part {PARTS[n - 1]}."} for w in ("First", "Second", "Third", "Fourth", "Fifth", "Sixth")],
                   {"type": "paragraph", "text": f"{n} Submit Document Feedback Copyright 2026"}) for n in range(1, 5)]
    assert structure.strip_running(pages) == 8
    for n, p in enumerate(pages, 1):
        assert [b["text"] for b in p["blocks"]][:2] == [f"Section {n}", f"First sentence belonging to part {PARTS[n - 1]}."]
        assert {r["text"] for r in p["running"]} == {"NA555, NE555, SA555, SE555", f"{n} Submit Document Feedback Copyright 2026"}


def test_a_line_that_is_not_repeated_stays():
    pages = [_page({"type": "heading", "level": 1, "text": f"Title {'abc' * n}"}, *[{"type": "paragraph", "text": f"{w} unique text {'x' * n}"} for w in ("Alpha", "Beta", "Gamma", "Delta", "Omega", "Sigma")]) for n in range(4)]
    assert structure.strip_running(pages) == 0 and "running" not in pages[0]


# ---- two columns --------------------------------------------------------------------------------------------------

def _cells_two_columns():
    rows = []
    for k in range(8):
        y = 0.1 + k * 0.03
        rows.append([(f"{'left' if k % 2 else 'text'} column line {k}, which runs on to the", 0.08, y, 0.45, y + 0.02),
                     (f"and right column line {k}, with more words that carry on past", 0.55, y, 0.92, y + 0.02)])
    return rows


def test_two_columns_of_text_read_down_each_column_in_turn():
    page = structure.build_page(_cells_two_columns())
    text = " ".join(b.get("text", "") for b in page["blocks"] if b["type"] == "paragraph")
    assert text.index("column line 7, which") < text.index("right column line 0")
    assert not [b for b in page["blocks"] if b["type"] == "table"]


def test_a_real_table_is_not_taken_for_columns_of_text():
    rows = [[("Name", 0.1, 0.1, 0.2, 0.12), ("Qty", 0.5, 0.1, 0.55, 0.12), ("Price", 0.8, 0.1, 0.88, 0.12)]]
    for k in range(8):
        y = 0.15 + k * 0.03
        rows.append([(f"Item {k}", 0.1, y, 0.2, y + 0.02), (str(k + 1), 0.5, y, 0.52, y + 0.02), (f"{k}.50", 0.8, y, 0.86, y + 0.02)])
    page = structure.build_page(rows)
    assert [b["type"] for b in page["blocks"]] == ["table"] and page["blocks"][0]["columns"] == ["Name", "Qty", "Price"]


def test_wrapped_bullet_items_stay_whole():
    rows = [[("• First item that is long enough to wrap onto", 0.08, 0.10, 0.5, 0.12)],
            [("a second line", 0.1, 0.125, 0.25, 0.145)],
            [("• Second item", 0.08, 0.16, 0.2, 0.18)]]
    page = structure.build_page(rows)
    assert page["blocks"][0]["items"] == ["First item that is long enough to wrap onto a second line", "Second item"]


def test_a_numbered_section_title_is_a_heading():
    rows = [[("1 Features", 0.08, 0.1, 0.2, 0.12)], [("Wide supply range of 3V to 36V and more text here.", 0.08, 0.14, 0.6, 0.16)]]
    page = structure.build_page(rows)
    assert page["blocks"][0]["type"] == "heading" and page["blocks"][0]["text"] == "1 Features"


# ---- ruled tables -------------------------------------------------------------------------------------------------

def _ruled_pdf(path, rows, xs, top=100, row_h=26, header_gap=False):
    doc = fitz.open()
    page = doc.new_page()
    bottom = top + row_h * len(rows)
    for k in range(len(rows) + 1):
        page.draw_line((xs[0], top + k * row_h), (xs[-1], top + k * row_h))
    for x in xs:
        page.draw_line((x, top), (x, bottom))
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            if text:
                page.insert_text((xs[c] + 4, top + r * row_h + 16), text, fontsize=8)
    doc.save(path)


def test_a_table_drawn_with_borders_is_read_from_its_rules(tmp_path):
    p = str(tmp_path / "grid.pdf")
    rows = [["PARAMETER", "TEST CONDITIONS", "VALUE", "UNIT"], ["Supply voltage", "VCC = 15V", "4.5", "V"],
            ["Supply current", "VCC = 5V, no load", "1.2", "mA"], ["Output current", "VCC = 5V", "200", "mA"]]
    _ruled_pdf(p, rows, [50, 150, 300, 400, 480])
    t = pdf_pages.analyze_page(p, 1)["tables"]
    assert len(t) == 1 and t[0]["method"] == "grid" and t[0]["header_rows"] == 1
    assert t[0]["rows"] == rows
    lt = digital.labelled_table(p, 1, t[0])
    assert lt["columns"] == rows[0] and lt["rows"][1] == rows[2]


def test_labels_set_apart_inside_one_ruled_header_cell_become_columns(tmp_path):
    p = str(tmp_path / "grid2.pdf")
    doc = fitz.open()
    page = doc.new_page()
    xs, top, h = [50, 200, 320, 420], 100, 26
    for k in range(5):
        page.draw_line((xs[0], top + k * h), (xs[-1], top + k * h))
    for x in xs:
        page.draw_line((x, top), (x, top + 4 * h))
    page.insert_text((54, top + 16), "PARAMETER", fontsize=8)
    page.insert_text((324, top + 16), "MIN", fontsize=8)
    page.insert_text((362, top + 16), "TYP", fontsize=8)
    page.insert_text((396, top + 16), "MAX", fontsize=8)
    for r, vals in enumerate([("Gain", ["1.2", "1.5", "2.0"]), ("Offset", ["3", "5", "7"]), ("Noise", ["10", "20", "30"])], 1):
        page.insert_text((54, top + r * h + 16), vals[0], fontsize=8)
        for x, v in zip((326, 364, 398), vals[1]):
            page.insert_text((x, top + r * h + 16), v, fontsize=8)
    doc.save(p)
    t = pdf_pages.analyze_page(p, 1)["tables"][0]
    assert t["method"] == "grid" and t["rows"][0][:1] == ["PARAMETER"] and t["rows"][0][-3:] == ["MIN", "TYP", "MAX"]
    assert t["rows"][1][-3:] == ["1.2", "1.5", "2.0"]


def test_a_statement_with_only_underlines_is_left_to_the_other_reader(tmp_path):
    p = str(tmp_path / "plain.pdf")
    doc = fitz.open()
    page = doc.new_page()
    for k in range(6):
        page.insert_text((60, 100 + k * 20), f"Revenue line {k}", fontsize=9)
        page.insert_text((300, 100 + k * 20), f"{1000 + k:,}", fontsize=9)
        page.draw_line((60, 104 + k * 20), (400, 104 + k * 20))
    doc.save(p)
    assert all(t["method"] != "grid" or t.get("header_rows") is None for t in pdf_pages.analyze_page(p, 1)["tables"])


# ---- fillable forms -----------------------------------------------------------------------------------------------

def test_fillable_fields_are_named_by_the_text_beside_them(tmp_path):
    p = str(tmp_path / "form.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 100), "Your first name", fontsize=9)
    page.insert_text((50, 160), "Taxpayer ID", fontsize=9)
    page.insert_text((300, 200), "Individual", fontsize=9)
    for name, rect, kind, val in (("a", (50, 104, 250, 120), fitz.PDF_WIDGET_TYPE_TEXT, "Morgan"), ("b", (115, 148, 250, 164), fitz.PDF_WIDGET_TYPE_TEXT, ""),
                                  ("c", (284, 190, 296, 202), fitz.PDF_WIDGET_TYPE_CHECKBOX, "")):
        w = fitz.Widget()
        w.field_name, w.rect, w.field_type = name, fitz.Rect(*rect), kind
        if kind == fitz.PDF_WIDGET_TYPE_TEXT:
            w.field_value = val
        page.add_widget(w)
    doc.save(p)
    info = pdf_pages.analyze_page(p, 1)
    fields, boxes = digital.fields_from_widgets(info["fields"], info["words"], info["size"])
    assert [(f["label"], f["value"]) for f in fields] == [("Your first name", "Morgan"), ("Taxpayer ID", "")]
    assert boxes[0]["label"] == "Individual" and boxes[0]["checked"] is False


# ---- sideways text and photographs -------------------------------------------------------------------------------

def test_a_stamp_up_the_margin_is_not_part_of_the_page(tmp_path):
    p = str(tmp_path / "stamp.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 100), "A real title", fontsize=16)
    page.insert_text((20, 600), "arXiv:1706.03762v7 [cs.CL] 2 Aug 2023", fontsize=9, rotate=90)
    for k in range(12):
        page.insert_text((60, 130 + k * 14), "Body text of the page goes here and runs on a while.", fontsize=10)
    doc.save(p)
    got = pdf_pages.analyze_page(p, 1)
    assert "arXiv" not in got["text"] and not [w for w in got["words"] if "Aug" in w["text"]]
    assert "A real title" in got["text"]


def test_a_short_turned_label_stays(tmp_path):
    p = str(tmp_path / "label.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((20, 120), "Form", fontsize=8, rotate=90)
    page.insert_text((60, 100), "1040", fontsize=20)
    for k in range(5):
        page.insert_text((60, 140 + k * 14), "Ordinary line of text on the page here.", fontsize=10)
    doc.save(p)
    assert any(w["text"] == "Form" for w in pdf_pages.analyze_page(p, 1)["words"])


def _photo_png(dark_side=0.55):
    """A page of text photographed in uneven light: one side much darker than the other."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("L", (1200, 1500), 250)
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 34)
    except Exception:
        font = ImageFont.load_default()
    for k in range(24):
        d.text((80, 80 + k * 55), f"Line {k} of recommended operating conditions for the supply voltage", fill=30, font=font)
    px = im.load()
    for x in range(im.width):
        f = 1 - (1 - dark_side) * (x / im.width) ** 0.6
        for y in range(im.height):
            px[x, y] = int(px[x, y] * f)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_uneven_light_is_evened_out_and_blank_pages_are_not_retried():
    png = _photo_png()
    clean, ink = tess.flatten(png)
    assert ink > 0.004 and len(clean) > 1000
    from PIL import Image
    blank = io.BytesIO()
    Image.new("L", (400, 400), 240).save(blank, "PNG")
    assert tess.flatten(blank.getvalue())[1] < 0.004


def test_a_page_that_comes_back_nearly_empty_is_read_again_on_the_cleaned_image(monkeypatch):
    calls = []

    def fake_run(png, psm=None):
        calls.append(png)
        tsv = "level\tpage\tblock\tpar\tline\tword\tl\tt\tw\th\tconf\ttext\n" + "".join(
            f"5\t1\t1\t1\t1\t{k}\t0\t0\t1\t1\t90\tw{k}\n" for k in range(60 if len(calls) == 2 else 3))
        return b"pdf-%d" % len(calls), tsv

    monkeypatch.setattr(tess, "_run", fake_run)
    monkeypatch.setattr(tess, "flatten", lambda png: (b"cleaned", 0.05))
    pdf, tsv = tess.png_to_pdf_and_words(b"photo")
    assert len(calls) == 2 and pdf == b"pdf-2" and tess.word_count(tsv) == 60          # the second reading (sparse text) was better, and was kept


def test_a_page_with_plenty_of_words_is_read_once(monkeypatch):
    calls = []
    tsv = "h\n" + "".join(f"5\t1\t1\t1\t1\t{k}\t0\t0\t1\t1\t90\tw{k}\n" for k in range(80))
    monkeypatch.setattr(tess, "_run", lambda png, psm=None: (calls.append(1), (b"pdf", tsv))[1])
    tess.png_to_pdf_and_words(b"x")
    assert len(calls) == 1


# ---- scans and vision pages: the same checks as digital ---------------------------------------------------------

def test_uneven_light_is_corrected_for_the_readers_but_a_clean_scan_is_never_touched():
    from PIL import Image
    flat = io.BytesIO()
    Image.new("L", (800, 1000), 245).save(flat, "PNG")
    assert tess.evened(flat.getvalue()) == flat.getvalue()
    photo = _photo_png(dark_side=0.4)
    assert tess.evened(photo) != photo


def _text_layer_pdf(path, lines):
    doc = fitz.open()
    page = doc.new_page()
    for k, l in enumerate(lines):
        page.insert_text((60, 100 + k * 16), l, fontsize=10)
    doc.save(path)


def test_a_vision_table_that_dropped_a_row_is_flagged(tmp_path):
    from app.docproc.services.extraction import discover
    p = str(tmp_path / "t.pdf")
    _text_layer_pdf(p, ["Revenue 70,066 61,858", "Cost of revenue 21,000 19,500", "Gross margin 49,066 42,358", "Operating income 31,100 27,000"])
    cell = lambda v: {"value": v}                                               # noqa: E731
    whole = {"t": [{"a": cell("Revenue"), "b": cell("70,066"), "c": cell("61,858")}, {"a": cell("Cost"), "b": cell("21,000"), "c": cell("19,500")},
                   {"a": cell("Gross"), "b": cell("49,066"), "c": cell("42,358")}, {"a": cell("Op"), "b": cell("31,100"), "c": cell("27,000")}]}
    assert discover.uncaptured(p, 1, {}, whole) is None
    short = {"t": whole["t"][:2]}
    note = discover.uncaptured(p, 1, {}, short)
    assert note and "49066" in note.replace(",", "") and note.startswith("page 1")


def test_a_page_with_few_figures_is_not_judged(tmp_path):
    from app.docproc.services.extraction import discover
    p = str(tmp_path / "f.pdf")
    _text_layer_pdf(p, ["Invoice 2026 total 120.50", "Thank you for your business"])
    assert discover.uncaptured(p, 1, {}, {}) is None


def test_no_model_scan_goes_to_the_layout_reader_not_plain_label_lines():
    from app.docproc.services.reading import router
    assert router.decide_extractor("unknown", llm_available=False, all_digital=False, laid_out=True)["extractor"] == "discover"
    assert router.decide_extractor("unknown", llm_available=False, all_digital=False)["extractor"] == "kv"


def test_a_page_read_from_ocr_layout_is_marked_unverified():
    res = {"fields": {"a": {"value": "x", "status": "agreed", "method": "text-layer"}, "b": {"value": None, "status": "empty"}},
           "tables": {"t": [{"c": {"value": "1", "status": "agreed", "method": "text-layer"}}]}, "readers": ["text-layer"]}
    digital.as_ocr(res, doubtful=False)
    assert res["fields"]["a"]["status"] == "single" and res["fields"]["a"]["method"] == "ocr-local" and res["fields"]["b"]["status"] == "empty"
    assert res["tables"]["t"][0]["c"]["status"] == "single" and res["readers"] == ["ocr-local"]
    digital.as_ocr(res, doubtful=True)
    assert res["fields"]["a"]["status"] == "review"


def test_a_dense_page_is_matched_to_positions_in_reasonable_time():
    import time
    from app.docproc.services.extraction import align
    ref, vis = [], []
    for i in range(450):
        y = 0.02 + i * 0.002
        cells = [(f"Item {i} description line {i * 7}", 0.05, y, 0.4, y + 0.0015), (f"{1000 + i * 13:,}", 0.5, y, 0.58, y + 0.0015)]
        ref.append(cells)
        vis.append(" | ".join(c[0] for c in cells))
    t = time.time()
    got = align.place(vis, ref)
    assert got is not None and got[1] > 0.99 and time.time() - t < 20


@pytest.mark.skipif(not tess.available(), reason="Tesseract is not installed")
def test_a_scanned_table_with_no_model_gives_extracted_values(tmp_path):
    from app.docproc.engines.strategy import plan as plans
    src = str(tmp_path / "t.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 80), "QUARTERLY RESULTS", fontsize=18)
    page.insert_text((60, 120), "Item", fontsize=12)
    page.insert_text((300, 120), "2026", fontsize=12)
    page.insert_text((400, 120), "2025", fontsize=12)
    for k, (name, a, b) in enumerate([("Revenue", "70,066", "61,858"), ("Cost of revenue", "21,000", "19,500"), ("Gross margin", "49,066", "42,358"),
                                      ("Operating income", "31,100", "27,000")]):
        y = 150 + k * 28
        page.insert_text((60, y), name, fontsize=12)
        page.insert_text((300, y), a, fontsize=12)
        page.insert_text((400, y), b, fontsize=12)
    pix = page.get_pixmap(dpi=200)
    scan = fitz.open()
    sp = scan.new_page(width=page.rect.width, height=page.rect.height)
    sp.insert_image(sp.rect, pixmap=pix)
    scan.save(src)
    r = asyncio.run(pipeline.process(src, plan=plans.resolve({"strategy": "economy", "local_engine": "tesseract"})))
    form = r["forms"][0]
    assert form.get("discovered") and form["readers"] == ["ocr-local"]
    values = {c["value"] for rows in form["tables"].values() for row in rows for c in row.values()}
    assert {"70,066", "49,066"} <= values
    assert all(c["status"] in ("single", "review", "empty") for rows in form["tables"].values() for row in rows for c in row.values())


def test_digits_inside_part_numbers_are_not_amounts_a_table_lost(tmp_path):
    from app.docproc.services.extraction import discover
    p = str(tmp_path / "parts.pdf")
    _text_layer_pdf(p, ["LM158, LM258, LM358, LM158A, LM258A, LM358A", "Supply voltage LM2904 3 36", "Operating temperature 125 85"])
    assert discover.uncaptured(p, 1, {}, {}) is None


def test_a_logo_word_does_not_title_the_page_when_a_real_heading_is_there():
    got = classify.describe([_head("TEXAS", 1), _head("INSTRUMENTS", 1), _head("5.3 Recommended Operating Conditions", 2)], "")
    assert got["label"] == "5.3 Recommended Operating Conditions"
    assert classify.describe([_head("INSTRUMENTS", 1)], "")["label"] == "INSTRUMENTS"


def test_a_poor_text_layer_does_not_second_guess_good_readers(tmp_path):
    from app.docproc.services.extraction import discover
    p = str(tmp_path / "garbled.pdf")
    _text_layer_pdf(p, ["Revenue 7O,O66 6l,858", "Cost of revenue 2l,000 l9,5OO", "Gross margin 49,O66 42,358", "Operating income 3l,l00 27,OOO", "Net income 676 688"])
    cell = lambda v: {"value": v}                                               # noqa: E731
    table = {"t": [{"a": cell("Revenue"), "b": cell("70,066"), "c": cell("61,858")}, {"a": cell("Cost"), "b": cell("21,000"), "c": cell("19,500")},
                   {"a": cell("Gross"), "b": cell("49,066"), "c": cell("42,358")}, {"a": cell("Op"), "b": cell("31,100"), "c": cell("27,000")}]}
    assert discover.uncaptured(p, 1, {}, table) is None


def test_vulgar_fraction_compares_equal_to_spelled_out():
    from app.docproc.services.extraction import normalize as N
    assert N.normalize("1½ min.", "text") == N.normalize("1 1/2 min.", "text")
    assert N.normalize("1½ min.", "text") != N.normalize("1 min.", "text")
