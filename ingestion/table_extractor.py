"""
Structured table extraction for native (digital, non-scanned) PDF pages —
adapted from a prior GraphRAG POC parser for this codebase's needs.

Problem being solved: `page.get_text("text")` (used for the main text chunks
in pdf_processor1.py) flattens tables into fragmented, column-order-losing
prose — fine for narrative text, poor for financial tables where column
alignment IS the data (e.g. a balance sheet's "2024 | 2023 | 2022" columns).

Approach:
1. `page.find_tables()` (PyMuPDF's built-in detector) locates table areas and
   row structure.
2. Vertical ruling lines (from the PDF's own drawing paths) are used to
   determine the TRUE column boundaries — find_tables() can miss a column
   when its divider line only spans part of the table height (e.g. a nested
   sub-box in one row). Ruling-line detection gives exact column regions.
3. Cell text is extracted by querying each [col_x0..col_x1] x [row_y0..row_y1]
   rectangle against the page's text blocks — handles multi-line cells.
4. Falls back to find_tables()'s own cell extraction when no ruling lines are
   detected (e.g. borderless/text-only tables).

Deliberately dropped from the source this was adapted from (not relevant to
digital SEC filings, which are never scanned images): OCR fallback for
scanned pages, img2table-based raster table detection, and document-type
classification.
"""

import logging

import fitz

logger = logging.getLogger("ingestion.table_extractor")


def get_vertical_lines(page) -> list:
    """Return (x, y0, y1) for every vertical ruling line on the page.

    Deduplicates lines within 3pt of each other (handles line borders drawn
    as thin filled rectangles with left-edge and right-edge very close).
    """
    raw = []
    for path in page.get_drawings():
        rect = fitz.Rect(path.get("rect", (0, 0, 0, 0)))
        w = rect.x1 - rect.x0
        h = rect.y1 - rect.y0
        if w < 3 and h > 15:  # thin, tall -> vertical line
            mid_x = (rect.x0 + rect.x1) / 2
            raw.append((mid_x, rect.y0, rect.y1))

    raw.sort()
    deduped = []
    for x, y0, y1 in raw:
        if deduped and abs(x - deduped[-1][0]) < 3:
            px, py0, py1 = deduped[-1]
            deduped[-1] = (px, min(py0, y0), max(py1, y1))
        else:
            deduped.append((x, y0, y1))
    return deduped


def extract_text_in_rect(blocks: list, rect: "fitz.Rect") -> str:
    """Return all text from `blocks` whose centre-line falls within `rect`."""
    lines = []
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            bbox = line.get("bbox", (0, 0, 0, 0))
            line_x = bbox[0]
            line_y = (bbox[1] + bbox[3]) / 2
            if rect.x0 <= line_x <= rect.x1 and rect.y0 <= line_y <= rect.y1:
                parts = [
                    s.get("text", "").strip()
                    for s in line.get("spans", [])
                    if s.get("text", "").strip()
                ]
                if parts:
                    lines.append(" ".join(parts))
    return "\n".join(lines)


def reconstruct_table_with_ruling_lines(page, table_obj, v_lines: list, page_num: int):
    """Reconstruct a table using vertical ruling lines as column boundaries.

    Returns {"page": int, "headers": list[str], "rows": list[list[str]]} or
    None if no usable ruling-line structure is found (caller falls back to
    find_tables()'s own extraction).
    """
    try:
        bbox = table_obj.bbox
        cells_obj = table_obj.cells
    except Exception:
        return None

    tab_y0, tab_y1 = bbox[1], bbox[3]

    col_xs = [x for x, vy0, vy1 in v_lines if vy0 < tab_y1 and vy1 > tab_y0]
    if len(col_xs) < 2:
        return None

    col_xs = sorted(set(round(x, 1) for x in col_xs))
    col_regions = [(col_xs[i], col_xs[i + 1]) for i in range(len(col_xs) - 1)]

    y_vals = {round(tab_y0, 1), round(tab_y1, 1)}
    for cell_rect in cells_obj:
        if cell_rect is not None:
            r = fitz.Rect(cell_rect)
            y_vals.add(round(r.y0, 1))
            y_vals.add(round(r.y1, 1))
    row_bounds = sorted(y_vals)

    all_blocks = page.get_text("dict")["blocks"]

    grid = []
    for i in range(len(row_bounds) - 1):
        y_top = row_bounds[i] - 1
        y_bot = row_bounds[i + 1] + 1

        row = []
        for cx0, cx1 in col_regions:
            cell_rect = fitz.Rect(cx0 - 1, y_top, cx1 + 1, y_bot)
            row.append(extract_text_in_rect(all_blocks, cell_rect))

        if any(row):
            grid.append(row)

    if not grid:
        return None

    return {"page": page_num, "headers": grid[0], "rows": grid[1:]}


def extract_page_tables(page, page_num: int) -> list:
    """Extract structured tables from a page.

    Returns a list of {"page": int, "headers": list[str], "rows": list[list[str]]}.
    Empty list if the page has no detectable tables.
    """
    tables = []
    try:
        tab_finder = page.find_tables()
        if not tab_finder.tables:
            return tables

        v_lines = get_vertical_lines(page)

        for t in tab_finder.tables:
            table = reconstruct_table_with_ruling_lines(page, t, v_lines, page_num)

            if table is None:
                cells = t.extract()
                if not cells:
                    continue
                headers = [str(c or "").strip() for c in cells[0]]
                rows = [[str(c or "").strip() for c in row] for row in cells[1:]]
                all_cells = headers + [cell for row in rows for cell in row]
                if any(all_cells):
                    table = {"page": page_num, "headers": headers, "rows": rows}

            if table is not None:
                all_text = table["headers"] + [c for row in table["rows"] for c in row]
                if any(all_text):
                    tables.append(table)

    except Exception as e:
        logger.warning("Table extraction failed on page %s: %s", page_num, e)
    return tables


def table_to_markdown(table: dict) -> str:
    """Convert a {"headers", "rows"} table dict to a GitHub-flavoured Markdown table."""
    if not table["headers"]:
        return ""
    lines = ["| " + " | ".join(table["headers"] or ["Col1"]) + " |"]
    lines.append("| " + " | ".join("---" for _ in table["headers"]) + " |")
    for row in table["rows"]:
        padded = list(row) + [""] * max(0, len(table["headers"]) - len(row))
        lines.append("| " + " | ".join(padded[: len(table["headers"])]) + " |")
    return "\n".join(lines)


def extract_tables_markdown_for_page(page, page_num: int) -> str:
    """
    Convenience wrapper: extract all tables on a page and render them as one
    combined markdown block. Returns "" if the page has no detectable tables
    (the common case — most 10-K/10-Q pages are narrative text).
    """
    tables = extract_page_tables(page, page_num)
    if not tables:
        return ""
    return "\n\n".join(table_to_markdown(t) for t in tables)
