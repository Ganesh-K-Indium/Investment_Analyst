"""Digital pages read by code: column names from geometry, cover-page facts without colons, check boxes from glyphs."""
import asyncio
from pathlib import Path

import pytest

from app.docproc.services.extraction import digital, structure as st
from app.docproc.services.reading import pdf_pages

MSFT = Path(__file__).resolve().parent.parent.parent / "sample_data" / "MSFT_10-Q_short_p1-5.pdf"
needs_sample = pytest.mark.skipif(not MSFT.exists(), reason="sample 10-Q not present")


def row(y, *cells, h=0.01):
    """cells are (text, x0, x1)."""
    return [(c[0], c[1], y, c[2], y + h) for c in cells]


# -- column names from where the header words sit ------------------------------------------------------------------------

@needs_sample
def test_a_heading_over_two_columns_names_both_and_the_years_name_one_each():
    info = pdf_pages.analyze_page(str(MSFT), 3)
    t = info["tables"][0]
    header, body = digital.split_header(t)
    ncols = max(len(r) for r in body)
    assert digital.raw_labels(header, ncols)[1] == "Three 2026"                      # what joining the cells gave
    assert digital.geometric_labels(str(MSFT), 3, t["bbox"], header, ncols) == [
        "Item", "Three Months Ended March 31, 2026", "Three Months Ended March 31, 2025",
        "Nine Months Ended March 31, 2026", "Nine Months Ended March 31, 2025"]


@needs_sample
def test_geometric_labels_decline_when_the_words_are_not_the_cells_words():
    info = pdf_pages.analyze_page(str(MSFT), 3)
    t = info["tables"][0]
    header, body = digital.split_header(t)
    wrong = [list(r) for r in header]
    wrong[2][1] = "2099"                                                              # the cells no longer match the page
    assert digital.geometric_labels(str(MSFT), 3, t["bbox"], wrong, max(len(r) for r in body)) is None
    assert digital.geometric_labels(str(MSFT), 3, t["bbox"], header[:1], 5) is None   # one header row: nothing to combine


def test_a_table_of_names_and_codes_or_a_contents_list_is_not_an_amount_table():
    codes = {"rows": [["Title", "Symbol", "Exchange"], ["Common stock", "MSFT", "NASDAQ"], ["Notes due 2028", "MSFT", "NASDAQ"]], "bbox": [0, 0, 1, 1]}
    toc = {"rows": [["a) Income Statements", "3"], ["b) Balance Sheets", "5"], ["c) Notes", "8"]], "bbox": [0, 0, 1, 1]}
    money = {"rows": [["", "2026", "2025"], ["Revenue", "82,886", "70,066"], ["Costs", "26,828", "21,919"]], "bbox": [0, 0, 1, 1]}
    assert not digital.is_amount_table(codes) and not digital.is_amount_table(toc) and digital.is_amount_table(money)


# -- cover-page facts and check boxes in the layout ----------------------------------------------------------------------

def test_a_value_over_its_caption_becomes_a_label_and_value():
    page = st.build_page([row(0.30, ("WASHINGTON", 0.17, 0.27), ("91-1144442", 0.75, 0.82)),
                          row(0.311, ("(STATE OF INCORPORATION)", 0.12, 0.31), ("(I.R.S. ID)", 0.76, 0.81))])
    (kv,) = page["blocks"]
    assert [(i["label"], i["value"]) for i in kv["items"]] == [("State of Incorporation", "WASHINGTON"), ("I.R.S. ID", "91-1144442")]


def test_a_label_followed_by_an_id_or_a_date_is_a_pair_but_a_sentence_is_not():
    page = st.build_page([row(0.10, ("Commission File Number 001-37845", 0.3, 0.7)), row(0.14, ("For the Quarterly Period Ended March 31, 2026", 0.1, 0.9)),
                          row(0.30, ("The committee reviewed every open item at length and then filed on March 31, 2026", 0.1, 0.9))])
    pairs = [(i["label"], i["value"]) for b in page["blocks"] if b["type"] == "key_value" for i in b["items"]]
    assert ("Commission File Number", "001-37845") in pairs and ("For the Quarterly Period Ended", "March 31, 2026") in pairs
    assert len(pairs) == 2


def test_a_centred_line_under_a_pair_is_not_swallowed_into_its_value():
    page = st.build_page([row(0.10, ("For the Quarterly Period Ended March 31, 2026", 0.01, 0.5)), row(0.125, ("OR", 0.5, 0.52))])
    kv = page["blocks"][0]
    assert kv["items"][0]["value"] == "March 31, 2026"


def test_check_box_glyphs_become_options_with_their_state_and_a_yes_no_takes_its_question():
    page = st.build_page([
        row(0.10, ("Indicate by check mark whether the registrant has filed all reports required to be filed during the preceding 12 months", 0.01, 0.95)),
        row(0.115, ("requirements for the past 90 days.", 0.01, 0.4), ("Yes ☒", 0.45, 0.5), ("No ☐", 0.55, 0.6)),
        row(0.20, ("Large Accelerated Filer ☒", 0.01, 0.3), ("Accelerated Filer ☐", 0.6, 0.9)),
        row(0.30, ("☒ QUARTERLY REPORT PURSUANT TO SECTION 13", 0.01, 0.9)),
    ])
    boxes = [i for b in page["blocks"] if b["type"] == "checkboxes" for i in b["items"]]
    by = {i["label"]: i for i in boxes}
    assert by["Yes"]["checked"] and not by["No"]["checked"] and by["Yes"]["group"].startswith("Indicate by check mark")
    assert by["Large Accelerated Filer"]["checked"] and not by["Accelerated Filer"]["checked"]
    assert by["QUARTERLY REPORT PURSUANT TO SECTION 13"]["checked"]


def test_a_bare_page_number_is_page_furniture_not_a_paragraph():
    page = st.build_page([row(0.10, ("Some content line here", 0.1, 0.5)), row(0.60, ("3", 0.5, 0.51))])
    assert page["page_number"] == "3" and [b["type"] for b in page["blocks"]] == ["paragraph"]


def test_a_text_table_with_centred_columns_is_found_by_overlap_not_left_edges():
    page = st.build_page([row(0.10, ("Title of each class", 0.01, 0.30), ("Trading Symbol", 0.44, 0.56), ("Name of exchange", 0.70, 0.90)),
                          row(0.12, ("Common stock, par value", 0.01, 0.40), ("MSFT", 0.48, 0.52), ("NASDAQ", 0.76, 0.84)),
                          row(0.14, ("3.125% Notes due 2028", 0.01, 0.30), ("MSFT", 0.48, 0.52), ("NASDAQ", 0.76, 0.84))])
    (tbl,) = page["blocks"]
    assert tbl["type"] == "table" and tbl["header"] and tbl["columns"] == ["Title of each class", "Trading Symbol", "Name of exchange"] and len(tbl["rows"]) == 2


# -- in the extracted data -----------------------------------------------------------------------------------------------

def test_read_page_adds_the_facts_and_check_boxes_the_layout_found_and_keeps_no_distinct_from_number():
    facts = [{"label": "Commission File Number", "value": "001-37845", "bbox": [1, 2, 3, 4]}]
    boxes = [{"label": "Yes", "checked": True, "group": "Has the registrant filed all reports", "bbox": None},
             {"label": "No", "checked": False, "group": "Has the registrant filed all reports", "bbox": None}]
    if not MSFT.exists():
        pytest.skip("sample 10-Q not present")
    got = asyncio.run(digital.read_page(str(MSFT), 1, ["x"], [], None, None, None, facts, boxes))
    assert got["fields"]["commission_file_number"]["value"] == "001-37845"
    keys = [k for k, c in got["fields"].items() if c["type"] == "checkbox"]
    assert keys == ["has_the_registrant_filed_all_reports.yes", "has_the_registrant_filed_all_reports.no"]
    assert got["fields"][keys[0]]["value"] is True and got["fields"][keys[1]]["value"] is False


@needs_sample
def test_the_10q_comes_out_with_named_columns_cover_facts_and_ticked_boxes():
    from app.docproc import pipeline
    r = asyncio.run(pipeline.process(str(MSFT)))
    f = r["forms"][0]
    assert f["table_columns"]["income_statements"][1:3] == ["Three Months Ended March 31, 2026", "Three Months Ended March 31, 2025"]
    assert "three_months_ended_march_31_2026" in f["tables"]["income_statements"][0]
    assert f["fields"]["commission_file_number"]["value"] == "001-37845" and f["fields"]["state_of_incorporation"]["value"] == "WASHINGTON"
    assert f["fields"]["checkboxes.large_accelerated_filer"]["value"] is True and f["fields"]["checkboxes.accelerated_filer"]["value"] is False
    assert r["pages"][2]["structure"].get("page_number") == "3"
    sec = [k for k in f["tables"] if k.startswith("securities_registered")]
    assert sec and f["table_columns"][sec[0]] == ["Title of each class", "Trading Symbol", "Name of exchange on which registered"]
