"""The structure of a page from its layout alone (services/extraction/structure.py)."""
from app.docproc.services.extraction import structure as st


def row(y, *cells, h=0.012):
    """One row: cells are (text, x0) or (text, x0, x1); y is the row's top as a fraction of the page."""
    return [(c[0], c[1], y, c[2] if len(c) > 2 else c[1] + 0.012 * len(c[0]) * 0.5, y + h) for c in cells]


def kinds(page):
    return [b["type"] for b in page["blocks"]]


def test_a_taller_line_is_a_heading_and_its_level_follows_its_size():
    page = st.build_page([row(0.05, ("Quarterly Report", 0.1), h=0.03), row(0.12, ("Revenue", 0.1), h=0.018),
                          row(0.16, ("Sales grew in the third quarter.", 0.1)), row(0.18, ("Costs were flat.", 0.1)), row(0.20, ("More text here.", 0.1))])
    heads = [b for b in page["blocks"] if b["type"] == "heading"]
    assert [h["text"] for h in heads] == ["Quarterly Report", "Revenue"] and heads[0]["level"] == 1 and heads[1]["level"] == 2


def test_consecutive_lines_form_one_paragraph_and_a_gap_starts_a_new_one():
    page = st.build_page([row(0.10, ("First line of text", 0.1)), row(0.115, ("second line of the same paragraph", 0.1)),
                          row(0.20, ("A new paragraph after a gap", 0.1)), row(0.30, ("Third", 0.1))])
    paras = [b["text"] for b in page["blocks"] if b["type"] == "paragraph"]
    assert paras[0] == "First line of text second line of the same paragraph" and paras[1].startswith("A new paragraph") and len(paras) == 3


def test_label_value_rows_group_and_a_wrapped_value_stays_with_its_label():
    page = st.build_page([row(0.10, ("Patient Name: Morgan Taylor", 0.1), ("Visit No.: 1", 0.6)),
                          row(0.125, ("History: pain started four weeks ago and", 0.1)),
                          row(0.14, ("has not stopped since", 0.1))])
    assert kinds(page) == ["key_value"]
    items = {i["label"]: i["value"] for i in page["blocks"][0]["items"]}
    assert items["Patient Name"] == "Morgan Taylor" and items["Visit No."] == "1"
    assert items["History"] == "pain started four weeks ago and has not stopped since"


def test_aligned_cells_become_a_table_and_a_header_row_names_the_columns():
    page = st.build_page([row(0.10, ("Code", 0.1), ("Service", 0.3), ("Units", 0.7)),
                          row(0.12, ("97110", 0.1), ("Therapeutic exercise", 0.3), ("2", 0.7)),
                          row(0.14, ("97140", 0.1), ("Manual therapy", 0.3), ("1", 0.7))])
    (tbl,) = page["blocks"]
    assert tbl["type"] == "table" and tbl["header"] and tbl["columns"] == ["Code", "Service", "Units"]
    assert tbl["rows"] == [["97110", "Therapeutic exercise", "2"], ["97140", "Manual therapy", "1"]]


def test_a_table_without_a_header_gets_numbered_columns_and_a_missing_cell_stays_empty():
    page = st.build_page([row(0.10, ("Rent", 0.1), ("1,200.00", 0.6)), row(0.12, ("Utilities", 0.1), ("85.50", 0.6)), row(0.14, ("Notes", 0.1))])
    tbl = page["blocks"][0]
    assert tbl["type"] == "table" and not tbl["header"] and tbl["columns"] == ["Column 1", "Column 2"] and tbl["rows"][0] == ["Rent", "1,200.00"]


def test_long_text_set_in_two_columns_is_not_taken_for_a_table():
    long = "this is a long sentence of running text that goes on and on across the column"
    page = st.build_page([row(0.10, (long, 0.1), (long, 0.55)), row(0.12, (long, 0.1), (long, 0.55)), row(0.14, (long, 0.1), (long, 0.55))])
    assert "table" not in kinds(page)


def test_list_items_are_grouped_and_ordered_lists_are_told_apart():
    bullets = st.build_page([row(0.10, ("• apples", 0.1)), row(0.12, ("• pears", 0.1))])["blocks"][0]
    numbered = st.build_page([row(0.10, ("1. first", 0.1)), row(0.12, ("2. second", 0.1))])["blocks"][0]
    assert bullets["type"] == "list" and bullets["items"] == ["apples", "pears"] and not bullets["ordered"]
    assert numbered["items"] == ["first", "second"] and numbered["ordered"]


def test_dates_amounts_phones_and_emails_are_listed_with_their_block():
    page = st.build_page([row(0.10, ("Invoice date: 08/12/2026", 0.1)), row(0.20, ("Total due $1,250.00 by 5%", 0.1)),
                          row(0.30, ("Call (201) 447-5757 or write to ap@example.com", 0.1))])
    found = {(e["type"], e["value"]) for e in page["entities"]}
    assert {("date", "08/12/2026"), ("amount", "$1,250.00"), ("percent", "5%"), ("phone", "(201) 447-5757"), ("email", "ap@example.com")} <= found


def test_fax_stamps_are_left_out_of_the_structure():
    page = st.build_page([row(0.01, ("From Sample 1.201.555.0121 Wed Nov 11 2026 MST Page 4 of 9", 0.1)), row(0.10, ("Real content here", 0.1))])
    assert [b.get("text") for b in page["blocks"]] == ["Real content here"]


def test_a_page_with_no_layout_is_built_from_its_lines_and_says_so():
    page = st.build_from_lines(["INVOICE", "Patient Name: Morgan Taylor | Visit No.: 1", "Code | Service | Units", "97110 | Exercise | 2", "97140 | Manual | 1",
                                "the sentence starts here and", "continues on the next line"])
    assert page["layout"] is False and all("bbox" not in b for b in page["blocks"])
    assert kinds(page)[:3] == ["heading", "key_value", "table"]
    assert page["blocks"][-1]["text"] == "the sentence starts here and continues on the next line"


def test_an_empty_page_gives_no_blocks():
    assert st.build_page([]) == {"blocks": [], "entities": [], "layout": True}


def test_the_pipeline_gives_every_page_a_structure_and_the_export_groups_it_by_document():
    import asyncio
    import tempfile
    from types import SimpleNamespace
    from app.docproc import export, pipeline
    from tests.docproc.make_packet import build
    path = tempfile.mkdtemp() + "/packet.pdf"
    build(path)
    result = asyncio.run(pipeline.process(path))
    assert all("structure" in p for p in result["pages"])
    digital = [p for p in result["pages"] if p["source"] == "text-layer" and p["text"].strip()]
    assert digital and any(b["type"] in ("heading", "key_value", "paragraph") for p in digital for b in p["structure"]["blocks"])
    ex = SimpleNamespace(id="x", filename="packet.pdf", result=result)
    out = export.structure_json(ex)
    assert [d["doc_index"] for d in out["documents"]] == [d["doc_index"] for d in result["documents"]]
    assert all("structure" in p for p in export.pages_json(ex)["pages"])             # pages.json is the full per-page record


def test_a_table_found_by_another_extractor_replaces_its_rows_but_not_the_heading_in_its_box():
    rows = [row(0.05, ("INCOME STATEMENTS", 0.1), h=0.02), row(0.12, ("Product", 0.1), ("15,089", 0.6)), row(0.14, ("Service", 0.1), ("67,797", 0.6)), row(0.30, ("Refer to the notes.", 0.1))]
    given = [{"bbox": [0.05, 0.03, 0.9, 0.2], "columns": ["Line item", "Col 1"], "rows": [["Product", "15,089"], ["Service", "67,797"]]}]
    page = st.build_page(rows, tables=given)
    assert kinds(page) == ["heading", "table", "paragraph"]
    assert page["blocks"][1]["columns"] == ["Line item", "Column 1"] and page["blocks"][1]["rows"][1] == ["Service", "67,797"]


# -- borrowing positions for a vision model's text (align.py) -----------------------------------------------------------

def _ref():
    """Tesseract-like rows of cells: a title beside an address, a label row, and a wrapped paragraph."""
    def c(text, x0, y, x1=None):
        return (text, x0, y, x1 if x1 is not None else x0 + 0.01 * len(text), y + 0.012)
    return [
        [c("Sample Health Associates", 0.1, 0.05)],
        [c("25 Testway Ave Ste 201", 0.1, 0.08), c("Physical Therapy", 0.6, 0.08)],
        [c("Testville NJ 07001", 0.1, 0.10), c("Initial", 0.6, 0.10)],
        [c("Patient Name: Morgan Taylor", 0.1, 0.16), c("Visit No.: 1", 0.6, 0.16)],
        [c("Taylor is a 51 year old female with neck", 0.1, 0.22)],
        [c("pain starting four weeks ago and it has", 0.1, 0.235)],
        [c("not stopped since then", 0.1, 0.25)],
    ]


def test_vision_text_takes_the_positions_of_the_matching_reference_cells():
    from app.docproc.services.extraction import align
    vision = ["Sample Health Associates", "25 Testway Ave Ste 201", "Testville NJ 07001", "Physical Therapy", "Initial",
              "Patient Name: Morgan Taylor | Visit No.: 1"]
    rows, coverage = align.place(vision, _ref())
    assert coverage == 1.0
    by_text = {c[0]: c for r in rows for c in r}
    assert by_text["Physical Therapy"][1:3] == (0.6, 0.08) and by_text["Sample Health Associates"][1:3] == (0.1, 0.05)
    # a column-by-column listing is put back into rows: the title is beside the address again
    assert any({"25 Testway Ave Ste 201", "Physical Therapy"} <= {c[0] for c in r} for r in rows)


def test_the_vision_models_words_are_kept_not_tesseracts():
    from app.docproc.services.extraction import align
    ref = _ref()
    ref[3] = [("Patient Narne: Morgan Tayler", 0.1, 0.16, 0.4, 0.172), ("Visit No.: l", 0.6, 0.16, 0.7, 0.172)]      # Tesseract misread
    rows, _ = align.place(["Sample Health Associates", "Patient Name: Morgan Taylor | Visit No.: 1"], ref)
    assert any(c[0] == "Patient Name: Morgan Taylor" for r in rows for c in r) and any(c[0] == "Visit No.: 1" for r in rows for c in r)


def test_a_paragraph_the_model_joined_is_spread_back_over_the_rows_it_came_from():
    from app.docproc.services.extraction import align
    para = "Taylor is a 51 year old female with neck pain starting four weeks ago and it has not stopped since then"
    rows, coverage = align.place(["Sample Health Associates", para], _ref())
    ys = sorted({round(c[2], 3) for r in rows for c in r if c[0] != "Sample Health Associates"})
    assert coverage == 1.0 and ys == [0.22, 0.235, 0.25]
    assert " ".join(c[0] for r in rows[1:] for c in r) == para


def test_a_page_that_mostly_does_not_match_is_refused_rather_than_guessed():
    from app.docproc.services.extraction import align
    vision = ["Totally different words here", "Nothing like the reference text at all", "Another unrelated line of text", "Sample Health Associates"]
    assert align.place(vision, _ref()) is None


def test_the_same_text_twice_is_not_placed_on_one_cell_twice():
    from app.docproc.services.extraction import align
    ref = [[("Yes please confirm", 0.1, 0.1, 0.4, 0.112)], [("Yes please confirm", 0.1, 0.3, 0.4, 0.312)]]
    rows, _ = align.place(["Yes please confirm", "Yes please confirm"], ref)
    assert sorted(round(c[2], 2) for r in rows for c in r) == [0.1, 0.3]


def _scanned_pdf(path, lines):
    """A PDF whose only page is a picture of `lines` (no text layer), like a scan."""
    import pymupdf
    src = pymupdf.open()
    page = src.new_page(width=612, height=792)
    y = 90
    for text in lines:
        page.insert_text((60, y), text, fontsize=14)
        y += 26
    pix = page.get_pixmap(dpi=200)
    out = pymupdf.open()
    scan = out.new_page(width=612, height=792)
    scan.insert_image(scan.rect, stream=pix.tobytes("png"))
    out.save(path)


def test_a_vision_read_page_in_the_pipeline_gets_positions_when_it_matches_and_says_where_from():
    import asyncio
    import tempfile
    import pytest
    from app.docproc import pipeline
    from app.docproc.services.reading.tess import available
    if not available():
        pytest.skip("Tesseract is not installed")
    lines = ["Quarterly Statement", "Patient Name: Morgan Taylor", "Date of Birth: 01/15/1975", "Total due $1,250.00 on 08/12/2026"]
    path = tempfile.mkdtemp() + "/scan.pdf"
    _scanned_pdf(path, lines)

    async def vision(model, jpeg):                       # a model that read the page correctly
        return lines

    result = asyncio.run(pipeline.process(path, ocr_reader=vision))
    (p,) = [x for x in result["pages"] if x["source"] == "ocr"]
    st = p["structure"]
    assert st["layout"] is True and st["positions_from"] in ("tesseract", "local engine") and st["placement"] >= 0.85
    kv = [b for b in st["blocks"] if b["type"] == "key_value"]
    assert kv and all(b.get("bbox") for b in kv)
    assert {e["value"] for e in st["entities"]} >= {"01/15/1975", "$1,250.00"}


def test_a_vision_read_page_that_does_not_match_keeps_a_text_only_structure_and_says_so():
    import asyncio
    import tempfile
    import pytest
    from app.docproc import pipeline
    from app.docproc.services.reading.tess import available
    if not available():
        pytest.skip("Tesseract is not installed")
    path = tempfile.mkdtemp() + "/scan.pdf"
    _scanned_pdf(path, ["Quarterly Statement", "Patient Name: Morgan Taylor", "Date of Birth: 01/15/1975"])

    async def vision(model, jpeg):                       # a model whose reading has nothing to do with the picture
        return ["Completely different wording", "Nothing here matches the page", "Another line of invented text"]

    result = asyncio.run(pipeline.process(path, ocr_reader=vision))
    (p,) = [x for x in result["pages"] if x["source"] == "ocr"]
    assert p["structure"]["layout"] is False and "positions_from" not in p["structure"]
    assert all("bbox" not in b for b in p["structure"]["blocks"])


# -- saying what a page with no template is, instead of "Unclassified" -----------------------------------------------------

def _h(text, level=2):
    return {"type": "heading", "level": level, "text": text}


def test_a_page_no_template_names_is_described_by_its_kind_or_its_title():
    from app.docproc.services.documents import classify
    sec = classify.describe([_h("UNITED STATES", 1), _h("FORM 10-Q", 1)], "UNITED STATES\nSECURITIES AND EXCHANGE COMMISSION\nFORM 10-Q\nQuarterly report")
    assert sec["label"] == "SEC Form 10-Q" and sec["kind"] == "SEC Form 10-Q"
    inv = classify.describe([_h("INVOICE")], "INVOICE\nInvoice No: 4471\nBill to: Acme Ltd\nAmount due $1,250.00")
    assert inv["label"] == "Invoice"
    assert classify.describe([_h("Pump Assembly Test Plan")], "Pump Assembly Test Plan\nRev B")["label"] == "Pump Assembly Test Plan"
    assert classify.describe([], "Dear Ms Rao,\nThank you for your letter.")["label"] == "Letter"


def test_a_kind_needs_its_words_together_so_a_passing_mention_does_not_count():
    from app.docproc.services.documents import classify
    assert classify.describe([_h("Cover note")], "Cover note\nPlease find the invoice attached.")["kind"] is None


def test_section_markers_are_passed_over_and_the_most_specific_heading_is_the_title():
    from app.docproc.services.documents import classify
    blocks = [_h("PART I", 3), _h("PART I. FINANCIAL INFORMATION", 3), _h("ITEM 1. FINANCIAL STATEMENTS", 3), _h("INCOME STATEMENTS", 3)]
    assert classify.describe(blocks, "PART I\nINCOME STATEMENTS")["title"] == "INCOME STATEMENTS"


def test_ocr_noise_is_never_used_as_a_title_and_a_poor_reading_offers_only_a_kind():
    from app.docproc.services.documents import classify
    assert classify.describe([_h("Me thn it it mE", 1)], "Me thn it it mE\nxx yy") == {}              # nothing sensible to say
    assert classify.describe([_h("Pump Assembly Test Plan")], "Pump Assembly Test Plan", trust_text=False) == {}
    assert classify.describe([_h("INVOICE")], "INVOICE\nInvoice No: 4471\nAmount due $5", trust_text=False)["label"] == "Invoice"


def test_the_pipeline_labels_an_unnamed_page_and_keeps_its_type_unknown_for_routing():
    from app.docproc import pipeline
    blocks = {"blocks": [_h("BALANCE SHEETS", 3)]}
    got = pipeline._classify_page("BALANCE SHEETS\nCash and equivalents 1,200\nTotal assets 9,400 and more words here to pass", blocks, {"confidence": 0.95})
    assert got["type"] == "unknown" and got["label"] == "BALANCE SHEETS" and got["by"] == "layout"
    noisy = pipeline._classify_page("Me thn it it mE\nsome more smudged text here that is long enough", {"blocks": [_h("Me thn it it mE", 1)]}, {"confidence": 0.4})
    assert noisy["type"] == "unknown" and noisy["label"] == "Other document"
    known = pipeline._classify_page("Provider Prior Authorization Request Form\n" + "x" * 60, {"blocks": []}, {})
    assert known["type"] == "prior_auth_form"


# -- a title Tesseract spreads over pieces of different count, and pages that continue a document ------------------------------

def test_a_title_in_the_right_column_is_found_when_the_neighbouring_lines_are_cut_into_more_pieces():
    from app.docproc.services.documents import classify
    page = "\n".join(["Sample Health Associates", "25 Testway Ave Ste 201 | Physical Therapy", "Testville | NJ 07001-1718 | Initial",
                      "Phone: (201)447-5757 | \u2018 | .", "Fax: (2otjaa7-o750000 | Examination",
                      "Patient Name: Morgan Taylor | Date of Initial Examination: 08/12/2026"])
    assert classify.classify_text(page)["type"] == "pt_initial_exam"


def test_a_page_inside_a_recognised_document_is_labelled_as_its_continuation_without_changing_its_type():
    from app.docproc import pipeline
    pages = {1: {"classification": {"type": "pt_initial_exam", "label": "Physical therapy initial examination"}},
             2: {"classification": {"type": "unknown", "label": "Other document", "by": "layout"}},
             3: {"classification": {"type": "unknown", "label": "Other document", "by": "layout"}}}
    docs = [{"type": "pt_initial_exam", "label": "Physical therapy initial examination", "pages": [1, 2]},
            {"type": "unknown", "label": "Other document", "pages": [3]}]
    pipeline._label_continuations(docs, pages)
    assert pages[2]["classification"]["label"] == "Physical therapy initial examination (continued)" and pages[2]["classification"]["type"] == "unknown"
    assert pages[3]["classification"]["label"] == "Other document"            # an unknown document keeps its own description
    assert pages[1]["classification"]["label"] == "Physical therapy initial examination"
