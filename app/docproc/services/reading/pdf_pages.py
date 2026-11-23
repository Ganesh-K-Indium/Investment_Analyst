"""
PyMuPDF layer: open a PDF and turn each page into a plain dict (size, embedded text, words/blocks with bboxes, tables,
scan detection) plus a JPEG render for the vision readers.

PyMuPDF (MuPDF) is not thread-safe; every use goes through FITZ_LOCK.
"""
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
        centres = _sub_centres(inside[0][c]) if geom[0][c] else []
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
    # a header cell that spans columns, with its own labels under it, makes a two-row header (PIN over NAME and NO.)
    spanned = [c for c in range(1, ncols) if geom[0][c] is None and len(geom) > 1 and geom[1][c] is not None and (0, c) not in covered]
    # ...which shows in the other header cells reaching down over the second row (TYPE and DESCRIPTION span both); a header whose
    # spanning cell merely sits over data (TEST CONDITIONS over a row of conditions) has nothing reaching down
    header_rows = 2 if spanned and any((1, c) in covered for c in range(ncols)) else 1
    col_of = [sum(max(1, len(layout[k])) for k in range(c)) for c in range(ncols)]     # where each grid column starts in the output
    origin, count = "", 1
    for c in range(ncols):
        i = col_of[c]
        if geom[0][c] is not None or layout[c]:
            origin, count = out[0][i], 1
        elif c > 0 and not out[0][i] and origin:                    # the rest of a header cell that spans this column too
            count += 1
            out[0][i] = origin if header_rows == 2 else f"{origin} ({count})"
    # a row under the header that says the same thing in every column ("8 PINS", "20 PINS") is part of the header, and so is a
    # row of labels with no figures in it that sits right above rows that have them (package names over pin numbers)
    while header_rows < len(out) - 1 and header_rows < 4:
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
    rows = [r for r in out if any(r)]
    total = sum(len(r) for r in rows)
    if len(rows) < 2 or not total or sum(1 for r in rows for c in r if not c) / total > GRID_MAX_EMPTY:
        return None
    return rows, header_rows


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


def _tables(page, page_num: int) -> List[Dict[str, Any]]:
    """Tables on a page. A table drawn with ruling lines is read from its rules: the lines say exactly where the cells are,
    including cells that span rows (datasheets, forms with boxes). A table without rules (statements, schedules) comes from
    the word-position extractor (ported from the SEC pipeline), which keeps column alignment; PyMuPDF's generic
    find_tables() is the fallback for text-only tables it rejects."""
    out: List[Dict[str, Any]] = []
    try:
        from app.docproc.services.reading.table_extractor import extract_page_tables_with_bbox
        for t, bb in extract_page_tables_with_bbox(page, page_num):
            out.append({"bbox": round_rect(bb), "headers": t["headers"], "rows": t["rows"], "method": "layout"})
    except Exception:
        out = []
    try:
        words = [(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words") if w[4].strip()]
        draws = page.get_drawings()
        for g in page.find_tables(strategy="lines_strict").tables:
            if _vertical_rules(draws, g.bbox) < max(2, g.col_count - 1):
                continue
            got = _grid_rows(g, words)
            if got:
                rows, header_rows = got
                box = round_rect(g.bbox)
                out = [t for t in out if _overlap(t["bbox"], box) < 0.5]       # the ruled grid replaces what the extractor made of the same area
                out.append({"bbox": box, "headers": rows[0], "rows": rows, "method": "grid", "header_rows": header_rows})
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


def _sideways(page: "fitz.Page") -> "list[tuple[list[float], str]]":
    """Lines set at an angle to the page (an arXiv stamp up the margin, a watermark): (box, text). They are not part of the
    reading order and, taken as lines of text, spoil titles ("Aug") and tables. When most of a page is turned (a landscape page
    saved sideways) none are returned: that text is the page."""
    try:
        d = page.get_text("dict")
    except Exception:
        return []
    turned, total, kept = [], 0, 0
    for b in d.get("blocks", []):
        for l in b.get("lines", []):
            text = "".join(sp["text"] for sp in l.get("spans", [])).strip()
            n = len(text)
            total += n
            dx, dy = l.get("dir", (1, 0))
            if n and abs(dy) > 0.25 and abs(dy) >= abs(dx) * 0.25:
                kept += n
                if n >= MIN_STAMP:                       # a short turned label ("Form" down the side of a tax form) is part of the page
                    turned.append((list(l["bbox"]), text))
    return turned if total and kept / total < 0.3 else []


def _without(text: str, lines: "list[str]") -> str:
    """`text` with the given lines of it taken out."""
    if not lines:
        return text
    gone = {" ".join(l.split()) for l in lines}
    return "\n".join(l for l in text.splitlines() if " ".join(l.split()) not in gone)


def analyze_page(path: str, page_num: int) -> Dict[str, Any]:
    """Embedded-text view of one page. `scanned` means a picture covers most of the page, so any text layer is a
    scanner's OCR and should not be trusted as the page text (it is still used to place bounding boxes)."""
    with FITZ_LOCK, open_pdf(path) as pdf:
        page = pdf[page_num - 1]
        sideways = _sideways(page)
        def turned(w) -> bool:
            cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
            return any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b, _ in sideways)
        words = [{"text": w[4], "bbox": round_rect(w[:4]), "block": w[5], "line": w[6]}
                 for w in page.get_text("words") if w[4].strip() and not turned(w)]
        blocks = [{"id": f"b{b[5]}", "bbox": round_rect(b[:4]), "text": b[4].strip()}
                  for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
        tables = _tables(page, page_num)
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
                    one, tsv = tess.searchable_page_with_words(pdf[n - 1])
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
    return ocr.normalize_image(tess.evened(png))


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
