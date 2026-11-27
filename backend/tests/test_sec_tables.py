"""Tables of SEC filings as the ingestion pipeline reads them (app/docproc/services/reading/table_extractor.py).

The pages are drawn the way EDGAR filings render: right-aligned figures with a separate "$", column captions stacked on
two or three lines, and the period captions centred over the columns they cover."""
import pymupdf
import pytest

from app.docproc.services.reading import table_extractor as te

# right edges of four value columns, as in a 10-Q income statement (two periods, two years each)
EDGES = [360, 435, 510, 585]


def _right(page, x1, y, text, size=8):
    page.insert_text((x1 - pymupdf.get_text_length(text, fontsize=size), y), text, fontsize=size)


def _centred(page, cx, y, text, size=8):
    page.insert_text((cx - pymupdf.get_text_length(text, fontsize=size) / 2, y), text, fontsize=size)


def _income_statement(rule_years: bool):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    _centred(page, 300, 60, "CONSOLIDATED STATEMENTS OF INCOME")
    _centred(page, 300, 72, "(in millions, except per share amounts; unaudited)")
    _centred(page, 397, 90, "Three Months Ended")
    _centred(page, 547, 90, "Six Months Ended")
    _centred(page, 397, 102, "June 30,")
    _centred(page, 547, 102, "June 30,")
    for x1, year in zip(EDGES, ["2025", "2026", "2025", "2026"]):
        _right(page, x1, 115, year)
    rows = [("Revenues", ["96,428", "119,796", "186,662", "229,692"], True),
            ("Cost of revenues", ["39,039", "45,943", "75,400", "87,214"], False),
            ("Research and development", ["13,808", "18,219", "27,364", "35,251"], False),
            ("Income from operations", ["31,271", "40,770", "61,877", "80,466"], False),
            ("Net income", ["28,196", "112,193", "62,736", "174,771"], True)]
    y = 130
    for label, vals, dollar in rows:
        page.insert_text((40, y), label, fontsize=8)
        for x1, v in zip(EDGES, vals):
            _right(page, x1, y, v)
            if dollar:
                page.insert_text((x1 - 62, y), "$", fontsize=8)
        y += 14
    top = 106 if rule_years else 120          # the detected box starts above the years line (inside it) or below it
    for yy in (top, y - 8):
        page.draw_line((30, yy), (590, yy), width=0.5)
    for x in (30, 290, 365, 440, 515, 590):
        page.draw_line((x, top), (x, y - 8), width=0.5)
    return doc


@pytest.mark.parametrize("rule_years", [True, False])
def test_stacked_period_captions_name_every_column_and_the_years_are_not_a_row(rule_years):
    doc = _income_statement(rule_years)
    tables = te.extract_page_tables(doc[0], 1)
    assert len(tables) == 1
    t = tables[0]
    assert t["headers"][1:] == ["Three Months Ended June 30, 2025", "Three Months Ended June 30, 2026",
                                "Six Months Ended June 30, 2025", "Six Months Ended June 30, 2026"]
    assert [r[0] for r in t["rows"]] == ["Revenues", "Cost of revenues", "Research and development",
                                         "Income from operations", "Net income"]          # no stray "2025 | | 2026" row
    assert t["rows"][0][1:] == ["$96,428", "$119,796", "$186,662", "$229,692"]
    assert "CONSOLIDATED STATEMENTS OF INCOME" in t["title"]


def test_caption_groups_need_an_even_split():
    phrase = lambda x0, x1: [(x0, 0, x1, 0, "w")]          # noqa: E731
    centers = [345.0, 420.0, 495.0, 570.0]
    assert te._caption_groups([phrase(340, 420), phrase(490, 570)], centers) == [[0, 1], [2, 3]]
    assert te._caption_groups([phrase(340, 420), phrase(490, 570)], centers[:3]) is None      # 2 captions over 3 columns: unclear
    assert te._caption_groups([phrase(340, 360)], centers) is None                           # one caption: it titles the table
    assert te._caption_groups([phrase(330, 350), phrase(405, 430), phrase(480, 505), phrase(555, 580)], centers) is None


def _segment_page():
    """Apple's segment note: one block per year, each headed by the year alone over all the columns."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((40, 50), "The following table shows information by reportable segment (in millions):", fontsize=8)
    edges = [250, 330, 410, 490, 570]
    y = 70
    for year, vals in (("2025", [["178,353", "111,032", "64,377", "28,703", "382,465"], ["72,480", "47,739", "26,917", "13,955", "161,091"]]),
                       ("2024", [["167,045", "101,328", "66,952", "25,052", "360,377"], ["67,656", "41,790", "27,082", "12,454", "148,982"]])):
        _centred(page, 410, y, year)
        for x1, name in zip(edges, ["Americas", "Europe", "China", "Japan", "Total"]):
            _right(page, x1, y + 12, name)
        y += 26
        for label, row in zip(("Net sales", "Operating income"), vals):
            page.insert_text((40, y), label, fontsize=8)
            for x1, v in zip(edges, row):
                _right(page, x1, y, v)
            y += 14
        y += 10
    return doc


def test_a_year_heading_over_a_block_is_kept_with_its_table():
    doc = _segment_page()
    md = te.extract_tables_markdown_for_page(doc[0], 1)
    for n in ("178,353", "161,091", "167,045", "148,982"):
        assert n in md                                                                      # nothing dropped
    first = te.extract_page_tables(doc[0], 1)[0]
    assert "2025" in first["title"] and first["headers"][1:] == ["Americas", "Europe", "China", "Japan", "Total"]
    assert "2025" not in [r[0] for r in first["rows"]]
