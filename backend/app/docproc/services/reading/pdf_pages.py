"""
PyMuPDF layer: open a PDF and turn each page into a plain dict (size, embedded text, words/blocks with bboxes, tables,
scan detection) plus a JPEG render for the vision readers.

PyMuPDF (MuPDF) is not thread-safe; every use goes through FITZ_LOCK.
"""
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

import pymupdf as fitz

from app.docproc.services.reading import ocr

FITZ_LOCK = threading.RLock()

# Fax machines stamp every page twice: a header ("From <name> <number>  <date>  Page 1 of 9") and a footer
# ("PAGE 1/9 * RCVD AT ... * DURATION ..."). They are transmission metadata, not content.
# Spaces are optional throughout: some OCR engines drop them ("WedNov1109:21:382026MSTPage3of9").
_FAX_HEADER_RE = re.compile(r"^\s*From\b.*Page\s*(\d+)\s*of\s*(\d+)\s*$", re.I)
_FAX_FOOTER_RE = re.compile(r"^\s*PAGE\s*(\d+)\s*/\s*(\d+)\s*\*\s*RCVD\s*AT", re.I)
_FOOTER_FIELDS = {
    "received_at": re.compile(r"RCVD AT\s+(.+?)\s*\*\s*SVR", re.I),
    "server": re.compile(r"SVR:\s*(\S+)", re.I),
    "dnis": re.compile(r"DNIS:\s*(\S+)", re.I),
    "csid": re.compile(r"CSID:\s*(\S+)", re.I),
    "ani": re.compile(r"ANI:\s*(\S+)", re.I),
    "duration": re.compile(r"DURATION[^:]*:\s*(\S+)", re.I),
}


def round_rect(r) -> List[float]:
    return [round(float(v), 2) for v in r]


def is_fax_stamp(text: str) -> bool:
    return bool(_FAX_HEADER_RE.match(text) or _FAX_FOOTER_RE.match(text))


def strip_fax_stamp(lines: List[str]) -> Tuple[List[str], Dict[str, Any]]:
    """Split transmission stamps off a page's lines -> (content lines, page_meta)."""
    content, meta = [], {}
    for line in lines:
        m = _FAX_HEADER_RE.match(line)
        if m:
            meta["fax_header"] = line.strip()
            meta["fax_page"], meta["fax_pages"] = int(m.group(1)), int(m.group(2))
            continue
        m = _FAX_FOOTER_RE.match(line)
        if m:
            meta["fax_footer"] = line.strip()
            meta["fax_page"], meta["fax_pages"] = int(m.group(1)), int(m.group(2))
            for k, rx in _FOOTER_FIELDS.items():
                f = rx.search(line)
                if f:
                    meta[k] = f.group(1).strip()
            continue
        content.append(line)
    return content, meta


def open_pdf(path: str) -> fitz.Document:
    try:
        pdf = fitz.open(path)
    except Exception as e:
        raise ocr.OcrError("That PDF could not be read.", 422) from e
    if pdf.needs_pass:
        pdf.close()
        raise ocr.OcrError("Password-protected PDFs are not supported.", 422)
    return pdf


def page_count(path: str) -> int:
    with FITZ_LOCK, open_pdf(path) as pdf:
        return pdf.page_count


GRID_MAX_COLS = 12         # a ruled grid wider than this is a page of boxes, not a table
GRID_MAX_EMPTY = 0.5       # ...and one that is mostly empty cells is a form or a layout grid


def _clean(cell: Any) -> str:
    return " ".join(str(cell or "").split())


SUBCOLUMN_GAP = 0.9        # words in a header cell this many line heights apart are separate columns that share a ruled cell (MIN  TYP  MAX)

Word = Tuple[float, float, float, float, str]


def _join(words: "List[Word]") -> str:
    """Words of one cell as text: lines top to bottom, each left to right."""
    if not words:
        return ""
    h = sorted(w[3] - w[1] for w in words)[len(words) // 2]
    lines: "List[List[Word]]" = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        if lines and abs((w[1] + w[3]) / 2 - sum((x[1] + x[3]) / 2 for x in lines[-1]) / len(lines[-1])) <= h * 0.6:
            lines[-1].append(w)
        else:
            lines.append([w])
    return " ".join(" ".join(x[4] for x in sorted(l, key=lambda w: w[0])) for l in lines)


def _sub_centres(words: "List[Word]") -> "List[float]":
    """Where the separate labels of one header cell sit across it. Words that overlap across (stacked lines of one label) or
    are a space apart are one label; a wide gap starts another."""
    if not words:
        return []
    h = sorted(w[3] - w[1] for w in words)[len(words) // 2]
    spans: "List[List[float]]" = []
    for w in sorted(words, key=lambda w: w[0]):
        if spans and w[0] - spans[-1][1] <= h * SUBCOLUMN_GAP:
            spans[-1][1] = max(spans[-1][1], w[2])
        else:
            spans.append([w[0], w[2]])
    return [(a + b) / 2 for a, b in spans]


def _grid_rows(t, words: "List[Word]") -> "Optional[Tuple[List[List[str]], int]]":
    """A ruled table's rows as text, or None when it is not a table of data. The cells' text is rebuilt from the page's words
    (the library's own extraction drops spaces and decimal points in some fonts). A cell that a neighbour spans downwards
    (a parameter name over several test conditions, a unit over a group of rows) takes the text of the cell that spans it;
    a cell that is merely blank stays blank. A header cell that holds several labels set well apart (MIN TYP MAX, ruled as
    one cell) becomes a column for each, and the values under it are dealt out by position."""
    ncols = t.col_count
    geom = [r.cells for r in t.rows]
    if not geom or ncols < 2 or ncols > GRID_MAX_COLS or len(geom) < 2:
        return None
    inside: "List[List[List[Word]]]" = []
    for r in geom:
        inside.append([[w for w in words if b[0] <= (w[0] + w[2]) / 2 <= b[2] and b[1] <= (w[1] + w[3]) / 2 <= b[3]] if b else [] for b in r])
    xs: "List[float]" = []
    for c in t.cells:
        for x in (c[0], c[2]):
            if not any(abs(x - y) < 1.5 for y in xs):
                xs.append(x)
    xs.sort()
    covered: "dict[tuple[int, int], tuple[int, int]]" = {}           # (row, col) of a cell a neighbour spans downwards -> the cell that spans it
    if len(xs) - 1 == ncols:
        for r2 in range(1, len(geom)):
            top = min((b[1] for b in geom[r2] if b), default=None)
            if top is None:
                continue
            for c in range(ncols):
                if geom[r2][c] is not None:
                    continue
                mid = (xs[c] + xs[c + 1]) / 2
                for r in range(r2 - 1, -1, -1):
                    b = geom[r][c] if c < len(geom[r]) else None
                    if b and b[3] > top + 1 and b[0] - 1 <= mid <= b[2] + 1:
                        covered[(r2, c)] = (r, c)
                        break
    # each grid column becomes one column, or several when its header cell holds labels set well apart
    layout: "List[List[float]]" = []
    for c in range(ncols):
        single = c + 1 >= ncols or geom[0][c + 1] is not None          # a header cell that spans several columns is not one column to be split
        centres = _sub_centres(inside[0][c]) if geom[0][c] and single else []
        layout.append(centres if len(centres) >= 2 else [])
    out: "List[List[str]]" = []
    for r in range(len(geom)):
        row: "List[str]" = []
        for c in range(ncols):
            src = covered.get((r, c), (r, c))
            ws = inside[src[0]][src[1]]
            if layout[c]:
                bucket: "List[List[Word]]" = [[] for _ in layout[c]]
                for w in ws:
                    k = min(range(len(layout[c])), key=lambda k: abs((w[0] + w[2]) / 2 - layout[c][k]))
                    bucket[k].append(w)
                row += [_join(x) for x in bucket]
            else:
                row.append(_join(ws))
        out.append(row)
    early = _lumped_header(out, inside, ncols, layout, first_only=True)       # a first row that is one band of labels over the columns (scans, shaded headers)
    # a header cell that spans columns, with its own labels under it, makes a two-row header (PIN over NAME and NO.)
    spanned = [c for c in range(1, ncols) if geom[0][c] is None and len(geom) > 1 and geom[1][c] is not None and (0, c) not in covered]
    # ...which shows in the other header cells reaching down over the second row (TYPE and DESCRIPTION span both); a header whose
    # spanning cell merely sits over data (TEST CONDITIONS over a row of conditions) has nothing reaching down
    header_rows = 2 if spanned and early is None and any((1, c) in covered for c in range(ncols)) else 1
    col_of = [sum(max(1, len(layout[k])) for k in range(c)) for c in range(ncols)]     # where each grid column starts in the output
    origin, count = "", 1
    for c in range(ncols):
        i = col_of[c]
        if early is not None:
            break
        if geom[0][c] is not None or layout[c]:
            origin, count = out[0][i], 1
        elif c > 0 and not out[0][i] and origin:                    # the rest of a header cell that spans this column too
            count += 1
            out[0][i] = origin if header_rows == 2 else f"{origin} ({count})"
    # a row under the header that says the same thing in every column ("8 PINS", "20 PINS") is part of the header, and so is a
    # row of labels with no figures in it that sits right above rows that have them (package names over pin numbers)
    while header_rows < len(out) - 1 and header_rows < 4 and len(out[0]) >= 3:        # two columns are a label and its value, never stacked headers
        row, after = out[header_rows], out[header_rows + 1]
        if sum(1 for x in row if x) >= 2 and not any(re.search(r"\d", x) for x in row) and any(re.search(r"\d", x) for x in after):
            header_rows += 1
            continue
        break
    while header_rows < len(out) - 1:
        cells = [re.sub(r"\d+", "", x).strip() for x in out[header_rows] if x]
        common = max(set(cells), key=cells.count) if cells else ""
        if len(cells) >= 3 and re.search(r"[A-Za-z]{3}", common) and cells.count(common) >= max(3, len(cells) * 0.6):
            header_rows += 1
        else:
            break
    split = early if early is not None else _lumped_header(out, inside, ncols, layout)
    rows = [r for r in out if any(r)]
    # blank cells are counted over the rows that have two or more values: a section title or a footnote that runs across the grid is
    # one cell and six blanks by nature, and a lab table (one of "Normal" and "Abnormal" is always blank) is sparse by design
    counted = [r for r in rows if sum(1 for c in r if c) >= 2]
    total = sum(len(r) for r in counted)
    if len(rows) < 2 or not total or sum(1 for r in counted for c in r if not c) / total > GRID_MAX_EMPTY:
        return None
    if split is not None:
        keep = [i for i, r in enumerate(out) if any(r)]
        split = keep.index(split) if split in keep else None
    return rows, header_rows, split


def _lumped_header(out: "List[List[str]]", inside: "List[List[List[Word]]]", ncols: int, layout, first_only: bool = False) -> "Optional[int]":
    """A header row whose labels came out as one cell (Test, Normal, Abnormal, Flag, Units, Reference Range in a shaded band that has
    no rules between them). Its words are dealt out to the columns the values below them sit in, and the row's index is returned
    so the grid can be cut there: what is above it (specimen, dates) is a form box, what is below is the table."""
    if any(layout):                                                  # sub-columns already changed the shape of the rows
        return None
    for i in range(0, 1 if first_only else len(out) - 1):
        filled = [c for c, v in enumerate(out[i]) if v]
        # one cell holding the labels, or (on a scan, where the band's ends were ruled off) that cell and a short one beside it
        toks = [t for c in filled for t in out[i][c].split()]
        if not filled or len(filled) > 3 or len(toks) < 4 or sum(1 for t in toks if re.search(r"\d", t)) > len(toks) * 0.25:       # a stray mark from a scan may hold a digit
            continue
        if len(filled) > 1 and sorted(len(out[i][c].split()) for c in filled)[-2] > 3:
            continue                                      # two long cells are two statements, not one label band
        below = [r for r in range(i + 1, len(out)) if sum(1 for v in out[r] if v) >= 3]
        if len(below) < 3:
            continue
        src = [w for c in filled for w in inside[i][c]]
        if len(_sub_centres(src)) < 4:
            continue
        centres: "dict[int, float]" = {}
        for c in range(ncols):
            xs = [(w[0] + w[2]) / 2 for r in below for w in inside[r][c]]
            if xs:
                centres[c] = sum(xs) / len(xs)
        label_col = min(c for c in range(ncols) if any(inside[r][c] for r in below))
        centres.setdefault(label_col, min((w[0] + w[2]) / 2 for w in src))
        if len(centres) < 3:
            continue
        groups = _sub_centres(src)
        spans: "List[List[Word]]" = [[] for _ in groups]
        for w in src:
            k = min(range(len(groups)), key=lambda k: abs((w[0] + w[2]) / 2 - groups[k]))
            spans[k].append(w)
        row = [""] * ncols
        for g, ws in zip(groups, spans):
            c = min(centres, key=lambda c: abs(centres[c] - g))
            row[c] = (row[c] + " " + _join(ws)).strip()
        out[i] = row
        return i
    return None


def _drop_empty_columns(rows: "List[List[str]]") -> "List[List[str]]":
    """The rows without the columns that have nothing in any of them (a ruled column that only exists to make room for a spanning cell)."""
    keep = [c for c in range(max((len(r) for r in rows), default=0)) if any(c < len(r) and r[c] for r in rows)]
    return [[r[c] if c < len(r) else "" for c in keep] for r in rows] if keep else rows


_AMOUNT_CELL = re.compile(r"^[($\u00b1+-]*\d[\d,]*(?:\.\d+)?%?\)?$")


def _trim_trailing(t: Dict[str, Any], words: "List[Word]") -> Dict[str, Any]:
    """A table found from word positions runs on into whatever is set below it in the same columns (callout boxes, notes, the
    next block's labels), which breaks into cells too. It ends at its last row of figures: rows after it go back to the page."""
    rows = t.get("rows") or []
    figures = [i for i, r in enumerate(rows) if sum(1 for c in r if _AMOUNT_CELL.match(str(c).strip())) >= 3]
    if not figures or figures[-1] >= len(rows) - 1:
        return t
    last = rows[figures[-1]]
    texts = {str(c).strip() for c in last if _AMOUNT_CELL.match(str(c).strip())}
    x0, y0, x1, y1 = t["bbox"]
    hits = [(w[1] + w[3]) / 2 for w in words if w[4] in texts and x0 - 2 <= w[0] <= x1 + 2 and y0 - 2 <= w[1] <= y1 + 2]
    rows_y = sorted(hits)
    best = None
    for y in rows_y:                                    # the lowest line on which three different amounts of that row sit together
        if len({w[4] for w in words if w[4] in texts and abs((w[1] + w[3]) / 2 - y) <= 3 and x0 - 2 <= w[0] <= x1 + 2}) >= min(3, len(texts)):
            best = y
    if best is None:
        return t
    return {**t, "rows": rows[:figures[-1] + 1], "bbox": [x0, y0, x1, round(best + 6, 2)]}


BLANK_GAP = 14.0          # words further apart than this on one line are separate labels
BLANK_MIN_ROWS = 2        # a header over at least this many ruled empty lines is a blank table to be filled in


def _blank_tables(page: "fitz.Page", words: "List[Word]") -> "List[Dict[str, Any]]":
    """Tables of a blank form: a line of column labels (Name of Drug, Dose, Times/Day) over ruled lines with nothing written on them.
    There are no cells to find in an empty grid, so the labels are the table and the rules below them are its rows."""
    rules: "List[Tuple[float, float, float]]" = []                      # (y, x0, x1) of horizontal lines
    try:
        for d in page.get_drawings():
            for it in d["items"]:
                if it[0] == "l" and abs(it[1].y - it[2].y) < 1 and abs(it[1].x - it[2].x) > 40:
                    rules.append((it[1].y, min(it[1].x, it[2].x), max(it[1].x, it[2].x)))
                elif it[0] == "re" and it[1].height < 1.5 and it[1].width > 40:
                    rules.append((it[1].y0, it[1].x0, it[1].x1))
    except Exception:
        return []
    if len(rules) < BLANK_MIN_ROWS + 1:
        return []
    lines: "List[List[Word]]" = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        if lines and abs((w[1] + w[3]) / 2 - (lines[-1][0][1] + lines[-1][0][3]) / 2) <= 3:
            lines[-1].append(w)
        else:
            lines.append([w])
    out: "List[Dict[str, Any]]" = []
    for ln in lines:
        ln.sort(key=lambda w: w[0])
        cells: "List[List[Word]]" = [[ln[0]]]
        for w in ln[1:]:
            (cells[-1].append(w) if w[0] - cells[-1][-1][2] <= BLANK_GAP else cells.append([w]))
        labels = [" ".join(w[4] for w in c) for c in cells]
        if len(cells) < 2 or any(len(l.split()) > 4 or re.search(r"\d|:$", l) for l in labels) or not all(l[:1].isupper() or l[:1] == "(" for l in labels):
            continue
        x0, x1, y_bottom = ln[0][0], ln[-1][2], max(w[3] for w in ln)
        below = sorted((r for r in rules if y_bottom - 2 <= r[0] <= y_bottom + 120 and r[1] <= x0 + 10 and r[2] >= x1 - 10), key=lambda r: r[0])
        ys: "List[float]" = []
        for r in below:                                                 # one entry per distinct rule (a line is often drawn twice)
            if not ys or r[0] - ys[-1] > 4:
                ys.append(r[0])
        rows, end = 0, ys[0] if ys else None
        for a, b in zip(ys, ys[1:]):
            if b - a > 40 or any(a < (w[1] + w[3]) / 2 < b and w[0] < x1 for w in words):
                break
            rows, end = rows + 1, b
        if rows >= BLANK_MIN_ROWS:
            out.append({"bbox": round_rect((x0 - 2, min(w[1] for w in ln) - 2, x1 + 2, end)), "headers": labels, "rows": [[""] * len(labels)] * rows, "method": "blank", "header_rows": 1})
    return out


HEADER_SLACK = 3.0        # words this close in height share a line
PHRASE_GAP = 6.0          # words on a line this close are one label


def _lines(words: "List[Word]") -> "List[List[Word]]":
    out: "List[List[Word]]" = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        if out and abs((w[1] + w[3]) / 2 - (out[-1][0][1] + out[-1][0][3]) / 2) <= HEADER_SLACK:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(l, key=lambda w: w[0]) for l in out]


def _by_labels(head: "List[List[Word]]", body_lines: "List[List[Word]]"):
    """(labels, rows) when the stacked caption lines `head` make at least four columns that the `body_lines` can be sorted into, else None."""
    phrases: "List[List[Any]]" = []                               # [x0, x1, [(y, text), ...]]
    for ln in head:
        cur: "List[Word]" = []
        for w in ln + [None]:                                    # type: ignore[list-item]
            if cur and (w is None or w[0] - cur[-1][2] > PHRASE_GAP):
                phrases.append([cur[0][0], cur[-1][2], [(cur[0][1], " ".join(x[4] for x in cur))]])
                cur = []
            if w is not None:
                cur.append(w)
    cols: "List[List[Any]]" = []
    for ph in sorted(phrases, key=lambda p: p[0]):
        for c in cols:
            if min(c[1], ph[1]) - max(c[0], ph[0]) >= 0.3 * min(c[1] - c[0], ph[1] - ph[0]):
                c[0], c[1] = min(c[0], ph[0]), max(c[1], ph[1])
                c[2] += ph[2]
                break
        else:
            cols.append(ph)
    cols.sort(key=lambda c: c[0])
    if len(cols) < 4:
        return None
    labels = [" ".join(txt for _, txt in sorted(c[2])) for c in cols]
    centres = [(c[0] + c[1]) / 2 for c in cols]
    out_rows: "List[List[str]]" = []
    for ln in body_lines:
        cells = [[] for _ in cols]
        for w in ln:
            cx = (w[0] + w[2]) / 2
            k = next((k for k, c in enumerate(cols) if c[0] - 2 <= cx <= c[1] + 2), None)
            cells[k if k is not None else min(range(len(cols)), key=lambda k: abs(centres[k] - cx))].append(w[4])
        row = [" ".join(c) for c in cells]
        if out_rows and not any(_AMOUNT_CELL.match(c.strip()) for c in row) and any(_AMOUNT_CELL.match(c.strip()) for c in out_rows[-1]):
            out_rows[-1] = [f"{a} {b}".strip() for a, b in zip(out_rows[-1], row)]       # the second line of a wrapped cell (3/20/22 over care)
        else:
            out_rows.append(row)
    return labels, out_rows


def _attach_header(t: Dict[str, Any], words: "List[Word]") -> Dict[str, Any]:
    """A table of figures whose column captions sit just above it, set apart from it (the reader left them out): the captions, on up to
    four lines of short labels with no numbers, make the columns, as in _rebuild_by_header."""
    rows = t.get("rows") or []
    if len(rows) < 2 or sum(1 for c in rows[0] if _AMOUNT_CELL.match(str(c).strip())) < 3:
        return t                                                 # it has its own header
    x0, y0, x1, y1 = t["bbox"]
    texts = {str(c).strip() for c in rows[0] if _AMOUNT_CELL.match(str(c).strip())}
    lines = _lines([w for w in words if x0 - 2 <= w[0] and w[2] <= x1 + 2 and y0 - 90 <= w[1] and w[3] <= y1 + 2])
    first = next((k for k, l in enumerate(lines) if len({w[4] for w in l if w[4] in texts}) >= min(3, len(texts))), None)
    if first is None:
        return t
    head: "List[List[Word]]" = []
    for ln in reversed(lines[:first]):
        h = max(w[3] - w[1] for w in ln)
        nxt = head[0] if head else lines[first]
        if min(w[1] for w in nxt) - max(w[3] for w in ln) > 1.8 * h:
            break
        if any(re.search(r"\d", w[4]) for w in ln) or len(ln) > 14:
            break
        head.insert(0, ln)
        if len(head) == 4:
            break
    if not head:
        return t
    body = lines[first:]
    got = _by_labels(head, body)
    if got is None:
        return t
    labels, out_rows = got
    top = min(w[1] for w in head[0]) - 2
    return {**t, "headers": labels, "rows": [labels] + out_rows, "bbox": [x0, round(top, 2), x1, y1]}


def _rebuild_by_header(t: Dict[str, Any], words: "List[Word]") -> Dict[str, Any]:
    """A table whose column labels are set on two or three stacked lines (Service / Code, Total / Amount) comes out of the word-position
    reader with the labels run together in the first cell. Here the labels make the columns: words that overlap across the lines are
    one label (Not / Covered), and each row's words go to the label they sit under."""
    rows = t.get("rows") or []
    fig = [i for i, r in enumerate(rows) if sum(1 for c in r if _AMOUNT_CELL.match(str(c).strip())) >= 3]
    if not fig or fig[0] < 2 or sum(1 for c in rows[0] if str(c).strip()) < 4:
        return t
    lead = fig[0]
    if any(re.search(r"\d", str(c)) for r in rows[:lead] for c in r):
        return t
    texts = {str(c).strip() for c in rows[fig[0]] if _AMOUNT_CELL.match(str(c).strip())}
    x0, y0, x1, y1 = t["bbox"]
    box = [w for w in words if x0 - 2 <= w[0] and w[2] <= x1 + 2 and y0 - 2 <= w[1] and w[3] <= y1 + 2]
    body_lines = _lines(box)
    first = next((k for k, l in enumerate(body_lines) if len({w[4] for w in l if w[4] in texts}) >= min(3, len(texts))), None)
    if first is None or first < lead:
        return t
    head = body_lines[first - lead:first]
    got = _by_labels(head, body_lines[first:])
    if got is None:
        return t
    labels, out_rows = got
    top = min(w[1] for w in head[0]) - 2                       # the table starts at its labels, not at whatever the reader took in above them
    return {**t, "headers": labels, "rows": [labels] + out_rows, "bbox": [x0, round(top, 2), x1, y1]}


def _row_top(g, rows: "List[List[str]]", cut: int) -> float:
    """The y where the `cut`-th non-empty row of grid `g` starts."""
    tops = [min((b[1] for b in r.cells if b), default=None) for r in g.rows]
    tops = [t for t in tops if t is not None]
    return round(tops[min(cut, len(tops) - 1)], 2)


def _vertical_rules(draws: "List[Any]", box: "Tuple[float, ...]") -> int:
    """How many vertical ruling lines are drawn inside `box`. A grid that has them is a table drawn with borders; one found
    from underlines alone (a statement's rules under its headings) is not, and the word-position extractor reads it better."""
    x0, y0, x1, y1 = box
    n = 0
    for d in draws:
        for it in d["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
                if abs(a.x - b.x) < 0.5 and abs(a.y - b.y) > 4 and x0 - 2 <= a.x <= x1 + 2 and y0 - 2 <= min(a.y, b.y) and max(a.y, b.y) <= y1 + 2:
                    n += 1
            elif it[0] == "re":
                r = it[1]
                if r.width < 1.5 and r.height > 4 and x0 - 2 <= r.x0 <= x1 + 2 and y0 - 2 <= r.y0 and r.y1 <= y1 + 2:
                    n += 1
    return n


def _overlap(a: List[float], b: List[float]) -> float:
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    return w * h / max(1e-6, min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])))


def _blank_grid(g, words: "List[Word]") -> "List[Dict[str, Any]]":
    """A titled grid with labels in its first row and nothing in the rows under it (a place to write each dose): its columns are its content."""
    if len(g.rows) < 2:
        return []
    def text(b) -> str:
        return _join([w for w in words if b[0] <= (w[0] + w[2]) / 2 <= b[2] and b[1] <= (w[1] + w[3]) / 2 <= b[3]]) if b else ""
    labels = [text(b) for b in g.rows[0].cells]
    if any(text(b) for r in g.rows[1:] for b in r.cells) or sum(1 for x in labels if x) < 2:
        return []
    labels = [x for x in labels if x]
    bar = g.title_bar
    box = round_rect(g.bbox)
    title = g.title.title() if g.title.isupper() and len(g.title) > 8 else g.title
    return [{"bbox": [min(box[0], bar[0]), min(box[1], bar[1]), max(box[2], bar[2]), box[3]], "headers": labels,
             "rows": [labels] + [[""] * len(labels)] * (len(g.rows) - 1), "method": "blank", "header_rows": 1, "title": title}]


def _scan_rules(page) -> "Optional[Tuple[List[List[float]], List[List[float]]]]":
    """The ruling lines of a scanned page, found in its picture; None for a page that is not a scan or has none."""
    try:
        from app.docproc.services.reading import image_rules
        if not image_rules.available() or not ocr._is_scanned_page(page):
            return None
        png = page.get_pixmap(dpi=150).tobytes("png")
        hs, vs = image_rules.find_rules(png, page.rect.width, page.rect.height)
        return (hs, vs) if len(hs) >= 3 and len(vs) >= 3 else None
    except Exception:
        return None


def _emit_grid(g, words: "List[Word]") -> "List[Dict[str, Any]]":
    """The table dicts for ruled grid `g` (two when a header is found part-way down: the box above it and the table below), or []."""
    got = _grid_rows(g, words)
    box = round_rect(g.bbox)
    title = getattr(g, "title", None)
    if not got:
        return _blank_grid(g, words) if title else []
    rows, header_rows, cut = got
    if cut:                                                         # a header found part-way down: a box above it, the table below
        y = _row_top(g, rows, cut)
        above = _drop_empty_columns([[re.sub(r" \(\d+\)$", "", c) if re.search(r" \(\d+\)$", c) and c.startswith(prev) else c
                                      for prev, c in zip([""] + r, r)] for r in rows[:cut]])
        below = _drop_empty_columns(rows[cut:])
        return [{"bbox": [box[0], box[1], box[2], y], "headers": above[0], "rows": above, "method": "grid", "header_rows": 0},
                {"bbox": [box[0], y, box[2], box[3]], "headers": below[0], "rows": below, "method": "grid", "header_rows": 1}]
    rows = _drop_empty_columns(rows)
    t: Dict[str, Any] = {"bbox": box, "headers": rows[0], "rows": rows, "method": "grid", "header_rows": header_rows}
    if title:
        t["title"] = title.title() if title.isupper() and len(title) > 8 else title
        bar = g.title_bar
        t["bbox"] = [min(box[0], bar[0]), min(box[1], bar[1]), max(box[2], bar[2]), box[3]]
    body = rows[header_rows:]
    if title is not None and header_rows >= 1 and len(body) >= 1 and not any(c.strip() for r in body for c in r[1:]):
        # a record card: the labels, a column of dose numbers and nothing written yet. It is a form to fill in, so the columns are its content
        t.update({"method": "blank", "rows": [rows[0]] + [[""] * len(rows[0])] * len(body), "header_rows": 1})
    return [t]


_PLACEHOLDER_HEAD = re.compile(r"Line item|Col \d+")


def _with_header_row(t: Dict[str, Any]) -> List[List[str]]:
    """The word-position extractor's rows, with its column captions as the first row when it found any. Everything downstream
    (digital.split_header) reads a table's labels from its first rows; the extractor keeps the captions apart from the rows,
    already joined per column ("Three Months Ended June 30, 2026"), and names a column "Col 2" when it has none."""
    heads = [h if not _PLACEHOLDER_HEAD.fullmatch(h or "") else "" for h in t["headers"]]
    return ([heads] + list(t["rows"])) if any(heads[1:]) else list(t["rows"])


def _tables(page, page_num: int, noise=lambda w: False, shown=lambda w: w[4]) -> List[Dict[str, Any]]:
    """Tables on a page. A table drawn with ruling lines is read from its rules: the lines say exactly where the cells are,
    including cells that span rows (datasheets, forms with boxes). A table without rules (statements, schedules) comes from
    the word-position extractor (ported from the SEC pipeline), which keeps column alignment; PyMuPDF's generic
    find_tables() is the fallback for text-only tables it rejects."""
    out: List[Dict[str, Any]] = []
    lwords = [(w[0], w[1], w[2], w[3], shown(w)) for w in page.get_text("words") if w[4].strip() and not noise(w)]
    try:
        from app.docproc.services.reading.table_extractor import extract_page_tables_with_bbox
        for t, bb in extract_page_tables_with_bbox(page, page_num):
            out.append(_attach_header(_rebuild_by_header(_trim_trailing({"bbox": round_rect(bb), "headers": t["headers"], "rows": _with_header_row(t), "method": "layout"}, lwords), lwords), lwords))
    except Exception:
        out = []
    try:
        words = [(w[0], w[1], w[2], w[3], shown(w)) for w in page.get_text("words") if w[4].strip() and not noise(w)]
        draws = page.get_drawings()
        for g in page.find_tables(strategy="lines_strict").tables:
            if _vertical_rules(draws, g.bbox) < max(2, g.col_count - 1):
                continue
            made = _emit_grid(g, words)
            if made:
                out = [t for t in out if _overlap(t["bbox"], round_rect(g.bbox)) < 0.5]       # the ruled grid replaces what the extractor made of the same area
                out += made
        from app.docproc.services.reading import drawn_grids
        rules = _scan_rules(page)                               # a scan's printed rules, found in its picture
        grids = drawn_grids.find_grids(page, words, rules)      # cells drawn as separate strokes, which the library does not see as a table
        if rules:
            for g in grids:                                     # the rules of a scanned table are its ruled grid, as for a digital page: it replaces what the word reader made
                made = _emit_grid(g, words)
                if made:
                    out = [t for t in out if _overlap(t["bbox"], round_rect(g.bbox)) < 0.5]
                    out += made
            grids = []
        card = sum(1 for g in grids if g.title) >= 2            # a record card: several sections, each under a dark title bar. Only then do they replace what another reader found
        for g in grids:
            box = round_rect(g.bbox)
            titled = bool(g.title) and card
            if not titled and out:                              # elsewhere a box on a page that already has tables is a form's header or a callout, not a table
                continue
            if not titled:
                g.title = g.title_bar = None
            made = _emit_grid(g, words)
            if made:
                out = [t for t in out if _overlap(t["bbox"], box) < 0.3]
                out += made
    except Exception:
        pass
    try:
        for b in _blank_tables(page, lwords):
            if not any(_overlap(t["bbox"], b["bbox"]) >= 0.3 for t in out):
                out.append(b)
    except Exception:
        pass
    if out:
        return sorted(out, key=lambda t: (t["bbox"][1], t["bbox"][0]))
    try:
        for t in page.find_tables().tables:
            rows = [[(c or "").strip() for c in r] for r in t.extract()]
            if rows and any(any(c for c in r) for r in rows):
                out.append({"bbox": round_rect(t.bbox), "headers": rows[0], "rows": rows[1:], "method": "grid"})
    except Exception:
        pass
    return out


def scanned_pages(path: str) -> "set[int]":
    """Pages with no usable embedded text, which need a Tesseract layer (picture-covered or almost no text)."""
    out = set()
    with FITZ_LOCK, open_pdf(path) as pdf:
        for i, page in enumerate(pdf, 1):
            if ocr._is_scanned_page(page) or len(page.get_text().strip()) < ocr.MIN_TEXT_LAYER_CHARS:
                out.add(i)
    return out


MIN_STAMP = 12            # a turned line at least this long is a stamp or a watermark, not a label


def _sideways(page: "fitz.Page", chars: bool = False) -> "list[tuple[list[float], str]]":
    """Lines set at an angle to the page (an arXiv stamp up the margin, a watermark): (box, text). They are not part of the
    reading order and, taken as lines of text, spoil titles ("Aug") and tables. When most of a page is turned (a landscape page
    saved sideways) none are returned: that text is the page."""
    try:
        d = page.get_text("rawdict" if chars else "dict",
                          flags=(fitz.TEXTFLAGS_RAWDICT if chars else fitz.TEXTFLAGS_DICT) & ~fitz.TEXT_PRESERVE_IMAGES)
    except Exception:
        return []
    turned, total, kept = [], 0, 0
    for b in d.get("blocks", []):
        for l in b.get("lines", []):
            if chars:                       # one box per letter: a diagonal line's own box covers half the page, and the text of the page lies under it
                text = "".join(c["c"] for sp in l.get("spans", []) for c in sp.get("chars", [])).strip()
            else:
                text = "".join(sp["text"] for sp in l.get("spans", [])).strip()
            n = len(text)
            total += n
            dx, dy = l.get("dir", (1, 0))
            if n and abs(dy) > 0.25 and abs(dy) >= abs(dx) * 0.25:
                kept += n
                if n >= MIN_STAMP:                       # a short turned label ("Form" down the side of a tax form) is part of the page
                    if chars:
                        turned += [(list(c["bbox"]), c["c"]) for sp in l.get("spans", []) for c in sp.get("chars", []) if c["c"].strip()]
                    else:
                        turned.append((list(l["bbox"]), text))
    return turned if total and kept / total < 0.3 else []


MIN_CALLOUTS = 1          # one numbered circle on a page is already a key, not part of the content
CALLOUT_SIZE = (7.0, 30.0)  # a numbered circle is a small, roughly square shape


def _callouts(page: "fitz.Page") -> "list[list[float]]":
    """Boxes of the numbered circles that point from a drawing to its explanation (1, 2, 3 ... in green or red dots). The number
    inside is a marker, not part of the page: in a table it becomes a stray cell above a header or beside a value."""
    try:
        draws = page.get_drawings()
        words = page.get_text("words")
    except Exception:
        return []
    out: "list[list[float]]" = []
    for d in draws:
        r = d.get("rect")
        if not r or d.get("fill") is None or not any(it[0] == "c" for it in d.get("items", ())):
            continue
        if not (CALLOUT_SIZE[0] <= r.width <= CALLOUT_SIZE[1] and CALLOUT_SIZE[0] <= r.height <= CALLOUT_SIZE[1]) or abs(r.width - r.height) > 3:
            continue
        inside = [w for w in words if r.x0 - 1 <= (w[0] + w[2]) / 2 <= r.x1 + 1 and r.y0 - 1 <= (w[1] + w[3]) / 2 <= r.y1 + 1 and w[4].strip()]
        if len(inside) == 1 and re.fullmatch(r"\d{1,2}", inside[0][4].strip()):
            out.append([r.x0, r.y0, r.x1, r.y1])
    return out if len(out) >= MIN_CALLOUTS else []


# Symbol fonts print a check box as an ordinary letter ("o" in Wingdings): the page text says "o heart disease". The letter is turned back into the box it draws.
_DINGBATS = {"o": "\u2610", "q": "\u2610", "p": "\u2610", "\u00a8": "\u2610", "n": "\u25a0", "x": "\u2612", "\u00fb": "\u2612", "\u00fe": "\u2611", "\u00fc": "\u2713",
             "r": "\u2610", "4": "\u2713", "8": "\u2717", "6": "\u2717"}


def _dingbat_words(page: "fitz.Page") -> "list[tuple[list[float], str]]":
    """(box, glyph) for each letter of the page that a symbol font draws as a box or a tick."""
    out: "list[tuple[list[float], str]]" = []
    try:
        for b in page.get_text("rawdict").get("blocks", []):
            for l in b.get("lines", []):
                for sp in l.get("spans", []):
                    font = sp.get("font", "").lower()
                    if "wingdings" in font or "zapfdingbats" in font or font.startswith("symbol"):
                        for c in sp.get("chars", []):
                            g = _DINGBATS.get(c["c"])
                            if g:
                                out.append((list(c["bbox"]), g))
    except Exception:
        return []
    return out


def _noise(page: "fitz.Page"):
    """A test for words that are not part of the page's own content: the letters of a diagonal watermark and the numbers in callout
    circles. The words are left out of every reader below; nothing is removed from the file or the picture."""
    marks = [(b, c) for b, c in _sideways(page, chars=True)]
    lines = _sideways(page)
    circles = _callouts(page)

    def is_noise(w) -> bool:
        text = w[4].strip()
        cx0, cy0 = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        if len(text) >= 2 and any(b[0] <= cx0 <= b[2] and b[1] <= cy0 <= b[3] and text in t for b, t in lines) and any(
                abs(w[0] - mb[0]) < 3 and mc == text[0] for mb, mc in marks):
            return True                     # a whole word of the watermark ("EXAMPLE"), which starts on one of its turned letters
        if len(text) == 1 and any(abs(w[0] - b[0]) < 0.6 and abs(w[1] - b[1]) < 0.6 and abs(w[2] - b[2]) < 0.6 and abs(w[3] - b[3]) < 0.6 and text == c for b, c in marks):
            return True
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        return bool(re.fullmatch(r"\d{1,2}", text)) and any(b[0] - 1 <= cx <= b[2] + 1 and b[1] - 1 <= cy <= b[3] + 1 for b in circles)
    return is_noise if (marks or circles) else (lambda w: False)


def _without(text: str, lines: "list[str]") -> str:
    """`text` with the given lines of it taken out."""
    if not lines:
        return text
    gone = {" ".join(l.split()) for l in lines}
    return "\n".join(l for l in text.splitlines() if " ".join(l.split()) not in gone)


def analyze_page(path: str, page_num: int, with_tables: bool = True) -> Dict[str, Any]:
    """Embedded-text view of one page. `with_tables=False` skips table detection, which on PyMuPDF 2.x runs a neural layout model (seconds per
    page) and finds nothing on a scan anyway (a scan has no ruling lines or text of its own to detect a table from). `scanned` means a picture covers most of the page, so any text layer is a
    scanner's OCR and should not be trusted as the page text (it is still used to place bounding boxes)."""
    with FITZ_LOCK, open_pdf(path) as pdf:
        page = pdf[page_num - 1]
        sideways = _sideways(page)
        marks = _dingbat_words(page)
        def shown(w) -> str:
            if marks and len(w[4]) == 1:
                for b, g in marks:
                    if abs(w[0] - b[0]) < 0.8 and abs(w[1] - b[1]) < 0.8 and abs(w[2] - b[2]) < 0.8 and abs(w[3] - b[3]) < 0.8:
                        return g
            return w[4]
        noise = _noise(page)
        def turned(w) -> bool:
            return noise(w)
        words = [{"text": shown(w), "bbox": round_rect(w[:4]), "block": w[5], "line": w[6]}
                 for w in page.get_text("words") if w[4].strip() and not turned(w)]
        blocks = [{"id": f"b{b[5]}", "bbox": round_rect(b[:4]), "text": b[4].strip()}
                  for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
        tables = _tables(page, page_num, noise, shown) if with_tables else []
        widgets, every = [], []
        try:  # fillable-form fields: a digitally filled form has its values here, exact
            for w in page.widgets() or []:
                every.append({"name": w.field_name, "type": w.field_type_string, "value": "" if w.field_value in (None, "Off") else str(w.field_value),
                              "bbox": round_rect(w.rect)})
                if w.field_value not in (None, "", "Off"):
                    widgets.append({"name": w.field_name, "type": w.field_type_string, "value": str(w.field_value),
                                    "bbox": round_rect(w.rect)})
        except Exception:
            widgets, every = [], []
        return {
            "widgets": widgets,
            "fields": every,                      # every fillable field, blank or filled (the widgets above are only the filled ones)
            "size": {"width": round(page.rect.width, 2), "height": round(page.rect.height, 2)},
            "text": _without(page.get_text().strip(), [t for _, t in sideways]),
            "words": words,
            "blocks": blocks,
            "tables": tables,
            "scanned": ocr._is_scanned_page(page),
        }


def build_work_pdf(src: str, dst: str, scanned: "set[int]", tsv_out: "Optional[dict[int, str]]" = None) -> "dict[int, str]":
    """Copy `src` to `dst`, replacing each page in `scanned` with a Tesseract-searchable version.
    Returns {page: 'ok' | error text} for the scanned pages; a failed page is copied through untouched.
    `tsv_out`, when given, receives each scanned page's Tesseract word table, so the page need not be OCR'd twice."""
    from app.docproc.services.reading import tess
    status: "dict[int, str]" = {}
    with FITZ_LOCK, open_pdf(src) as pdf, fitz.open() as out:
        for n in range(1, pdf.page_count + 1):
            if n in scanned and tess.available():
                try:
                    one, tsv = tess.searchable_page_with_words(pdf[n - 1], key=(os.path.abspath(src), n))
                    if tsv_out is not None:
                        tsv_out[n] = tsv
                    out.insert_pdf(one)
                    one.close()
                    status[n] = "ok"
                    continue
                except Exception as e:  # keep the page; it just has no text layer
                    status[n] = f"tesseract failed: {e}"
            out.insert_pdf(pdf, from_page=n - 1, to_page=n - 1)
        out.save(dst)
    return status


def render_jpeg(path: str, page_num: int) -> bytes:
    with FITZ_LOCK, open_pdf(path) as pdf:
        png = pdf[page_num - 1].get_pixmap(dpi=ocr.RENDER_DPI).tobytes("png")
    from app.docproc.services.reading import tess
    known = tess.ANGLES.get((os.path.abspath(path), page_num))          # the tilt taken out of this page's OCR copy: take out the same
    png = tess.evened(png)                                              # light first: the white corners a turned page gets would fool the estimate of uneven light
    return ocr.normalize_image(tess.rotated(png, known) if known is not None else tess.straightened(png)[0])


def search_rects(path: str, page_num: int, needle: str) -> List[List[float]]:
    with FITZ_LOCK, open_pdf(path) as pdf:
        return [round_rect(r) for r in pdf[page_num - 1].search_for(needle)]


def layout_text(path: str, page_num: int) -> str:
    """A digital page's text with its rows kept: words on one line are joined, and a wide gap becomes " | ", so a table row
    reads "Revenue | 70,066 | 61,858" instead of one cell per line. This is what a text-only model reads."""
    with FITZ_LOCK, open_pdf(path) as pdf:
        words = pdf[page_num - 1].get_text("words")
    words = sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    rows: List[List[tuple]] = []
    for w in words:
        mid = (w[1] + w[3]) / 2
        if rows and abs(mid - sum((x[1] + x[3]) / 2 for x in rows[-1]) / len(rows[-1])) <= 3.0:
            rows[-1].append(w)
        else:
            rows.append([w])
    out = []
    for row in rows:
        row.sort(key=lambda w: w[0])
        line = row[0][4]
        for prev, w in zip(row, row[1:]):
            height = max(prev[3] - prev[1], 6.0)
            glue = prev[4] in ("$", "(", "($", "-$") or w[4] in (")", "%", "%)")   # a sign or bracket printed in its own cell
            line += (" " if glue and w[0] - prev[2] > height * 1.2 else " | " if w[0] - prev[2] > height * 1.2 else " ") + w[4]
        out.append(line.strip())
    return "\n".join(l for l in out if l)
