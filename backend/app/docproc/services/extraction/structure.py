"""
The structure of a page, from its layout alone: no template and no model.

Input is the page as rows of cells, each cell a piece of text with its position (see Reading.line_cells). Output is
blocks in reading order: headings, paragraphs, label/value groups, tables and lists, each with where it sits on the
page, plus the dates, amounts, phone numbers and so on found in them.

It is rule-based on purpose. Layout rules are explainable to a reviewer, cost nothing, and work offline. They are good
on printed documents with a clear layout (reports, statements, letters, forms). They are weak on handwriting and on
text set in several columns; a page that has no layout (read by a vision model) is built from its lines only and says so.
"""
import re
import statistics
from typing import Any, Dict, List, Optional, Tuple

Cell = Tuple[str, float, float, float, float]       # text, x0, y0, x1, y1 as fractions of the page

COLUMN_TOL = 0.02          # cells whose left edges are this close (page width) share a column
MAX_COLUMNS = 8
MAX_CELL_CHARS = 48        # cells longer than this are running text, not table cells
BIG_HEADING = 1.25         # a line this much taller than the page's usual text is a heading
PARAGRAPH_GAP = 1.0        # a gap above this many line heights starts a new paragraph

_LABEL = re.compile(r"^\s*([A-Z#][^:|]{0,48}?)\s*:\s*(.*)$")
_LIST = re.compile(r"^\s*([•●▪◦*\-]|\d{1,2}[.)]|[A-Za-z][.)])\s+(\S.*)$")
_SENTENCE_END = re.compile(r"[.!?]\s*$")
# "1 Features", "5.3 Recommended Operating Conditions": a section number and a short title in capitals-and-small-words
_NUMBERED = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\s+([A-Z][\w&/'\-,]*(?:\s+(?:[A-Z0-9][\w&/'\-,()]*|of|and|the|for|in|to|on|or|a|an|with|by|at|per|vs\.?)){0,9})$")

_CHECKED, _EMPTY = "☑☒✔✓■", "☐□"
_GLYPH = re.compile("([" + re.escape(_CHECKED + _EMPTY) + "])")
_CAPTION = re.compile(r"^\(\s*[^()]{2,45}\)$")
_PAGE_NUMBER = re.compile(r"^(?:page\s*)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?$", re.I)
# "Commission File Number 001-37845", "For the Quarterly Period Ended March 31, 2026": a short label, then an id or a date, no colon
_LABEL_THEN_ID = re.compile(r"^(?P<label>[A-Z][A-Za-z.&'/\- ]{5,60}?)\s+(?P<value>\d{2,}-\d[\d-]*|[A-Z]{0,3}\d{5,}[\dA-Z-]*)$")
_DATE = r"(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.? \d{1,2},? \d{4}|\d{1,2}/\d{1,2}/\d{2,4})"
_LABEL_THEN_DATE = re.compile(r"^(?P<label>[A-Z][A-Za-z.&'/\- ]{5,60}?)\s+(?P<value>" + _DATE + r")$")
_SMALL = {"of", "the", "and", "for", "in", "to", "a", "an", "on", "or"}

_ENTITIES: List[Tuple[str, "re.Pattern[str]"]] = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("url", re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.I)),
    ("date", re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.? \d{1,2},? \d{4}\b|\b\d{4}-\d{2}-\d{2}\b", re.I)),
    ("amount", re.compile(r"[$€£]\s?\d[\d,]*(?:\.\d{1,2})?|\b\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?\b")),
    ("percent", re.compile(r"\b\d+(?:\.\d+)?\s?%")),
    ("phone", re.compile(r"(?<!\d)\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}(?!\d)")),
]


def _box(cells: List[Cell]) -> List[float]:
    return [round(min(c[1] for c in cells), 4), round(min(c[2] for c in cells), 4), round(max(c[3] for c in cells), 4), round(max(c[4] for c in cells), 4)]


class _Row:
    def __init__(self, cells: List[Cell]):
        self.cells = cells
        self.text = " | ".join(c[0] for c in cells)
        self.x0 = min(c[1] for c in cells)
        self.y0 = min(c[2] for c in cells)
        self.y1 = max(c[4] for c in cells)
        self.h = statistics.median([max(c[4] - c[2], 1e-6) for c in cells])
        self.order = 0.0
        self.figure: Optional[Dict[str, Any]] = None
        self.kind = "text"            # text | heading | list | kv | multi | given
        self.level = 0
        self.table: Optional[Dict[str, Any]] = None
        self.pairs: Optional[List[Dict[str, Any]]] = None      # label/value pairs known without a colon (a caption under a value)
        self.boxes: Optional[List[Dict[str, Any]]] = None      # check boxes read from glyphs

    @property
    def box(self) -> List[float]:
        return _box(self.cells)


def _nice(caption: str) -> str:
    """"(STATE OF INCORPORATION)" -> "State of Incorporation"; acronyms such as "I.R.S. ID" stay as printed."""
    words = caption.strip("() ").split()
    out = []
    for i, w in enumerate(words):
        if w.lower() in _SMALL and i:
            out.append(w.lower())
        elif w.isupper() and len(w) > 3 and "." not in w:
            out.append(w.capitalize())
        else:
            out.append(w)
    return " ".join(out)


def _label_tail(text: str) -> Optional[Tuple[str, str]]:
    """A short label followed directly by an identifier or a date, with no colon between."""
    text = text.strip()
    for rx in (_LABEL_THEN_ID, _LABEL_THEN_DATE):
        m = rx.match(text)
        if m:
            label = m.group("label").strip()
            if 2 <= len(label.split()) <= 7 and not re.search(r"\d", label):
                return label, m.group("value").strip()
    return None


def _box_items(text: str) -> List[Dict[str, Any]]:
    """Check boxes in one line of text: "☒ Option text", or "Option ☒ Other option ☐", or "... days. Yes ☒ No ☐"."""
    parts = _GLYPH.split(text.replace(" | ", "  "))
    if len(parts) < 3:
        return []
    items: List[Dict[str, Any]] = []
    before = parts[0].strip()
    if not before:                                           # the box comes first: the option's text follows it
        for i in range(1, len(parts), 2):
            label = parts[i + 1].strip() if i + 1 < len(parts) else ""
            if label:
                items.append({"label": label, "checked": parts[i] in _CHECKED})
        return items
    for i in range(1, len(parts), 2):                        # the option's text comes first: it ends at the box
        raw = parts[i - 1].strip()
        if i == 1:
            sentences = re.split(r"(?<=[.:;?!])\s+", raw)
            raw = sentences[-1] if sentences else raw                  # "Yes" in "...the past 90 days. Yes"; a long sentence stays whole
            if len(raw) > 140:
                raw = raw[:137].rsplit(" ", 1)[0] + "..."
        if raw:
            items.append({"label": raw, "checked": parts[i] in _CHECKED})
    return items


def _label_value(text: str) -> Optional[Tuple[str, str]]:
    m = _LABEL.match(text)
    if not m:
        return None
    label, value = m.group(1).strip(" *"), m.group(2).strip()
    if len(label.split()) > 7 or value.startswith("//") or not re.search(r"[A-Za-z]", label):
        return None
    return label, value


def _pairs(cells: List[Cell]) -> List[Dict[str, Any]]:
    """Label/value pairs of one row. A label with nothing after it takes the next cell as its value."""
    out: List[Dict[str, Any]] = []
    i = 0
    while i < len(cells):
        lv = _label_value(cells[i][0])
        if lv is None:
            return []
        label, value = lv
        box = [cells[i][1], cells[i][2], cells[i][3], cells[i][4]]
        if not value and i + 1 < len(cells) and _label_value(cells[i + 1][0]) is None:
            value = cells[i + 1][0].strip()
            box = [box[0], min(box[1], cells[i + 1][2]), cells[i + 1][3], max(box[3], cells[i + 1][4])]
            i += 1
        out.append({"label": label, "value": value or None, "bbox": [round(v, 4) for v in box]})
        i += 1
    return out


def _classify(rows: List[_Row], median_h: float) -> None:
    for r in rows:
        if r.kind in ("given", "figure"):
            continue
        if r.pairs:
            r.kind = "kv"
            continue
        if _GLYPH.search(r.text):
            items = _box_items(r.text)
            if items:
                r.kind, r.boxes = "boxes", items
                continue
        if len(r.cells) >= 2:
            r.kind = "kv" if _pairs(r.cells) else "multi"
            continue
        text = r.text.strip()
        tail = _label_tail(text)
        if _pairs(r.cells):
            r.kind = "kv"
        elif tail:
            r.kind, r.pairs = "kv", [{"label": tail[0], "value": tail[1], "bbox": r.box}]
        elif _NUMBERED.match(text) and len(text) <= 80 and not _SENTENCE_END.search(text) and not _LIST.match(text):
            r.kind, r.level = "heading", 3          # the size of the type is not known here: a title set bigger outranks a numbered section
        elif _LIST.match(text):
            r.kind = "list"
        else:
            words = text.split()
            letters = re.sub(r"[^A-Za-z]", "", text)
            big = r.h >= median_h * BIG_HEADING
            shouty = len(letters) >= 3 and letters.isupper() and len(words) <= 10 and not _SENTENCE_END.search(text)
            if (big or shouty) and len(text) <= 100 and not _SENTENCE_END.search(text):
                r.kind = "heading"
                r.level = 1 if r.h >= median_h * 1.8 else 2 if r.h >= median_h * 1.4 else 3


def _columns(rows: List[_Row]) -> Optional[List[Tuple[float, float]]]:
    """The column spans (left, right) shared by the cells of these rows, or None when they do not line up like a table.
    A column is where cells overlap across rows, so it does not matter whether its text is left-aligned, centred or
    right-aligned (a centred "Trading Symbol" over "MSFT" shares no left edge, but the two overlap)."""
    cells = sorted((c for r in rows for c in r.cells), key=lambda c: c[1])
    spans: List[List[float]] = []                       # [left, right, count]
    for c in cells:
        if spans and c[1] <= spans[-1][1] + COLUMN_TOL / 2:
            spans[-1][1] = max(spans[-1][1], c[3])
            spans[-1][2] += 1
        else:
            spans.append([c[1], c[3], 1])
    if len(spans) > MAX_COLUMNS or len(spans) < 2:
        return None
    if any(sp[2] < 2 for sp in spans):
        return None                                     # a column that only one row has is not a column
    if statistics.fmean(len(c[0]) for c in cells) > MAX_CELL_CHARS:
        return None                                     # long cells are running text set in columns
    if statistics.fmean(len(c[0].split()) for c in cells) >= PROSE_WORDS + 1:
        return None                                     # so are cells of five words or more a line
    if statistics.fmean(len(c[0]) for c in cells) <= 3.5 and sum(1 for r in rows if len(r.cells) >= len(spans)) < len(rows):
        return None                                     # pin numbers and labels round a drawing of a package, not a table
    return [(sp[0], sp[1]) for sp in spans]


def _table(rows: List[_Row], spans: List[Tuple[float, float]]) -> Dict[str, Any]:
    def col(c: Cell) -> int:
        return max(range(len(spans)), key=lambda i: (min(c[3], spans[i][1]) - max(c[1], spans[i][0]), -abs((c[1] + c[3]) / 2 - (spans[i][0] + spans[i][1]) / 2)))

    anchors = spans
    grid: List[List[str]] = []
    for r in rows:
        line = [""] * len(anchors)
        for c in r.cells:
            i = col(c)
            line[i] = (line[i] + " " + c[0]).strip()
        grid.append(line)
    has_digit = [any(re.search(r"\d", v) for v in line) for line in grid]
    header = bool(grid) and not has_digit[0] and (any(has_digit[1:]) or len(grid) >= 3) and all(grid[0])
    columns = [v or f"Column {i + 1}" for i, v in enumerate(grid[0])] if header else [f"Column {i + 1}" for i in range(len(anchors))]
    body = grid[1:] if header else grid
    return {"type": "table", "columns": columns, "header": header, "rows": body, "bbox": _box([c for r in rows for c in r.cells])}


def _entities(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    for i, b in enumerate(blocks):
        if b["type"] == "table":
            texts = [v for row in b["rows"] for v in row]
        elif b["type"] == "key_value":
            texts = [it["value"] for it in b["items"] if it.get("value")]
        elif b["type"] == "list":
            texts = b["items"]
        else:
            texts = [b.get("text", "")]
        seen = set()
        for t in texts:
            for kind, rx in _ENTITIES:
                for m in rx.finditer(t or ""):
                    key = (kind, m.group(0))
                    if key not in seen:
                        seen.add(key)
                        found.append({"type": kind, "value": m.group(0).strip(), "block": i})
    return found


def _drop_page_number(rows: List["_Row"]) -> Tuple[List["_Row"], Optional[str]]:
    """A bare page number that opens or closes the page, or sits in a margin, is page furniture, not content: it is returned apart."""
    number = None
    kept = []
    for k, r in enumerate(rows):
        edge = k == 0 or k == len(rows) - 1 or r.y0 > 0.9 or r.y1 < 0.06          # first or last line of the page, or in a margin
        if edge and len(r.cells) == 1 and r.kind not in ("given", "figure") and _PAGE_NUMBER.match(r.text.strip()):
            number = r.text.strip()
        else:
            kept.append(r)
    return kept, number


def _pair_captions(rows: List["_Row"]) -> List["_Row"]:
    """A value with a small caption printed under it, as on a cover page:  WASHINGTON / (STATE OF INCORPORATION).
    Each caption names the cell above it (matched by position), so the pair reads as label and value. Only done when every
    caption finds a cell and every cell has a caption."""
    out: List["_Row"] = []
    i = 0
    while i < len(rows):
        a = rows[i]
        b = rows[i + 1] if i + 1 < len(rows) else None
        done = False
        if b is not None and a.kind not in ("given", "figure") and b.kind not in ("given", "figure") and all(_CAPTION.match(c[0].strip()) for c in b.cells) \
                and not any(_CAPTION.match(c[0].strip()) for c in a.cells) and -0.6 * b.h <= b.y0 - a.y1 <= b.h * 1.6 and len(a.cells) == len(b.cells):
            pairs, used = [], set()
            for cap in b.cells:
                width = max(cap[3] - cap[1], 1e-6)
                best, overlap = None, 0.0
                for k, c in enumerate(a.cells):
                    ov = min(cap[3], c[3]) - max(cap[1], c[1])
                    if k not in used and ov > overlap:
                        best, overlap = k, ov
                if best is None or overlap < 0.3 * min(width, max(a.cells[best][3] - a.cells[best][1], 1e-6)):
                    pairs = []
                    break
                used.add(best)
                c = a.cells[best]
                pairs.append({"label": _nice(cap[0]), "value": c[0].strip(), "bbox": _box([c, cap])})
            if pairs and len(pairs) == len(a.cells):
                merged = _Row(a.cells + b.cells)
                merged.pairs = pairs
                out.append(merged)
                i += 2
                done = True
        if not done:
            out.append(a)
            i += 1
    return out


def _given_table(t: Dict[str, Any]) -> Dict[str, Any]:
    """A table another extractor found (digital pages), in the shape of a table block."""
    cols = [re.sub(r"^Col (\d+)$", r"Column \1", str(h)) if str(h).strip() else f"Column {i + 1}" for i, h in enumerate(t.get("columns") or [])]
    rows = [[str(v) for v in r] for r in t.get("rows", []) if any(str(v).strip() for v in r)]
    return {"type": "table", "columns": cols, "header": bool(cols), "rows": rows, "bbox": [round(v, 4) for v in t["bbox"]], "method": t.get("method", "given")}


MAX_EMPTY_CELLS = 0.4      # a table found from the layout alone with more blank cells than this is not trusted
MIN_BAND_ROWS = 6          # a column layout runs over at least this many rows, of which most have text on both sides
PROSE_WORDS = 4.0          # ...and the text on each side is lines of running text: this many words a line, and some start in lower case


def _prose_side(cells: List[Cell]) -> bool:
    if len(cells) < 3:
        return False
    words = statistics.fmean(len(c[0].split()) for c in cells)
    lower = sum(1 for c in cells if c[0].strip()[:1].islower()) / len(cells)
    return words >= PROSE_WORDS and lower >= 0.15


def _reading_order(rows: List[List[Cell]]) -> List[List[Cell]]:
    """Rows of cells in the order a person reads them. A page set in two columns of text (a paper, a datasheet's first page)
    arrives as rows that cross both columns, which mixes the two texts line by line. Where a clear gutter runs down a stretch of
    rows that are text on both sides, that stretch is returned as the whole left column, then the whole right column.
    Rows above and below it (a title, a full-width table) keep their place."""
    if len(rows) < MIN_BAND_ROWS:
        return rows
    best: Optional[Tuple[int, int, float]] = None                       # (first row, last row + 1, gutter x)
    for step in range(60, 141):
        gx = step / 200
        i = 0
        while i < len(rows):
            if any(c[1] < gx < c[3] for c in rows[i]):
                i += 1
                continue
            j = i
            while j < len(rows) and not any(c[1] < gx < c[3] for c in rows[j]):
                j += 1
            run = rows[i:j]
            both = [r for r in run if any(c[3] <= gx for c in r) and any(c[1] >= gx for c in r)]
            if len(run) >= MIN_BAND_ROWS and len(both) >= max(4, len(run) * 0.5):
                left = [c for r in run for c in r if c[3] <= gx]
                right = [c for r in run for c in r if c[1] >= gx]
                if _prose_side(left) and _prose_side(right) and (best is None or len(run) > best[1] - best[0]):
                    best = (i, j, gx)
            i = j
    if best is None:
        return rows
    a, b, gx = best
    left_rows = [[c for c in r if c[3] <= gx] for r in rows[a:b]]
    right_rows = [[c for c in r if c[1] >= gx] for r in rows[a:b]]
    return rows[:a] + [r for r in left_rows if r] + [r for r in right_rows if r] + rows[b:]


_CAPTION_START = re.compile(r"^\s*(figure|fig\.?|chart|graph|diagram|plot|photo|drawing|illustration)\s*[\dA-Z][\w.\-]*\s*[:.\-–—]?", re.I)
CAPTION_REACH = 0.07       # a caption is at most this far (page height) over or under its figure
CAPTION_LINES = 3


def _take_figures(rows_in: List[List[Cell]], figs: List[Dict[str, Any]]) -> Tuple[List[List[Cell]], List[Dict[str, Any]]]:
    """Lift the figures out of the rows: the cells printed inside a figure's box become its labels, and a line such as "Figure 2:
    Scaled dot-product attention" next to it becomes its caption (shared by the figures it sits under: "(left) ... (right) ...").
    Returns the remaining rows and the figure blocks."""
    rows = [list(r) for r in rows_in]
    blocks: List[Dict[str, Any]] = []
    for f in figs:
        x0, y0, x1, y1 = f["bbox"]
        labels: List[str] = list(f.get("labels") or [])
        have = {re.sub(r"\W", "", t.lower()) for t in labels}
        kept_rows: List[List[Cell]] = []
        for r in rows:
            inside = [c for c in r if x0 - 0.004 <= (c[1] + c[3]) / 2 <= x1 + 0.004 and y0 - 0.004 <= (c[2] + c[4]) / 2 <= y1 + 0.004]
            for c in inside:
                if re.sub(r"\W", "", c[0].lower()) not in have and c[0].strip():
                    labels.append(c[0].strip())
            rest = [c for c in r if c not in inside]
            if rest:
                kept_rows.append(rest)
        rows = kept_rows
        blocks.append({"type": "figure", "bbox": [round(v, 4) for v in f["bbox"]], "source": f.get("source"), "caption": None,
                       "labels": labels[:80], "kind": None, "reading": None, **({"digest": f["digest"]} if f.get("digest") else {})})
    gone: set = set()
    for fb in blocks:
        x0, y0, x1, y1 = fb["bbox"]
        for k, r in enumerate(rows):
            text = " ".join(c[0] for c in r).strip()
            top, bottom = min(c[2] for c in r), max(c[4] for c in r)
            near = (0 <= top - y1 <= CAPTION_REACH) or (0 <= y0 - bottom <= CAPTION_REACH)
            across = min(max(c[3] for c in r), x1 + 0.1) - max(min(c[1] for c in r), x0 - 0.1) > 0
            if near and across and _CAPTION_START.match(text):
                lines, last = [text], k
                while len(lines) < CAPTION_LINES and last + 1 < len(rows) and not re.search(r"[.!?]\s*$", lines[-1]):
                    nxt = rows[last + 1]
                    if min(c[2] for c in nxt) - max(c[4] for c in rows[last]) > 0.012:
                        break
                    lines.append(" ".join(c[0] for c in nxt).strip())
                    last += 1
                fb["caption"] = re.sub(r"\s+", " ", " ".join(lines)).strip()
                gone.update(range(k, last + 1))
                break
    return [r for k, r in enumerate(rows) if k not in gone], blocks


def build_page(rows_in: List[List[Cell]], has_layout: bool = True, tables: Optional[List[Dict[str, Any]]] = None,
               figures: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """{"blocks": [...], "entities": [...], "layout": bool} for one page given its rows of cells.
    Fax transmission stamps are not content and are left out. `tables` are tables another extractor already found
    (bbox as page fractions, columns, rows): the rows they cover are not rebuilt, the table takes their place."""
    from app.docproc.services.reading import pdf_pages
    from app.docproc.services.extraction import figures as figs_mod
    figs = [f for f in (figures or []) if has_layout]
    fig_blocks: List[Dict[str, Any]] = []
    if figs:
        rows_in, fig_blocks = _take_figures(rows_in, figs)
        tables = [t for t in (tables or []) if not any(figs_mod.overlap(t["bbox"], f["bbox"]) >= 0.5 for f in figs)]       # a drawing's labels are not a table
    rows = [_Row(r) for r in (_reading_order(rows_in) if has_layout else rows_in)
            if r and any(c[0].strip() for c in r) and not pdf_pages.is_fax_stamp(" ".join(c[0] for c in r))]
    for k, r in enumerate(rows):
        r.order = float(k)                                  # reading order: y, except down the columns of a column layout
    usual_h = statistics.median(r.h for r in rows) if rows else 0.01        # the page's usual text height, taken before figures and tables take their place
    for fb in fig_blocks:                                   # each figure takes its place in the reading order, among the rows beside and below it
        x0, y0, x1, y1 = fb["bbox"]
        later = [r for r in rows if r.kind != "figure" and r.y0 >= y0 - 0.002 and min(max(c[3] for c in r.cells), x1) - max(min(c[1] for c in r.cells), x0) > 0]
        later = later or [r for r in rows if r.kind != "figure" and r.y0 >= y0 - 0.002]
        marker = _Row([("", x0, y0, x1, y1)])
        marker.kind, marker.figure = "figure", fb
        marker.order = (min(r.order for r in later) - 0.5) if later else float(len(rows))
        rows.append(marker)
    rows.sort(key=lambda r: r.order)
    for t in tables or []:
        x0, y0, x1, y1 = t["bbox"]
        # the table's own text, header rows included (they may have been folded into its column names)
        flat_table = re.sub(r"[^a-z0-9]", "", " ".join(str(v) for row in (t.get("all_rows") or t.get("rows") or []) for v in row).lower())
        # only rows whose own text is in the table: the table's box may also hold a heading above it, which stays a heading
        table_words = set(re.sub(r"[^a-z0-9]", "", w.lower()) for row in (t.get("all_rows") or t.get("rows") or []) for v in row for w in str(v).split())
        table_words |= set(re.sub(r"[^a-z0-9]", "", w.lower()) for v in (t.get("columns") or []) for w in str(v).split())
        table_words.discard("")

        def _in_table(r: "_Row") -> bool:
            if all(re.sub(r"[^a-z0-9]", "", c[0].lower()) in flat_table for c in r.cells):
                return True
            # a row whose cells run together across columns (the text of two cells in one): its words are the table's, in another order
            words = [re.sub(r"[^a-z0-9]", "", w.lower()) for c in r.cells for w in c[0].split()]
            words = [w for w in words if w]
            return len(words) >= 2 and sum(1 for w in words if w in table_words) / len(words) >= 0.9

        inside = [r for r in rows if y0 - 0.002 <= (r.y0 + r.y1) / 2 <= y1 + 0.002 and r.x0 >= x0 - 0.02 and r.x0 <= x1 and _in_table(r)]
        if not inside or not t.get("rows"):
            continue
        top = min(r.y0 for r in inside)                     # the table starts at its first row, not at the edge of its box
        marker = _Row([("", x0, top, x1, top + 0.001)])
        marker.kind, marker.table = "given", _given_table(t)
        marker.order = min(r.order for r in inside) - 0.5
        rows = [r for r in rows if r not in inside]
        rows.append(marker)
        rows.sort(key=lambda r: r.order)
    if not rows:
        return {"blocks": [], "entities": [], "layout": has_layout}
    rows, page_number = _drop_page_number(rows)
    rows = _pair_captions(rows)
    if not rows:
        return {"blocks": [], "entities": [], "layout": has_layout}
    median_h = usual_h
    _classify(rows, median_h)

    blocks: List[Dict[str, Any]] = []
    i = 0
    while i < len(rows):
        r = rows[i]
        if r.kind == "given":
            blocks.append(r.table)  # type: ignore[arg-type]
            i += 1
        elif r.kind == "figure":
            blocks.append(r.figure)  # type: ignore[attr-defined]
            i += 1
        elif r.kind == "multi":
            j = i
            while j < len(rows) and rows[j].kind == "multi":
                j += 1
            group, anchors = rows[i:j], None
            if len(group) >= 2:
                anchors = _columns(group)
            table = _table(group, anchors) if anchors else None
            if table:
                cells = [v for row in table["rows"] for v in row]
                if cells and sum(1 for v in cells if not v) / len(cells) > MAX_EMPTY_CELLS:
                    table = None                                  # mostly gaps: labels round a drawing, not a table
            if table:
                blocks.append(table)
            else:                                       # side-by-side text that is not a table: keep it as lines
                for g in group:
                    blocks.append({"type": "paragraph", "text": " ".join(c[0] for c in g.cells), "bbox": g.box})
            i = j
        elif r.kind == "boxes":
            items, j = [], i
            group = None
            prev_block = blocks[-1] if blocks else None
            while j < len(rows) and rows[j].kind == "boxes":
                items += [dict(it) for it in rows[j].boxes or []]
                j += 1
            if prev_block and prev_block["type"] == "paragraph" and any(it["label"].lower() in ("yes", "no") for it in items):
                q = prev_block["text"]
                group = q if len(q) <= 130 else q[:127].rsplit(" ", 1)[0] + "..."                          # the question the Yes / No answers
            for it in items:
                it["group"] = group
            blocks.append({"type": "checkboxes", "items": items, "bbox": _box([c for g in rows[i:j] for c in g.cells])})
            i = j
        elif r.kind == "kv":
            items: List[Dict[str, Any]] = []
            j, prev = i, None
            while j < len(rows) and rows[j].kind == "kv" and (prev is None or rows[j].y0 - prev.y1 <= rows[j].h * 2.5):
                items += rows[j].pairs or _pairs(rows[j].cells)
                prev = rows[j]
                j += 1
            # a long value wraps onto the lines under it: those lines belong to the last label, not to a paragraph of their own
            while has_layout and prev is not None and j < len(rows) and rows[j].kind == "text" and items and items[-1].get("value") \
                    and rows[j].y0 - prev.y1 <= rows[j].h * PARAGRAPH_GAP and abs(rows[j].x0 - prev.x0) <= 0.04:
                items[-1]["value"] = (items[-1]["value"] + " " + rows[j].text.strip()).strip()
                items[-1]["bbox"][3] = round(max(items[-1]["bbox"][3], rows[j].y1), 4)
                prev = rows[j]
                j += 1
            blocks.append({"type": "key_value", "items": items, "bbox": _box([c for g in rows[i:j] for c in g.cells])})
            i = j
        elif r.kind == "heading":
            blocks.append({"type": "heading", "level": r.level, "text": r.text.strip(), "bbox": r.box})
            i += 1
        elif r.kind == "list":
            items, j = [], i
            ordered = not re.match(r"\s*[•●▪◦*\-]", r.text)
            while j < len(rows) and rows[j].kind == "list":
                m = _LIST.match(rows[j].text.strip())
                items.append(m.group(2).strip() if m else rows[j].text.strip())
                j += 1
                # a long item wraps onto the lines under it, set in from the bullet: those lines are part of it
                while has_layout and j < len(rows) and rows[j].kind == "text" and rows[j].y0 - rows[j - 1].y1 <= rows[j].h * PARAGRAPH_GAP \
                        and rows[j].x0 > rows[j - 1].x0 + 0.005 and rows[j].x0 < rows[j - 1].x0 + 0.08 and not _LIST.match(rows[j].text.strip()):
                    items[-1] = (items[-1] + " " + rows[j].text.strip()).strip()
                    j += 1
            blocks.append({"type": "list", "ordered": ordered, "items": items, "bbox": _box([c for g in rows[i:j] for c in g.cells])})
            i = j
        else:
            j, text = i, []
            while j < len(rows) and rows[j].kind == "text":
                if j > i:
                    if has_layout:
                        if rows[j].y0 - rows[j - 1].y1 > rows[j].h * PARAGRAPH_GAP or abs(rows[j].x0 - rows[i].x0) > 0.04:
                            break
                    elif not rows[j].text.strip()[:1].islower() or _SENTENCE_END.search(rows[j - 1].text):
                        break                           # no positions: only an unfinished sentence continues on the next line
                text.append(rows[j].text.strip())
                j += 1
            blocks.append({"type": "paragraph", "text": " ".join(text), "bbox": _box([c for g in rows[i:j] for c in g.cells])})
            i = j
    out = {"blocks": blocks, "entities": _entities(blocks), "layout": has_layout}
    if page_number:
        out["page_number"] = page_number
    return out


def build_from_lines(lines: List[str]) -> Dict[str, Any]:
    """A page with no positions (read by a vision model): each line becomes a row, and cells keep their order, so the
    rules that need no geometry still work (label/value, headings in capitals, lists, tables of equal-width rows).
    Bounding boxes are dropped, because the ones made up here mean nothing."""
    rows: List[List[Cell]] = []
    for n, line in enumerate(l for l in lines if l.strip()):
        parts = line.split(" | ")
        rows.append([(p, 0.1 + 0.2 * k, n * 0.02, 0.1 + 0.2 * k + 0.15, n * 0.02 + 0.01) for k, p in enumerate(parts)])
    page = build_page(rows, has_layout=False)
    for b in page["blocks"]:
        b.pop("bbox", None)
        for it in b.get("items", []) if b["type"] == "key_value" else []:
            it.pop("bbox", None)
    return page


# ---- running headers and footers (what every page of a document repeats) ----------------------------------------------

EDGE_BLOCKS = 3            # a running line is one of the first or last blocks of a page
MIN_RUNNING_PAGES = 3      # ...repeated on at least this many pages, and on half of them


_EDGE_NUMBER = re.compile(r"^\s*(?:page\s*)?\d+(?:\s*(?:of|/)\s*\d+)?\b|\b(?:page\s*)?\d+(?:\s*(?:of|/)\s*\d+)?\s*$", re.I)


def _running_key(text: str) -> str:
    """What a line says once its page number is taken off the front or the back, or "" when there is too little left to tell it
    from a heading that merely carries a number ("Section 3")."""
    t = re.sub(r"\s+", " ", text.lower()).strip(" |:-")
    stripped = _EDGE_NUMBER.sub(" ", t).strip(" |:-")
    if stripped != t and len(re.findall(r"[a-z]", stripped)) < 12:
        return ""
    return stripped


def strip_running(structures: List[Dict[str, Any]]) -> int:
    """Take the lines a document repeats on its pages (a part-number banner, a document title, "Page 3 of 9", a confidentiality
    note) out of each page's blocks and keep them once per page under `running`. They are not content, and they hid each
    page's own title from the titles the page descriptions are built from. Returns how many lines were taken out."""
    pages = [s for s in structures if s and s.get("blocks")]
    if len(pages) < MIN_RUNNING_PAGES:
        return 0
    seen: Dict[str, int] = {}
    for s in pages:
        blocks = s["blocks"]
        edge = list(blocks[:EDGE_BLOCKS]) + list(blocks[-EDGE_BLOCKS:])
        keys = {_running_key(b["text"]) for b in edge if b.get("type") in ("heading", "paragraph") and 3 <= len(b.get("text", "")) <= 140}
        for k in keys:
            seen[k] = seen.get(k, 0) + 1
    repeated = {k for k, n in seen.items() if k and n >= MIN_RUNNING_PAGES and n >= len(pages) / 2 and len(re.findall(r"[a-z]", k)) >= 4}
    taken = 0
    for s in pages:
        blocks = s["blocks"]
        edge_ids = {id(b) for b in list(blocks[:EDGE_BLOCKS]) + list(blocks[-EDGE_BLOCKS:])}
        gone = [b for b in blocks if id(b) in edge_ids and b.get("type") in ("heading", "paragraph") and _running_key(b.get("text", "")) in repeated]
        if gone:
            s["running"] = [{"text": b["text"], "where": "top" if blocks.index(b) < len(blocks) / 2 else "bottom"} for b in gone]
            s["blocks"] = [b for b in blocks if not any(b is g for g in gone)]
            taken += len(gone)
    return taken
