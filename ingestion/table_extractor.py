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
import re
from statistics import median

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



# ---------------------------------------------------------------------------
# Word-position reconstruction (financial statements)
#
# find_tables() + ruling lines drop the row-label column and split multi-row
# headers whenever a statement is drawn with shaded bands instead of full grid
# lines (e.g. Alphabet's 10-Q). Financial tables are right-aligned numeric
# columns, so the columns are recovered directly from the numbers' right edges.
# ---------------------------------------------------------------------------

_NUM_CLEAN = re.compile(r"[\s$%,()\u00a0]")
_YEAR = re.compile(r"^(19|20)\d\d$")
_DASHES = {"—", "–", "-", "— ", "−", "$—", "$–", "$-"}
COL_CLUSTER_TOL = 12.0   # pt: max right-edge spread inside one column (")" vs digit, etc.)
HEADER_REACH = 150.0     # pt: how far above find_tables' bbox to look for column headers


def _is_num(text: str) -> bool:
    t = text.strip()
    if t in _DASHES:
        return True
    t = _NUM_CLEAN.sub("", t).lstrip("-−")
    return bool(t) and bool(re.fullmatch(r"\d+(\.\d+)?", t))


def _split_words(page, y0: float, y1: float) -> list:
    """Words (x0, y0, x1, y1, text) in the vertical band, with NBSP-joined tokens split."""
    out = []
    for w in page.get_text("words"):
        if w[3] < y0 or w[1] > y1:
            continue
        parts = [p for p in w[4].split("\u00a0") if p.strip()]
        if len(parts) <= 1:
            out.append((w[0], w[1], w[2], w[3], w[4].strip()))
            continue
        width = (w[2] - w[0]) / max(1, len("".join(parts)))
        x = w[0]
        for part in parts:
            out.append((x, w[1], x + width * len(part), w[3], part))
            x += width * len(part)
    return out


def _group_lines(words: list) -> list:
    """Cluster words into visual lines by vertical centre; each line sorted left to right."""
    lines = []
    for w in sorted(words, key=lambda w: (w[1] + w[3]) / 2):
        cy, h = (w[1] + w[3]) / 2, w[3] - w[1]
        for ln in lines:
            if abs(ln["cy"] - cy) <= 0.55 * max(h, ln["h"]):
                ln["words"].append(w)
                ln["cy"] = sum((x[1] + x[3]) / 2 for x in ln["words"]) / len(ln["words"])
                ln["h"] = max(ln["h"], h)
                break
        else:
            lines.append({"cy": cy, "h": h, "words": [w]})
    for ln in lines:
        ln["words"].sort(key=lambda w: w[0])
    return sorted(lines, key=lambda ln: ln["cy"])


def _cluster_right_edges(tokens: list) -> list:
    """tokens: [(x0, x1)] -> columns [{"x0", "x1", "n"}] clustered by right edge."""
    cols = []
    for x0, x1 in sorted(tokens, key=lambda t: t[1]):
        if cols and x1 - cols[-1]["x1_max"] <= COL_CLUSTER_TOL:
            c = cols[-1]
            c["x0"], c["x1_max"], c["n"] = min(c["x0"], x0), x1, c["n"] + 1
            c["sum"] += x1
        else:
            cols.append({"x0": x0, "x1_max": x1, "n": 1, "sum": x1})
    for c in cols:
        c["x1"] = c["sum"] / c["n"]
    return cols


def _join(words: list) -> str:
    return " ".join(w[4] for w in words).strip()


_UNITS_NOTE = re.compile(r"(?i)\(.*\b(in|except)\b.*(millions|thousands|billions|per share).*\)")
TITLE_REACH = 45.0           # pt above the column captions that may hold the statement title
ROW_EXTENSION_REACH = 40.0   # pt below find_tables' bottom edge that may hold an aligned totals row
CAPTION_MAX_PHRASE_WORDS = 10


def _starts_like_caption(words: list) -> bool:
    """A caption starts with a capital, digit or bracket; "were as follows:" is the tail of the sentence above."""
    text = _join(words)
    return bool(text) and (text[0].isupper() or text[0].isdigit() or text[0] in "($") and not text.endswith(":")


def _is_caption_line(words: list) -> bool:
    """Column captions: phrases separated by wide gaps, each short. A paragraph line is one long unbroken phrase."""
    phrase, longest = 0, 0
    for k, w in enumerate(words):
        phrase = 1 if k == 0 or w[0] - words[k - 1][2] > 14 else phrase + 1
        longest = max(longest, phrase)
    return bool(words) and longest <= CAPTION_MAX_PHRASE_WORDS


def reconstruct_table_by_words(page, bbox, prev_bottom: float, page_num: int):
    """Rebuild a numeric (financial) table from word positions.

    Returns ({"page", "headers", "rows"}, (x0, y0, x1, y1)) or None when the region doesn't look like a
    right-aligned numeric table (caller then falls back to the ruling-line / find_tables extraction).
    """
    top = max(prev_bottom, bbox[1] - HEADER_REACH)
    words = _split_words(page, top, bbox[3] + ROW_EXTENSION_REACH)  # a little below: totals rows can sit under the detected edge
    if not words:
        return None
    lines = _group_lines(words)

    # Data rows = lines inside find_tables' vertical extent that carry numbers (not a bare years line).
    def nums(ln):
        return [w for w in ln["words"] if _is_num(w[4])]

    in_body = [ln for ln in lines if bbox[1] - 2 <= ln["cy"] <= bbox[3] + 2]
    data = [ln for ln in in_body if nums(ln) and not all(_YEAR.match(w[4]) for w in nums(ln))]
    if len(data) < 2:
        return None

    tokens = [(w[0], w[2]) for ln in data for w in nums(ln)]  # dashes ("—") are values too: a Level 1 column may hold only dashes
    if len(tokens) < 4:
        return None
    clusters = _cluster_right_edges(tokens)
    # Real columns carry a value on most rows; numbers quoted inside row labels ("of $190, $(36)") don't.
    min_support = max(2 if len(data) > 2 else 1, -(-len(data) * 35 // 100))
    cols = [c for c in clusters if c["n"] >= min_support]
    if not cols:
        return None
    cols.sort(key=lambda c: c["x1"])

    # Everything left of the first numeric column is the row label. A standalone "$" / "%" sitting just left
    # of a value belongs to that value.
    first = cols[0]
    edge = first["x0"] - 2

    # The detector's bottom edge can stop just above a totals row (its numbers sit on the underline). Keep taking
    # the following lines while they hold numbers whose right edges line up with the known columns.
    below = [ln for ln in lines if ln["cy"] > bbox[3] + 2]
    last = in_body[-1] if in_body else None
    for ln in below:
        vals = [w for w in nums(ln) if w[4] not in _DASHES and w[0] >= edge]  # label numbers ("June 30, 2026") don't count
        aligned = bool(vals) and all(any(abs(c["x1"] - w[2]) <= 1.5 * COL_CLUSTER_TOL for c in cols) for w in vals)
        close = last is not None and ln["cy"] - last["cy"] <= 3.0 * max(ln["h"], last["h"])
        if not (aligned and close):
            break
        in_body.append(ln)
        last = ln

    def col_index(w):
        return min(range(len(cols)), key=lambda i: abs(cols[i]["x1"] - w[2]))

    def row_cells(ln):
        cells = [[] for _ in cols]
        label = []
        words_ = ln["words"]
        for k, w in enumerate(words_):
            nxt = words_[k + 1] if k + 1 < len(words_) else None
            sign_of_value = w[4] == "$" and nxt is not None and nxt[0] >= edge and _is_num(nxt[4])
            if w[0] < edge and not sign_of_value:
                label.append(w)
            elif w[4] == "$":
                continue
            else:
                prev = words_[k - 1] if k else None
                txt = ("$" + w[4]) if prev is not None and prev[4] == "$" and _is_num(w[4]) else w[4]
                cells[col_index(w)].append((w[0], txt))
        return label, [" ".join(t for _, t in sorted(c)) for c in cells]

    # Header block: contiguous lines above the first data row that live entirely in the numeric area.
    first_data_cy = data[0]["cy"]
    above = [ln for ln in lines if ln["cy"] < first_data_cy - 1 and ln["cy"] >= top]
    header_lines = []
    centers = [(c["x0"] + c["x1"]) / 2 for c in cols]
    pitch = median([b - a for a, b in zip(centers, centers[1:])]) if len(cols) > 1 else 0.0
    caption_left = edge - max(25.0, 1.2 * pitch)  # wide group captions start left of the first numeric column
    unit_parts, header_src = [], []
    for ln in reversed(above):
        left = [w for w in ln["words"] if w[0] < caption_left]
        right = [w for w in ln["words"] if w[0] >= caption_left]
        short = _is_caption_line(right)
        if short and not left:
            header_lines.insert(0, ln)
            header_src.append(ln)
        elif short and right and 0 < len(left) <= 6 and not any(_is_num(w[4]) for w in left):
            unit_parts.insert(0, _join(left))  # a units label such as "(Dollars in Millions)" sharing the captions' line
            header_lines.insert(0, {**ln, "words": right})
            header_src.append(ln)
        elif not header_lines and ln in in_body and all(w[2] < caption_left + 1 or w[0] < edge for w in ln["words"]):
            continue  # a section label above the first row ("Assets", "Current assets:"): the captions are further up
        else:
            break
    col_headers = [[] for _ in cols]
    spanning = []
    for ln in header_lines:
        phrases, cur = [], []
        for w in ln["words"]:
            if cur and w[0] - cur[-1][2] > 14:
                phrases.append(cur); cur = []
            cur.append(w)
        if cur:
            phrases.append(cur)
        if len(phrases) == 1 and len(cols) >= 3:  # one caption over a wide table ("Three Months Ended ...") titles the table
            spanning.append(_join(phrases[0]))
            continue
        for ph in phrases:
            px0, px1, text = ph[0][0], ph[-1][2], _join(ph)
            hit = [i for i, c in enumerate(cols) if px0 - 8 <= (c["x0"] + c["x1"]) / 2 <= px1 + 8]
            if not hit:
                hit = [min(range(len(cols)), key=lambda i: abs((cols[i]["x0"] + cols[i]["x1"]) / 2 - (px0 + px1) / 2))]
            for i in hit:
                col_headers[i].append(text)

    # Body rows. A label-only line that doesn't end in ":" is the start of the next row's wrapped label.
    rows, carry = [], []
    for ln in in_body:
        label_words, cells = row_cells(ln)
        label = _join(label_words)
        has_values = any(cells)
        if not has_values and _UNITS_NOTE.fullmatch(label):
            unit_parts.append(label)  # "(Dollars in Millions)" describes the table; it is not a row label
            continue
        if not has_values:
            if label.endswith(":") or not label:
                rows.append([" ".join(carry + [label]).strip()] + [""] * len(cols) if label else None)
                carry = []
            else:
                carry.append(label)
            continue
        rows.append([" ".join(carry + [label]).strip()] + cells)
        carry = []
    rows = [r for r in rows if r]
    if carry:
        rows.append([" ".join(carry)] + [""] * len(cols))

    # Statement caption: the lines just above the column captions ("CONSOLIDATED STATEMENTS OF ...", "(in millions)").
    header_top = min((ln["cy"] for ln in header_lines), default=first_data_cy)
    title_lines = [ln for ln in above if ln not in header_src and ln not in in_body and header_top - ln["cy"] <= TITLE_REACH
                   and _is_caption_line(ln["words"]) and _starts_like_caption(ln["words"])][-3:]  # captions only, never prose
    title_parts = [_join(ln["words"]) for ln in title_lines] + unit_parts + spanning
    title = re.sub(r"(?i)table of contents", "", " ".join(t for t in title_parts if not t.isdigit()))
    title = " ".join(title.split())[:200]

    headers = ["Line item"] + [" ".join(h) if h else f"Col {i + 1}" for i, h in enumerate(col_headers)]
    all_words = [w for ln in header_lines + in_body for w in ln["words"]]
    table_bbox = (min(w[0] for w in all_words), min(w[1] for w in all_words),
                  max(w[2] for w in all_words), max(w[3] for w in all_words))
    return {"page": page_num, "headers": headers, "rows": rows, "title": title}, table_bbox



_DIGITS = re.compile(r"\d[\d,.]*")


def _numbers(text: str):
    from collections import Counter
    return Counter(n.strip(".,") for n in _DIGITS.findall(text or ""))


def _loses_numbers(page, table: dict, box) -> bool:
    """True if any number printed inside `box` is missing from the rebuilt table (never silently drop data)."""
    x0, y0, x1, y1 = box
    raw = " ".join(w[4] for w in page.get_text("words")
                   if w[0] >= x0 - 1 and w[2] <= x1 + 1 and w[1] >= y0 - 1 and w[3] <= y1 + 1)
    cells = [h for h in table["headers"] if not re.fullmatch(r"(Col \d+|Line item)", h)]
    cells += [c for row in table["rows"] for c in row] + [table.get("title") or ""]
    missing = _numbers(raw) - _numbers(" ".join(cells))
    return bool(missing)


PROSE_MIN_WORDS = 8
PROSE_MIN_STOPWORDS = 2
PROSE_MAX_NUMERIC_SHARE = 0.3
NUMBER_RICH = 12
_STOPWORDS = {"the", "of", "and", "to", "in", "our", "were", "we", "that", "for", "as", "is", "are", "by", "its",
              "an", "be", "on", "with", "from", "this", "which", "or", "has", "have", "was", "at"}
_PUNCT_ONLY = re.compile(r"[\s$()%,.\-—–]*")
_NUMBER_CELL = re.compile(r"[($\s-]*\d[\d,.]*\s*[)%]*\s*(bn|m)?")


def _is_prose_cell(cell: str) -> bool:
    words = cell.lower().split()
    return len(words) >= PROSE_MIN_WORDS and sum(1 for w in words if w.strip(".,;:()") in _STOPWORDS) >= PROSE_MIN_STOPWORDS


def _is_data_table(table: dict) -> bool:
    """False only for regions that are clearly body text: a lone non-empty cell (a section caption such as
    "Revenue:"), or sentence-like cells in a region with few numeric cells. The detector reports paragraphs, footnotes
    and captions as tables; real tables, including text-only ones (names, dates, terms), have short cells and are kept.
    Dropping a region never loses data: the page text chunk still contains every word and number."""
    cells = [c.strip().replace("\u00a0", " ") for c in table["headers"] + [c for r in table["rows"] for c in r]]
    cells = [c for c in cells if not _PUNCT_ONLY.fullmatch(c)]
    if len(cells) <= 1:
        return False
    if sum(_numbers(" ".join(cells)).values()) >= NUMBER_RICH:
        return True  # a number-rich region is data even when its cells hold long text (exhibit / patent tables)
    numeric = sum(1 for c in cells if _NUMBER_CELL.fullmatch(c))
    if numeric / len(cells) < PROSE_MAX_NUMERIC_SHARE and any(_is_prose_cell(c) for c in cells):
        return False
    return True



# ---------------------------------------------------------------------------
# Borderless tables: runs of lines with right-aligned numbers that find_tables() never reports
# ---------------------------------------------------------------------------

BORDERLESS_MIN_ROWS = 3
BORDERLESS_LINE_GAP = 3.2     # x line height: max vertical gap between lines of one block
BORDERLESS_LABEL_WORDS = 14   # a row label is at most this many words before the first value
BORDERLESS_ALIGNED_SHARE = 0.6


def _value_tokens(ln: dict) -> list:
    return [w for w in ln["words"] if _is_num(w[4])]


def _is_data_line(ln: dict) -> bool:
    """>= 2 numeric/dash values, a short label before them, and not just a line of years (a column caption)."""
    vals = _value_tokens(ln)
    if len(vals) < 2 or all(_YEAR.match(w[4]) for w in vals):
        return False
    first, last = vals[0], vals[-1]
    label_words = [w for w in ln["words"] if w[2] <= first[0] + 1]
    between = [w for w in ln["words"] if first[2] - 1 <= w[0] and w[2] <= last[2] + 1 and not _is_num(w[4]) and w[4] != "$"]
    if any(re.search(r"[A-Za-z]", w[4]) for w in between):  # "Level 1  Level 2  Level 3": column captions, not values
        return False
    return len(label_words) <= BORDERLESS_LABEL_WORDS


def _is_label_only(ln: dict) -> bool:
    return not _value_tokens(ln) and len(ln["words"]) <= 12


def find_borderless_blocks(page, covered: list) -> list:
    """Bounding boxes of table-like blocks outside `covered` [(y0, y1), ...] (regions already extracted)."""
    words = _split_words(page, 0, page.rect.height)
    lines = _group_lines(words)
    blocks, cur = [], None

    def overlaps(ln):
        return any(y0 - 2 <= ln["cy"] <= y1 + 2 for y0, y1 in covered)

    def close():
        nonlocal cur
        if cur and cur["data"] >= BORDERLESS_MIN_ROWS:
            # section labels directly above the first value row ("Derivatives designated ...:", "Assets:") are rows too
            k, first = cur["start"], cur["lines"][0]
            while k > 0:
                prev = lines[k - 1]
                if (_is_label_only(prev) and len(prev["words"]) <= 10 and not overlaps(prev)
                        and first["cy"] - prev["cy"] <= BORDERLESS_LINE_GAP * max(first["h"], prev["h"])):
                    cur["lines"].insert(0, prev)
                    first = prev
                    k -= 1
                else:
                    break
            ws = [w for ln in cur["lines"] for w in ln["words"]]
            blocks.append((min(w[0] for w in ws), min(w[1] for w in ws), max(w[2] for w in ws), max(w[3] for w in ws), cur["lines"]))
        cur = None

    for i, ln in enumerate(lines):
        if overlaps(ln):
            close()
            continue
        gap_ok = cur is not None and ln["cy"] - cur["last"]["cy"] <= BORDERLESS_LINE_GAP * max(ln["h"], cur["last"]["h"])
        if _is_data_line(ln):
            if cur is not None and not gap_ok:
                close()
            if cur is None:
                cur = {"lines": [], "data": 0, "last": ln, "start": i}
            cur["lines"].append(ln)
            cur["data"] += 1
            cur["last"] = ln
        elif cur is not None and gap_ok and _is_label_only(ln) and any(
                _is_data_line(n) and n["cy"] - ln["cy"] <= BORDERLESS_LINE_GAP * ln["h"] for n in lines[i + 1:i + 3]):
            cur["lines"].append(ln)  # a section label or the first half of a wrapped label
            cur["last"] = ln
        else:
            close()
    close()

    out = []
    for x0, y0, x1, y1, blines in blocks:
        data = [ln for ln in blines if _is_data_line(ln)]
        edges = _cluster_right_edges([(w[0], w[2]) for ln in data for w in _value_tokens(ln)])
        strong = [c for c in edges if c["n"] >= max(2, BORDERLESS_ALIGNED_SHARE * len(data))]
        if len(strong) >= 2:  # real columns: values on most rows share right edges
            out.append((x0, y0, x1, y1))
    return out


MERGE_GAP_FRAGMENTS = 42.0  # pt: row-sized fragments (height <= FRAGMENT_HEIGHT) merge across a blank row
FRAGMENT_HEIGHT = 20.0
MERGE_GAP = 26.0         # pt: detector regions closer than this vertically are parts of one table
MERGE_X_OVERLAP = 0.6    # share of the narrower region's width that must overlap horizontally


def _group_regions(tables: list) -> list:
    """Group find_tables() regions that are stacked closely and line up horizontally.

    The detector often reports every row (or every section) of one financial statement as its own tiny table;
    those are one logical table. Groups are returned top to bottom as lists of Table objects."""
    groups = []
    for t in sorted(tables, key=lambda t: t.bbox[1]):
        x0, y0, x1, y1 = t.bbox
        if groups:
            g = groups[-1]
            gx0, gy0, gx1, gy1 = (min(m.bbox[0] for m in g), min(m.bbox[1] for m in g),
                                  max(m.bbox[2] for m in g), max(m.bbox[3] for m in g))
            overlap = min(x1, gx1) - max(x0, gx0)
            narrow = max(1.0, min(x1 - x0, gx1 - gx0))
            small = (y1 - y0) <= FRAGMENT_HEIGHT and (g[-1].bbox[3] - g[-1].bbox[1]) <= FRAGMENT_HEIGHT
            gap_limit = MERGE_GAP_FRAGMENTS if small else MERGE_GAP
            if y0 - gy1 <= gap_limit and overlap / narrow >= MERGE_X_OVERLAP:
                g.append(t)
                continue
        groups.append([t])
    return groups


def _union(group: list):
    return (min(t.bbox[0] for t in group), min(t.bbox[1] for t in group),
            max(t.bbox[2] for t in group), max(t.bbox[3] for t in group))


def extract_page_tables_with_bbox(page, page_num: int) -> list:
    """Extract structured tables from a page as [(table, bbox_or_None), ...].

    table = {"page": int, "headers": list[str], "rows": list[list[str]], "title": str}; bbox is in PDF points.
    Closely stacked detector regions are merged into one table. Numeric (financial) tables are rebuilt from word
    positions; anything else (or a rebuild that would drop a number) falls back to the ruling-line reconstruction,
    then to find_tables()'s own cells.
    """
    out = []
    tab_finder = None
    try:
        tab_finder = page.find_tables()
        if tab_finder is None:  # transient PyMuPDF failure: retry once rather than silently dropping the page's tables
            tab_finder = page.find_tables()
        detected = tab_finder.tables if tab_finder is not None else []

        v_lines = get_vertical_lines(page)
        prev_bottom = 0.0

        def rebuild(bbox):
            try:
                built = reconstruct_table_by_words(page, bbox, prev_bottom, page_num)
            except Exception as e:
                logger.warning("Word-position table rebuild failed on page %s: %s", page_num, e)
                return None
            if built and _loses_numbers(page, *built):
                logger.warning("Word-position rebuild dropped numbers on page %s; using ruling-line extraction", page_num)
                return None
            return built

        for group in _group_regions(detected):
            if len(group) > 1:
                built = rebuild(_union(group))
                if built:
                    table, box = built
                    prev_bottom = max(prev_bottom, box[3])
                    if _is_data_table(table):
                        out.append((table, tuple(round(v, 2) for v in box)))
                    continue
                # could not rebuild the merged table cleanly: handle the regions one by one, as before

            for t in group:
                built = rebuild(t.bbox)
                if built:
                    table, box = built
                    prev_bottom = max(prev_bottom, box[3])
                    if _is_data_table(table):
                        out.append((table, tuple(round(v, 2) for v in box)))
                    continue

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

                if table is not None and table["rows"]:  # a header-only leftover carries no data (the page text keeps it)
                    all_text = table["headers"] + [c for row in table["rows"] for c in row]
                    if any(all_text):
                        prev_bottom = max(prev_bottom, t.bbox[3])
                        if _is_data_table(table):
                            out.append((table, tuple(round(float(v), 2) for v in t.bbox)))

    except Exception as e:
        logger.warning("Table extraction failed on page %s: %s", page_num, e)

    # Tables with no ruling lines are invisible to find_tables(): pick them up from word positions.
    try:
        covered = [(bb[1], bb[3]) for _, bb in out]  # only what was actually extracted: dropped fragments may still be found
        for x0, y0, x1, y1 in find_borderless_blocks(page, covered):
            above = [bb[3] for _, bb in out if bb[3] <= y0 + 1]
            built = reconstruct_table_by_words(page, (x0, y0, x1, y1), max(above, default=0.0), page_num)
            if built and not _loses_numbers(page, *built) and _is_data_table(built[0]):
                out.append((built[0], tuple(round(v, 2) for v in built[1])))
        out.sort(key=lambda tb: tb[1][1])
    except Exception as e:
        logger.warning("Borderless table detection failed on page %s: %s", page_num, e)
    return out


def extract_page_tables(page, page_num: int) -> list:
    """Extract structured tables from a page.

    Returns a list of {"page": int, "headers": list[str], "rows": list[list[str]]}.
    Empty list if the page has no detectable tables.
    """
    return [t for t, _ in extract_page_tables_with_bbox(page, page_num)]


def table_to_markdown(table: dict) -> str:
    """Convert a {"headers", "rows"} table dict to a GitHub-flavoured Markdown table."""
    if not table["headers"]:
        return ""
    lines = [f"Table: {table['title']}"] if table.get("title") else []
    lines.append("| " + " | ".join(table["headers"] or ["Col1"]) + " |")
    lines.append("| " + " | ".join("---" for _ in table["headers"]) + " |")
    for row in table["rows"]:
        padded = list(row) + [""] * max(0, len(table["headers"]) - len(row))
        lines.append("| " + " | ".join(padded[: len(table["headers"])]) + " |")
    return "\n".join(lines)


def extract_table_markdowns_for_page(page, page_num: int) -> list:
    """One markdown string per table on the page (so the chunker can keep each table whole)."""
    return [md for md in (table_to_markdown(t) for t in extract_page_tables(page, page_num)) if md]


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
