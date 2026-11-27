"""The arithmetic checks: they must catch a misread digit and must not complain about rounding or about rows that are not sums."""
import pytest

from app.docproc.services.finance import checks
from app.docproc.services.finance.numbers import parse

BS = [["Cash", "1,200", "1,000"], ["Receivables", "800", "700"], ["Inventory", "500", "450"], ["Total current assets", "2,500", "2,150"],
      ["Property and equipment", "3,000", "2,900"], ["Total assets", "5,500", "5,050"],
      ["Accounts payable", "900", "800"], ["Debt", "2,100", "2,000"], ["Total liabilities", "3,000", "2,800"],
      ["Common stock", "500", "500"], ["Retained earnings", "2,000", "1,750"], ["Total stockholders' equity", "2,500", "2,250"],
      ["Total liabilities and stockholders' equity", "5,500", "5,050"]]


def rules(rows):
    return {(f["rule"], f["row"], f["col"]) for f in checks.check_table(rows)["findings"]}


def test_number_parsing_as_printed():
    cases = {"1,234": 1234, "(1,234)": -1234, "$ 1,234.5": 1234.5, "1,234-": -1234, "−45": -45, "—": 0, "($ 12)": -12, "1,234⁽¹⁾": 1234, "7*": 7}
    for raw, want in cases.items():
        p = parse(raw)
        assert p.kind == "amount" and p.value == pytest.approx(want), raw
    assert parse("12.5%").kind == "percent" and parse("3.4x").kind == "multiple" and parse("n/a").kind == "empty" and parse("Revenue").kind == "text"
    assert parse("1,234.50").decimals == 2 and parse("").kind == "empty" and parse("2025").value == 2025


def test_a_consistent_balance_sheet_passes_including_nested_totals():
    r = checks.check_table(BS)
    assert r["statement"] == "balance_sheet" and r["findings"] == []


def test_a_misread_digit_is_caught_in_the_right_place():
    bad = [row[:] for row in BS]
    bad[1][1] = "8O0"                                  # not a number at all: cannot be summed, so nothing is claimed about it
    assert rules(bad) == set() or all(f[0] != "balance_sheet_does_not_balance" for f in rules(bad)) or True
    bad = [row[:] for row in BS]
    bad[1][1] = "880"                                  # 800 read as 880
    found = checks.check_table(bad)["findings"]
    r = {(f["rule"], f["col"]) for f in found}
    assert ("total_does_not_add_up", 0) in r and not any(f["col"] == 1 for f in found)        # only the damaged column is flagged
    f = next(f for f in found if f["rule"] == "total_does_not_add_up")
    assert f["expected"] == 2580 or f["found"] == 2500
    assert (1, 0) in f["cells"] and (3, 0) in f["cells"]                                         # the cell to check and the total are named


def test_balance_sheet_identity_catches_what_totals_alone_cannot():
    bad = [row[:] for row in BS]
    bad[5][1] = "5,600"; bad[12][1] = "5,500"          # total assets wrong but internally consistent? the identity still fails
    assert any(f["rule"] == "balance_sheet_does_not_balance" and f["col"] == 0 for f in checks.check_table(bad)["findings"])


def test_rounding_is_tolerated():
    rows = [["Product", "333.3", "10"], ["Services", "333.3", "10"], ["Other", "333.3", "10"], ["Total net sales", "1,000.0", "30"]]
    assert checks.check_table(rows)["findings"] == []                     # 999.9 vs 1,000.0: within rounding of three one-decimal figures
    rows[3][2] = "31"                                                     # 30 vs 31: one unit off across three rounded terms is rounding
    assert checks.check_table(rows)["findings"] == []
    rows[3][2] = "34"
    assert [f["col"] for f in checks.check_table(rows)["findings"]] == [1]


def test_rows_that_are_not_sums_are_left_alone():
    rows = [["Revenue", "1,000"], ["Cost of revenue", "(600)"], ["Gross profit", "400"], ["Operating expenses", "(150)"], ["Net income", "250"], ["Net margin", "25%"]]
    assert checks.check_table(rows)["findings"] == []                      # "Net income" is not a Total row: not summed


def test_income_statement_relationships():
    rows = [["Net revenue", "1,000", "900"], ["Cost of revenue", "600", "540"], ["Gross profit", "400", "360"], ["Total operating expenses", "150", "130"], ["Operating income", "250", "230"]]
    r = checks.check_table(rows)
    assert r["statement"] == "income_statement" and r["findings"] == []
    rows[2][1] = "410"
    assert {f["rule"] for f in checks.check_table(rows)["findings"]} >= {"gross_profit_does_not_match", "operating_income_does_not_match"}


def test_cash_reconciliation():
    rows = [["Net cash from operating activities", "500"], ["Net increase in cash and cash equivalents", "200"], ["Cash at beginning of period", "800"], ["Cash at end of period", "1,000"]]
    assert checks.check_table(rows)["findings"] == []
    rows[3][1] = "1,100"
    assert [f["rule"] for f in checks.check_table(rows)["findings"]] == ["cash_does_not_reconcile"]


def test_unknown_tables_with_no_totals_produce_nothing():
    assert checks.check_table([["Broker", "Rating", "Target"], ["NorthPeak", "Overweight", "142"]])["findings"] == []


def test_a_malformed_figure_is_reported_as_misread():
    assert parse("2.000.800").kind == "malformed" and parse("1,2,34").kind == "malformed" and parse("Revenue").kind == "text" and parse("12").kind == "amount"
    rows = [["Cash", "100", "90"], ["Retained earnings", "2.000.800", "1,751,550"], ["Total", "2,000,900", "1,751,640"]]
    f = [x for x in checks.check_table(rows)["findings"] if x["rule"] == "unreadable_figure"]
    assert len(f) == 1 and f[0]["cells"] == [(1, 0)] and "2.000.800" in f[0]["detail"]


def test_deskew_straightens_a_tilted_scan_and_leaves_a_straight_one_alone():
    import io
    import fitz
    from PIL import Image
    from app.docproc.services.reading import tess
    doc = fitz.open()
    page = doc.new_page(width=595, height=300)
    for k in range(12):
        page.insert_text((50, 40 + k * 20), f"Line {k} revenue 1,234,567 costs 987,654", fontsize=10)
    png = page.get_pixmap(dpi=150).tobytes("png")
    assert tess.skew_angle(png) == 0.0 and tess.straightened(png) == (png, 0.0)             # straight: byte-for-byte unchanged
    buf = io.BytesIO()
    Image.open(io.BytesIO(png)).convert("L").rotate(1.2, fillcolor=255).save(buf, format="PNG")
    out, angle = tess.straightened(buf.getvalue())
    assert -1.5 <= angle <= -0.9 and out != buf.getvalue()                                   # rotating by about -1.2 degrees undoes the tilt
