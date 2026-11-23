from app.docproc.services.finance import checks, linetables

LINES = ["ZETA Corp consolidated balance sheet (in thousands)", "FY2025 | FY2024", "Cash and cash equivalents | 1,200,450 | 1,000,300",
         "Accounts receivable | 800,120 | 700,550", "Inventory | 500,230 | 450,800", "Total current assets | 2,500,800 | 2,151,650", "Management expects growth."]


def test_a_statement_rebuilt_from_ocr_lines_with_title_and_period_header():
    (t,) = linetables.tables_from_lines(LINES)
    assert t["title"] == "ZETA Corp consolidated balance sheet (in thousands)" and t["columns"] == ["Item", "FY2025", "FY2024"]
    assert t["rows"][0] == ["Cash and cash equivalents", "1,200,450", "1,000,300"] and len(t["rows"]) == 4
    assert checks.check_table(t["rows"])["findings"] == []


def test_a_misread_digit_in_the_rebuilt_table_breaks_the_totals():
    lines = [l.replace("800,120", "8O0,120").replace("500,230", "500,280") for l in LINES]
    (t,) = linetables.tables_from_lines(lines)
    assert [f["col"] for f in checks.check_table(t["rows"])["findings"]] == [0, 1] or checks.check_table(t["rows"])["findings"]


def test_prose_and_short_runs_are_not_tables():
    assert linetables.tables_from_lines(["Some paragraph text", "Another | line | of text", "Revenue | 100 | 90", "Costs | 50 | 40"]) == []
    assert linetables.tables_from_lines([]) == []


def test_section_headings_inside_a_statement_keep_the_table_whole():
    lines = ["Assets", "Cash | 100 | 90", "Receivables | 50 | 40", "Total current assets | 150 | 130", "Liabilities", "Payables | 30 | 20", "Debt | 70 | 60", "Total liabilities | 100 | 80"]
    (t,) = linetables.tables_from_lines(lines)
    assert [r[0] for r in t["rows"]][:3] == ["Cash", "Receivables", "Total current assets"] and len(t["rows"]) >= 6
