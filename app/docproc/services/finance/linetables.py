"""
Tables from OCR lines, for pages where layout analysis found none.

Local OCR already separates the cells of a row with " | " (gap in a line). Financial statements right-align their numbers, so their cells do
not share a left edge and column detection built on left edges misses the table entirely: the figures then sit in the page text as plain lines.
This rebuilds the table from those lines: a run of rows that each have a label and at least one number is a table, and a nearby row of
period labels ("FY2025 | FY2024", "Q3 2025 | Q3 2024") is its header.
"""
import re
from typing import Any, Dict, List, Tuple

from .numbers import parse

_PERIOD = re.compile(r"^(FY|CY|Q[1-4]|H[12]|1H|2H|YTD)?\s*'?(19|20)\d{2}$|^(Q[1-4]|H[12])\s*'?(19|20)?\d{2}$|^(year|quarter|three|six|nine|twelve)\b|^\d{4}$|^as (of|at)\b", re.I)
MIN_ROWS = 3


def _cells(line: str) -> List[str]:
    return [c.strip() for c in line.split(" | ")]


def _numeric(cell: str) -> bool:
    return parse(cell).kind in ("amount", "percent", "multiple") or parse(cell).kind == "empty" and cell.strip() != ""


def _is_data_row(cells: List[str]) -> bool:
    if len(cells) < 2 or not cells[0] or parse(cells[0]).kind != "text":
        return False
    vals = cells[1:]
    return sum(1 for c in vals if parse(c).kind in ("amount", "percent", "multiple")) >= max(1, len(vals) // 2 + (len(vals) % 2))


def _is_header(cells: List[str], width: int) -> bool:
    return len(cells) in (width - 1, width) and all(c == "" or _PERIOD.match(c.strip()) or (parse(c).kind == "text" and len(c) <= 24) for c in cells) and any(_PERIOD.match(c.strip()) for c in cells)


def tables_from_lines(lines: List[str]) -> List[Dict[str, Any]]:
    """[{"title", "columns", "rows", "line_range"}] for each run of at least MIN_ROWS data rows. Rows keep the cells exactly as read."""
    rows = [_cells(l) for l in lines]
    out: List[Dict[str, Any]] = []
    i = 0
    while i < len(rows):
        if not _is_data_row(rows[i]):
            i += 1
            continue
        j = i
        while j < len(rows) and (_is_data_row(rows[j]) or (rows[j][0] and all(c == "" for c in rows[j][1:]) and len(rows[j][0]) <= 48 and parse(rows[j][0]).kind == "text" and j + 1 < len(rows) and _is_data_row(rows[j + 1]))):
            j += 1                                              # a label-only row between data rows is a section heading inside the table
        run = rows[i:j]
        if sum(1 for r in run if _is_data_row(r)) >= MIN_ROWS:
            width = max(len(r) for r in run)
            header, title, start = None, None, i
            if i > 0 and _is_header(rows[i - 1], width):
                header, start = rows[i - 1], i - 1
            if start > 0 and len(rows[start - 1]) == 1 and rows[start - 1][0]:
                title = rows[start - 1][0]
            cols = (["Item"] + header[-(width - 1):]) if header else ["Item"] + [f"Column {k}" for k in range(1, width)]
            body = [r + [""] * (width - len(r)) for r in run]
            out.append({"title": title, "columns": cols, "rows": body, "line_range": (i, j)})
        i = max(j, i + 1)
    return out
