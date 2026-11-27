"""
Digital (born-digital) pages: read them with code, not a model.

A PDF with a real text layer already holds its text and its table cells exactly. Asking a model to read them back costs
tokens and can only add mistakes. So for these pages:

    tables    the layout table extractor's cells, copied verbatim            (free, exact)
    fields    "Label: value" pairs found in the text                         (free, exact)
    headers   a column header that spans several rows is combined by a model (a few lines of text, once per distinct
              header; without a model the raw header cells are joined)

and a model reads the page only when the page is form-like but gave code almost nothing to work with (labels above
values, no ruled structure). Prose pages are left as text: there is no field to extract from a paragraph.

Values copied from the text layer are marked "agreed" with method "text-layer": there is no second opinion to disagree
with, the PDF itself is the source.
"""
import asyncio
import json

import pymupdf as fitz
import logging
import re
from collections import Counter
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.docproc import config
from app.docproc.services.extraction import kv, labels
from app.docproc.services.reading import locate, pdf_pages

logger = logging.getLogger("app.services.digital")

MIN_KV_FOR_STRUCTURE = 3     # fewer "Label: value" pairs than this, and no table, means code found no structure
MIN_LINES_FOR_FORM = 6       # a page with less text than this is a stub, not a form
PROSE_WORDS_PER_LINE = 8     # a line this long is a sentence, not a label
MAX_VALUE_CHARS = 80         # a labelled value is short; "Cybersecurity: Our board oversees..." is a sentence with a lead-in
MAX_PAIR_LINE = 90           # ...and so is a whole line this long


def _is_year(c: str) -> bool:
    return bool(re.fullmatch(r"(19|20)\d\d", c.strip()))


def _is_data(c: str) -> bool:
    """A cell holding an amount (not a year, not a date part): digits with a separator, sign, currency or percent."""
    c = c.strip()
    return bool(c) and bool(re.search(r"\d", c)) and not _is_year(c) and bool(re.fullmatch(r"[\s$(\-.\d,%)—–]+", c))


def split_header(table: Dict[str, Any]) -> Tuple[List[List[str]], List[List[str]]]:
    """(header rows, body rows). The header block is the leading rows that put text over the value columns and hold no
    amounts yet ("Three | Months Ended", "March 31,", "2026 | 2025"). A row that is only a label ("Revenue:") starts the body."""
    rows = [[(c or "").strip() for c in r] for r in table["rows"]]
    if table.get("header_rows") is not None:          # a ruled grid says where its header is: its first row
        return rows[:table["header_rows"]], rows[table["header_rows"]:]
    n = 0
    for r in rows:
        values = r[1:]
        if any(_is_data(c) for c in values) or not any(values):
            break
        n += 1
    if n == len(rows) and n >= 2:          # no amounts anywhere (a table of names and codes): only the first row is a header
        n = 1
    return rows[:n], rows[n:]


def raw_labels(header_rows: List[List[str]], ncols: int) -> List[str]:
    """No model: each column's header cells joined top to bottom. Correct for a one-row header, merely readable otherwise."""
    out: List[str] = []
    for j in range(ncols):
        parts: List[str] = []
        for r in header_rows:
            if j < len(r) and r[j] and (not parts or r[j] != parts[-1]):       # a label that spans two header rows is the same text in both
                parts.append(r[j])
        out.append(" ".join(parts) or ("Item" if j == 0 else f"Column {j}"))
    return out


def _page_words(path: str, page_num: int, bbox: List[float]) -> List[Tuple[float, float, float, float, str]]:
    with pdf_pages.FITZ_LOCK, pdf_pages.open_pdf(path) as pdf:
        page = pdf[page_num - 1]
        noise = pdf_pages._noise(page)                  # the letters of a watermark and the numbers in callout circles are not the page's figures
        found = [w for w in page.get_text("words", clip=tuple(bbox)) if not noise(w)]
    return [(w[0], w[1], w[2], w[3], w[4]) for w in found if w[4].strip()]


def _lines_of(words: List[Tuple[float, float, float, float, str]]) -> List[List[Tuple[float, float, float, float, str]]]:
    """Words grouped into lines top to bottom, each line left to right."""
    heights = sorted(w[3] - w[1] for w in words)
    tol = (heights[len(heights) // 2] if heights else 8.0) * 0.6
    lines: List[List[Tuple[float, float, float, float, str]]] = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        mid = (w[1] + w[3]) / 2
        if lines and abs(mid - sum((x[1] + x[3]) / 2 for x in lines[-1]) / len(lines[-1])) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(l, key=lambda w: w[0]) for l in lines]


def geometric_labels(path: str, page_num: int, bbox: List[float], header_rows: List[List[str]], ncols: int) -> Optional[List[str]]:
    """Column labels from where the header words sit on the page, with no model.

    A heading such as "Three Months Ended March 31," is printed once over the two columns it covers, so the table extractor cuts
    it into pieces ("Three" | "Months Ended") and joining the cells down each column gives nonsense ("Three 2026"). Here the
    words of each header line are grouped into phrases by the gaps between them; a phrase that touches several columns spans
    them, and a phrase in a middle row that sits under such a span takes the same columns. The last row names one column each
    (years, dates) and never spreads. Each column's label is its phrases read top to bottom.

    Returns None whenever anything does not line up exactly (the words are not the cells' words, the phrases leave a column
    unnamed, two columns would get the same name): the caller then falls back to the cells, or to a model.
    """
    if len(header_rows) < 2 or ncols < 3:
        return None
    try:
        words = _page_words(path, page_num, bbox)
        first = " ".join(c for c in header_rows[0][1:] if c).split()
        if not words or not first:
            return None
        lines = _lines_of(words)
        start = next((i for i, l in enumerate(lines) if " ".join(first) in " ".join(w[4] for w in l)), None)
        if start is None or start + len(header_rows) > len(lines):
            return None
        head = lines[start:start + len(header_rows)]
        phrases: List[List[Tuple[int, int, float, float, str]]] = []        # per row: (first column, last column, x0, x1, text)
        for row, line in zip(header_rows, head):
            cells = [c for c in row]
            if [w[4] for w in line] != " ".join(c for c in cells if c).split():
                return None                                                  # the words are not exactly the cells' words
            col_of: List[int] = []
            for j, c in enumerate(cells):
                col_of += [j] * len(c.split())
            heights = sorted(w[3] - w[1] for w in line)
            gap = max(heights[len(heights) // 2], 4.0) * 1.0
            groups: List[List[int]] = [[0]]
            for k in range(1, len(line)):
                if line[k][0] - line[k - 1][2] <= gap:
                    groups[-1].append(k)
                else:
                    groups.append([k])
            out = []
            for g in groups:
                cols = sorted({col_of[k] for k in g if col_of[k] >= 1})
                if cols:
                    out.append((cols[0], cols[-1], line[g[0]][0], line[g[-1]][2], " ".join(line[k][4] for k in g)))
            phrases.append(out)
        last = len(phrases) - 1
        spans: List[List[Tuple[int, int, float, float, str]]] = []          # the columns each phrase finally covers
        for r, row_phrases in enumerate(phrases):
            row_spans = []
            for a, b, x0, x1, text in row_phrases:
                if a == b and r < last:
                    best, best_ov = None, 0.0
                    for up in spans[:r]:
                        for ua, ub, ux0, ux1, _ in up:
                            ov = min(x1, ux1) - max(x0, ux0)
                            if ub > ua and ov > best_ov and ov >= 0.6 * (x1 - x0):
                                best, best_ov = (ua, ub), ov
                    if best:
                        a, b = best
                row_spans.append((a, b, x0, x1, text))
            spans.append(row_spans)
        parts: List[List[str]] = [[] for _ in range(ncols)]
        for row_spans in spans:
            for a, b, _x0, _x1, text in row_spans:
                for c in range(a, min(b, ncols - 1) + 1):
                    if not parts[c] or parts[c][-1] != text:
                        parts[c].append(text)
        names = ["Item"] + [" ".join(parts[c]) for c in range(1, ncols)]
        if any(not n for n in names) or len(set(names)) != len(names):
            return None
        return names
    except Exception as e:
        logger.warning("Geometric header labels failed, using the header cells: %s", e)
        return None


Ask = Callable[[str, str], Awaitable[str]]


async def default_ask(model: str, prompt: str) -> str:
    """A short text-only call (no image). Recorded in the usage tracker like every other model call."""
    from app.docproc.engines.providers import llm
    return await llm.complete(model, prompt, None, stage="header_labels")


_HEADER_PROMPT = """The first rows of a table from a PDF are shown below, cells separated by " | ". A heading that spans several
columns was split across cells. Write ONE short label per value column, combining the rows that apply to it (for example
"Three Months Ended March 31, 2026"). The first cell of each row is the row-label column: skip it.

Rows:
{rows}

Return JSON only: {{"columns": ["<label for column 1>", "<label for column 2>", ...]}} with exactly {n} labels."""


async def header_labels(header_rows: List[List[str]], ncols: int, model: Optional[str], ask: Optional[Ask], memo: Dict[str, List[str]]) -> List[str]:
    """Column labels for a table. One short model call per distinct header (a filing repeats the same header on every
    statement), none when the header is a single row or no model is available."""
    fallback = raw_labels(header_rows, ncols)
    if len(header_rows) < 2 or not model or ask is None:
        return fallback
    shown = "\n".join(" | ".join(r) for r in header_rows)
    if shown in memo:
        return memo[shown]
    try:
        data = json.loads(await ask(model, _HEADER_PROMPT.format(rows=shown, n=ncols - 1)))
        cols = [str(c).strip() for c in data.get("columns", [])] if isinstance(data, dict) else []
    except Exception as e:
        logger.warning("Header labelling failed, using the raw header cells: %s", e)
        cols = []
    got = (["Item"] + cols) if len(cols) == ncols - 1 and all(cols) else fallback
    memo[shown] = got
    return got


def _slug(text: str) -> str:
    """Plain lower-case words joined by underscores: "No" stays "no" (the label canonicaliser would read it as "number")."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48]


def is_amount_table(t: Dict[str, Any]) -> bool:
    """A table of amounts: two or more value columns that are mostly figures. The extractor behind `tables` is built for these
    (statements, schedules). A table of names and codes, or a contents list with a page-number column, is read better from
    the layout (structure.py), which also copes with centred and right-aligned columns. A table drawn with ruling lines is always
    taken from its rules, whatever it holds: the lines give the cells exactly."""
    if t.get("method") == "grid" and t.get("header_rows") is not None:
        return True
    _header, body = split_header(t)
    body = [r for r in body if any(r)]
    width = max((len(r) for r in body), default=0)
    figures = 0
    for j in range(1, width):
        cells = [r[j] for r in body if j < len(r) and r[j].strip()]
        if cells and sum(1 for c in cells if _is_data(c)) / len(cells) >= 0.5:
            figures += 1
    return figures >= 2


def _points(box: Optional[List[float]], size: Dict[str, Any]) -> Optional[List[float]]:
    """A box given as fractions of the page, in PDF points."""
    if not box or not size or not size.get("width"):
        return None
    return [round(box[0] * size["width"], 2), round(box[1] * size["height"], 2), round(box[2] * size["width"], 2), round(box[3] * size["height"], 2)]


def tables_from_structure(blocks: List[Dict[str, Any]], size: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Tables the layout finder made (not the ones the extractor supplied) in the extractor's shape, so they are read the same way."""
    out = []
    for i, b in enumerate(blocks):
        if b.get("type") == "table" and not b.get("method") and b.get("bbox"):
            rows = ([b["columns"]] if b.get("header") else []) + b["rows"]
            out.append({"rows": rows, "headers": b["columns"], "bbox": _points(b["bbox"], size), "method": "layout",
                        "title": _title_above(blocks, i)})
    return out


def titled(tables: List[Dict[str, Any]], blocks: List[Dict[str, Any]], size: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The extractor's tables with the name the page gives each (the heading or caption just above it) where there is one.
    The table block the layout made for each of them (same place on the page) says what sits above it."""
    out = []
    for t in tables:
        t = dict(t)
        box = t.get("bbox")
        if box and size and size.get("width") and not t.get("title"):        # a title bar drawn over the table names it already
            mine = [box[0] / size["width"], box[1] / size["height"], box[2] / size["width"], box[3] / size["height"]]
            for i, b in enumerate(blocks):
                bb = b.get("bbox")
                if b.get("type") == "table" and b.get("method") and bb and all(abs(x - y) < 0.01 for x, y in zip(bb, mine)):
                    title = _title_above(blocks, i)
                    if title:
                        t["title"] = title
                    break
        out.append(t)
    return out


FIELD_LABEL_REACH = 14.0    # a label above a field is at most this far (points) over its top edge
FIELD_FAR = 240.0           # a label in the column to the left of a field may be this far from it
FIELD_GAP = 22.0            # words of one label are at most this far apart


def _near_run(words: List[Tuple[float, float, float, float, str]], start: Tuple[float, float, float, float, str], direction: int) -> str:
    """The words of the line `start` is on, from `start` away to the left (-1) or right (+1), up to a gap."""
    mid = (start[1] + start[3]) / 2
    line = sorted((w for w in words if abs((w[1] + w[3]) / 2 - mid) <= (w[3] - w[1]) * 0.6), key=lambda w: w[0])
    out, edge = [], None
    ordered = line if direction > 0 else list(reversed(line))
    for w in ordered:
        if (direction > 0 and w[0] < start[0]) or (direction < 0 and w[2] > start[2]) or w is start:
            continue
        if edge is not None and abs((w[0] - edge) if direction > 0 else (edge - w[2])) > FIELD_GAP:
            break
        out.append(w[4])
        edge = w[2] if direction > 0 else w[0]
    return " ".join(out if direction > 0 else reversed(out))


def fields_from_widgets(widgets: List[Dict[str, Any]], words: List[Dict[str, Any]], size: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(label/value fields, check boxes) of a fillable form, each named by the printed text next to it: on its left, else on the
    line above; a check box by the words on its right. The form's own field names ("topmostSubform[0].Page1[0].f1_01[0]") say
    nothing, but where the box sits on the page does. A blank form gives its fields with no values: its schema."""
    ws = [(w["bbox"][0], w["bbox"][1], w["bbox"][2], w["bbox"][3], w["text"]) for w in words if w.get("text", "").strip()]
    boxes_at = [w["bbox"] for w in widgets]
    fields: List[Dict[str, Any]] = []
    boxes: List[Dict[str, Any]] = []
    for w in sorted(widgets, key=lambda w: (round(w["bbox"][1] / 4), w["bbox"][0])):
        x0, y0, x1, y1 = w["bbox"]
        inside = [t for t in ws if x0 <= (t[0] + t[2]) / 2 <= x1 and y0 <= (t[1] + t[3]) / 2 <= y1]
        if w["type"] in ("CheckBox", "RadioButton"):
            right = [t for t in ws if t[0] >= x1 - 1 and abs((t[1] + t[3]) / 2 - (y0 + y1) / 2) <= max(6.0, (y1 - y0) * 0.7) and t[0] - x1 <= FIELD_GAP]
            label = _near_run(ws, min(right, key=lambda t: t[0]), +1) if right else ""
            label = " ".join([min(right, key=lambda t: t[0])[4], label]).strip() if right else ""
            boxes.append({"label": label or "Option", "checked": bool(w.get("value")), "group": None, "bbox": [x0, y0, x1, y1]})
            continue
        left = [t for t in ws if t[2] <= x0 + 1 and x0 - t[2] <= FIELD_GAP and abs((t[1] + t[3]) / 2 - (y0 + y1) / 2) <= max(6.0, (y1 - y0) * 0.7)
                and not any(b[0] <= (t[0] + t[2]) / 2 <= b[2] and b[1] <= (t[1] + t[3]) / 2 <= b[3] for b in boxes_at)]
        label_far = ""
        if not left:                 # a label in the column to the left (a table of "Provider Name | [field]"): the nearest text on the row, with no other field between
            row_mid = (y0 + y1) / 2
            far = [t for t in ws if t[2] <= x0 + 1 and x0 - t[2] <= FIELD_FAR and abs((t[1] + t[3]) / 2 - row_mid) <= max(6.0, (y1 - y0) * 0.7)
                   and not any(b is not w["bbox"] and b[2] <= x0 + 1 and b[0] >= t[2] - 1 and b[1] <= row_mid <= b[3] for b in boxes_at)
                   and not any(b[0] <= (t[0] + t[2]) / 2 <= b[2] and b[1] <= (t[1] + t[3]) / 2 <= b[3] for b in boxes_at)]
            left = far
            if far:                  # such a label may run over several lines ("Reason for" / "Admission"), centred on its row
                top_mid = y0 + (far[0][3] - far[0][1]) / 2
                near = min(far, key=lambda t: (round(min(abs((t[1] + t[3]) / 2 - row_mid), abs((t[1] + t[3]) / 2 - top_mid)) / 3), -t[2]))
                h = max(1.0, near[3] - near[1])
                cols = [t for t in far if abs(t[0] - near[0]) <= FIELD_FAR and abs(((t[0] + t[2]) / 2) - ((near[0] + near[2]) / 2)) <= FIELD_FAR]
                lines_y = sorted({round((t[1] + t[3]) / 2) for t in cols})
                chain = [round((near[1] + near[3]) / 2)]
                for yy in sorted(lines_y):
                    if yy not in chain and min(abs(yy - c) for c in chain) <= h * 1.6:
                        chain.append(yy)
                got = sorted((t for t in cols if min(abs((t[1] + t[3]) / 2 - c) for c in chain) <= h * 0.6), key=lambda t: (round((t[1] + t[3]) / 2 / h), t[0]))
                label_far = " ".join(t[4] for t in got)
        if left:
            near = max(left, key=lambda t: t[2])
            label = label_far or " ".join([_near_run(ws, near, -1), near[4]]).strip()
        else:
            above = [t for t in ws if t[3] <= y0 + 2 and y0 - t[3] <= FIELD_LABEL_REACH and t[2] >= x0 - 2 and t[0] <= x1 + 2 and t not in inside]
            label = ""
            if above:
                row = max(above, key=lambda t: t[3])
                mid = (row[1] + row[3]) / 2
                def line_at(m: float, h: float) -> List[Tuple[float, float, float, float, str]]:
                    return sorted((t for t in ws if abs((t[1] + t[3]) / 2 - m) <= h * 0.6 and t[2] >= x0 - 2 and t[0] <= x1 + 2), key=lambda t: t[0])

                h = row[3] - row[1]
                parts = [line_at(mid, h)]
                # a label set over two lines: the line above joins it when no other field sits between them
                upper = line_at(mid - h * 1.25, h)
                if upper and not any(b[3] > upper[0][3] - 1 and b[1] < parts[0][0][1] + 1 and b[0] < x1 and b[2] > x0 and b is not w["bbox"] for b in boxes_at):
                    parts.insert(0, upper)
                label = " ".join(t[4] for part in parts for t in part)
        fields.append({"label": re.sub(r"\s+", " ", label).strip(" :.") or "Field", "value": (w.get("value") or "").strip(),
                       "bbox": [x0, y0, x1, y1], "type": "text"})
    return fields, boxes


def _title_above(blocks: List[Dict[str, Any]], i: int) -> Optional[str]:
    """What the page calls the table at blocks[i]: the heading directly above it, or a short caption ("Securities registered
    pursuant to Section 12(b) of the Act:"). A long paragraph is not a title."""
    j = i - 1
    while j >= max(0, i - 3):
        prev = blocks[j]
        text = (prev.get("text") or "").strip()
        if prev.get("type") == "paragraph" and re.fullmatch(r"\(.*\)|[\s(]*in (millions|thousands|billions)[^.]*", text, re.I | re.S):
            j -= 1                                       # "(In millions) (Unaudited)": a note on units, the title is above it
            continue
        if prev.get("type") == "heading" and text and len(re.findall(r"\d", text)) >= 3:
            return None                                  # "PO BOX 000", "Page 12 of 40": an address or a running line, not a title
        if prev.get("type") == "heading" and text:
            return text.title() if text.isupper() else text
        items = prev.get("items") or []
        if prev.get("type") == "key_value" and len(items) == 1 and (not items[0].get("value") or str(items[0]["value"]).startswith("(")):
            label = str(items[0]["label"]).strip()                  # "CURRENT MEDICATIONS: (include prescriptions ...)" over a table to fill in
            return label.title() if label.isupper() else label
        if prev.get("type") == "paragraph" and 2 <= len(text.split()) <= 12 and not text.endswith((".", "?")):
            return text.rstrip(": ")
        return None
    return None


def facts_from_structure(blocks: List[Dict[str, Any]], size: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Label and value pairs the layout found, including those with no colon (a value over its caption, "Commission File Number 001-37845")."""
    out = []
    for b in blocks:
        if b.get("type") == "key_value":
            for it in b["items"]:
                if it.get("value") and len(it["value"]) <= 2 * MAX_VALUE_CHARS and len(it["label"]) <= MAX_VALUE_CHARS and not _instruction(it["label"], it["value"]):
                    out.append({"label": it["label"], "value": it["value"], "bbox": _points(it.get("bbox"), size)})
    return out


def _instruction(label: str, value: str) -> bool:
    """A heading with its instruction after it ("ALLERGIES: (include allergies to medications, dyes ...)"): the words tell the person what to write, they are not a value."""
    v = (value or "").strip()
    return label.strip().isupper() and v.startswith("(") and v.endswith(")")


def boxes_from_structure(blocks: List[Dict[str, Any]], size: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for b in blocks:
        if b.get("type") == "checkboxes":
            out += [{"label": it["label"], "checked": bool(it["checked"]), "group": it.get("group"), "bbox": _points(b.get("bbox"), size)} for it in b["items"]]
    return out


def labelled_table(path: str, page_num: int, t: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """{"columns", "rows"} of a detected table with its header folded into proper column names and removed from the rows;
    the same naming the extraction uses, so the page view and the extracted data agree. None for a table with no body."""
    header, body = split_header(t)
    body = [r for r in body if any(r)]
    ncols = max((len(r) for r in body), default=0)
    if not body or ncols < 2:
        return None
    cols = geometric_labels(path, page_num, t["bbox"], header, ncols) or raw_labels(header, ncols)
    return {"columns": cols, "rows": [r + [""] * (ncols - len(r)) for r in body], "method": t.get("method")}


_TITLE_OK = re.compile(r"[A-Za-z]{3}")
_SECTION_MARKER = re.compile(r"^(?:(?:PART|ITEM|NOTE|SECTION|PAGE)\s+[IVX\d]+\.?|TABLE OF CONTENTS)$", re.I)


def table_title(lines: List[str], first_label: str = "") -> Optional[str]:
    """The heading just above a table: the last mostly-capitalised short line before the table's first row label."""
    first_word = first_label.split()[0] if first_label.split() else ""
    stop = next((i for i, l in enumerate(lines) if first_label and l.strip().startswith(first_label[:20])), None)
    if stop is None and len(first_word) >= 4:         # a merged row label ("Assets Current assets:") is several lines in the text
        stop = next((i for i, l in enumerate(lines) if l.strip().split()[:1] == [first_word]), None)
    if stop is None:
        return None                                   # cannot place the table in the text: leave it untitled rather than guess a footer
    for line in reversed(lines[:stop]):
        t = line.strip()
        letters = [c for c in t if c.isalpha()]
        if _SECTION_MARKER.match(t):
            continue                                   # "PART I", "ITEM 2", "Table of Contents": running headers, not a table's name
        if re.search(r":\s*\S", t) or t.endswith(":") or t.startswith("[") or len(re.findall(r"\d", t)) >= 3 or len(set(letters)) < 4:
            continue                                   # "PO BOX 000", a date, a barcode ("IIIIIII"): not a name
        if 4 <= len(t) <= 80 and _TITLE_OK.search(t) and letters and sum(c.isupper() for c in letters) / len(letters) >= 0.7:
            return t.title() if t.isupper() else t
    return None



# ---------------------------------------------------------------------------------------------------------------------
# Self-verification. Code is only as good as its table detector, and PDFs are endlessly varied. So every table is checked
# against the page itself: each number printed inside the table's region must appear in the cells that were extracted.
# ---------------------------------------------------------------------------------------------------------------------
COVERAGE_OK = 0.98      # share of the numbers printed in a table's region that must be in its cells
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(tokens: List[str]) -> Counter:
    """Amounts only (signs and currency marks are formatting here): "$15,089" and "(1,234)" count as 15089 and 1234."""
    return Counter(m.replace(",", "") for t in tokens for m in _NUM.findall(t))


_HEADING_WORDS = {"note", "notes", "item", "part", "section", "exhibit", "schedule", "table", "figure", "page", "chapter", "article", "appendix"}
_MARKER = re.compile(r"\(?\d{1,2}\)?\.?")


def _words_in(path: str, page_num: int, bbox: List[float]) -> List[str]:
    """The text tokens printed inside `bbox`, minus footnote and note markers (a 1-2 digit token set noticeably smaller
    than the surrounding text): those are references, not table data, and must not count as numbers a table lost."""
    x0, y0, x1, y1 = bbox
    spans: List[Tuple[str, float]] = []
    with pdf_pages.FITZ_LOCK, pdf_pages.open_pdf(path) as pdf:
        circles = pdf_pages._callouts(pdf[page_num - 1])          # numbers in callout circles are markers, not figures of the table
        for b in pdf[page_num - 1].get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"]:
            for line in b.get("lines", []):
                for sp in line["spans"]:
                    bx0, by0, bx1, by1 = sp["bbox"]
                    if any(c[0] - 1 <= (bx0 + bx1) / 2 <= c[2] + 1 and c[1] - 1 <= (by0 + by1) / 2 <= c[3] + 1 for c in circles):
                        continue
                    if x0 - 1 <= (bx0 + bx1) / 2 <= x1 + 1 and y0 - 1 <= (by0 + by1) / 2 <= y1 + 1:
                        spans.append((sp["text"], sp["size"]))
    sizes = sorted(sz for _, sz in spans)
    median = sizes[len(sizes) // 2] if sizes else 0.0
    out: List[str] = []
    for text, sz in spans:
        for tok in text.split():
            if sz < 0.8 * median and _MARKER.fullmatch(tok):
                continue
            if out and out[-1].lower().strip(".:") in _HEADING_WORDS and _MARKER.fullmatch(tok):
                continue                                 # "NOTE 7", "ITEM 6.": a heading's number, not table data
            out.append(tok)
    return out


def coverage(path: str, page_num: int, bbox: List[float], rows: List[List[str]]) -> Tuple[float, List[str]]:
    """(share of the page's numbers in `bbox` found in `rows`, the numbers that were not)."""
    page = _numbers(_words_in(path, page_num, bbox))
    if not page:
        return 1.0, []
    got = _numbers([c for r in rows for c in r if c])
    missing = page - got
    lost = sum(missing.values())
    return 1.0 - lost / sum(page.values()), sorted(missing.elements())[:6]


def _raw_rows(t: Dict[str, Any]) -> List[List[str]]:
    rows = [[(c or "").strip() for c in r] for r in t["rows"]]
    # The header row holds numbers too (years, "Q3 2025"): leaving it out made every table with a dated header look incomplete.
    return ([[(c or "").strip() for c in t["headers"]]] + rows) if t.get("headers") else rows


def grid_alternative(path: str, page_num: int, bbox: List[float]) -> Optional[Dict[str, Any]]:
    """PyMuPDF's own ruled-grid finder over the same region: a second, independent way to read the table."""
    try:
        with pdf_pages.FITZ_LOCK, pdf_pages.open_pdf(path) as pdf:
            found = pdf[page_num - 1].find_tables(clip=tuple(bbox)).tables
            rows = [[(c or "").strip() for c in r] for t in found[:1] for r in t.extract()]
    except Exception:
        return None
    return {"rows": rows, "headers": [], "method": "layout", "bbox": bbox} if rows and any(any(r) for r in rows) else None


def verified_table(path: str, page_num: int, t: Dict[str, Any]) -> Tuple[Dict[str, Any], float, List[str]]:
    """The better of the two table readings for this region, with its coverage and what it missed."""
    best, (cov, missing) = t, coverage(path, page_num, t["bbox"], _raw_rows(t))
    if cov < COVERAGE_OK and t.get("header_rows") is None:       # a ruled grid is already read from the page's own words and lines
        alt = grid_alternative(path, page_num, t["bbox"])
        if alt:
            acov, amissing = coverage(path, page_num, t["bbox"], _raw_rows(alt))
            if acov > cov:
                best, cov, missing = alt, acov, amissing
    return best, cov, missing


_NUM_LINE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")


def looks_tabular(lines: List[str]) -> bool:
    """Several lines that each carry two or more amounts: a table, whether or not the detector found one."""
    rows = sum(1 for l in lines if len(_NUM_LINE.findall(l)) >= 2 and len(re.findall(r"[A-Za-z]{3}", l)) >= 1)
    return rows >= 4


_FORM_LINE = re.compile(r"^(?:SCHEDULE|FORM)\s+[A-Z0-9][\w\-/.]*(?:\s+[A-Z0-9][\w\-/.]*)?$", re.I)


def page_title(lines: List[str]) -> Optional[str]:
    """The document's heading near the top of a page. A line that names a form ("FORM 10-Q", "Form W-9") wins; otherwise the
    longest short, capitalised line without a colon (the first one would often be a letterhead such as "UNITED STATES")."""
    top = [l.strip() for l in lines[:12]]
    for t in top:
        if _FORM_LINE.match(t) and len(t) <= 40:
            return t.upper() if len(t.split()) <= 3 and t.upper() == t else t
    best: Optional[str] = None
    for t in top:
        letters = [c for c in t if c.isalpha()]
        if 3 <= len(t) <= 80 and ":" not in t and letters and sum(c.isupper() for c in letters) / len(letters) >= 0.7:
            if best is None or len(t) > len(best):
                best = t
    return (best.title() if best.isupper() else best) if best else None


_GENERIC_HEAD = re.compile(r"(?:Column \d+|Item|Line item|Col \d+|Field|Information|Value|Details?|Parameter)")
_BOXES = "\u2610\u25a1\u2611\u2612\u2713\u25a0"


def _not_a_table(t: Dict[str, Any]) -> bool:
    """A ruled box that only holds check boxes (a list of conditions to tick), or a box of labels to write beside (Patient Name:, MR#:, DATE:)
    is a form's fields, not a table of rows."""
    cells = [str(c).strip() for r in [t.get("headers") or []] + list(t.get("rows") or []) for c in r if str(c).strip()]
    if len(cells) >= 4 and sum(1 for c in cells if c[0] in _BOXES) / len(cells) >= 0.6:
        return True
    if all(_GENERIC_HEAD.fullmatch(str(h)) for h in (t.get("headers") or []) if str(h).strip()) and sum(1 for c in cells if c[:1].islower()) >= 2:
        return True                                         # no real header, and cells that start in the middle of a sentence: a note cut into columns
    if cells and sum(1 for c in cells if len(c.split()) >= 4 and not re.search(r"\d", c)) / len(cells) >= 0.4:
        return True                                         # callout boxes and notes that the columns of the page cut into pieces
    body = [r for r in (t.get("rows") or [])[1:] if any(str(c).strip() for c in r)]
    return len(body) <= 1 and bool(cells) and not any(re.search(r"\d", c) for c in cells) and sum(1 for c in cells if c.endswith(":")) >= 2


def _label_box(t: Dict[str, Any]) -> bool:
    """A ruled box whose cells are mostly "LABEL:" and the value beside it (patient, specimen, dates): fields, whatever its first row looks like."""
    cells = [str(c).strip() for r in list(t.get("rows") or []) for c in r if str(c).strip()]
    labels_ = [c for c in cells if c.endswith(":") and len(c) > 2]
    return len(cells) >= 4 and len(labels_) >= 2 and len(labels_) / len(cells) >= 0.2 and len(_box_pairs(t)) >= 2


def _box_pairs(t: Dict[str, Any]) -> List[Tuple[str, str]]:
    """(label, value) pairs of a box whose cells read "LABEL:" then its value (and sometimes a second part, a date then a time)."""
    out: List[Tuple[str, str]] = []
    for r in t.get("rows") or []:
        cells = []
        for c in r:                                    # a cell that holds several parts ("Doe, John Q. | Age/Sex: | 73/M", ruled as one) is read part by part
            c = re.sub(r"\s*\(\d+\)$", "", str(c).strip())
            cells += [x.strip() for x in c.split(" | ")] if " | " in c else [c]
        i = 0
        while i < len(cells):
            if cells[i].endswith(":") and len(cells[i]) > 2:
                j, parts = i + 1, []
                while j < len(cells) and not cells[j].endswith(":"):
                    if cells[j] and not (parts and cells[j] == parts[-1]):
                        parts.append(cells[j])
                    j += 1
                if parts:
                    out.append((cells[i].rstrip(":").strip(), " ".join(parts)))
                i = j
            else:
                i += 1
    return out


def _cell(value: str, page: int) -> Dict[str, Any]:
    return {"value": value or None, "status": "agreed" if value else "empty", "alternatives": [], "method": "text-layer", "bbox": None, "page": page}


async def read_page(path: str, page_num: int, lines: List[str], tables: List[Dict[str, Any]], model: Optional[str] = None,
                    ask: Optional[Ask] = None, memo: Optional[Dict[str, List[str]]] = None,
                    facts: Optional[List[Dict[str, Any]]] = None, boxes: Optional[List[Dict[str, Any]]] = None,
                    form_fields: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Everything code can read from one digital page, in the same shape as discover.discover_page returns.
    `facts` and `boxes` are label/value pairs and check boxes the layout already found (see structure.py)."""
    memo = memo if memo is not None else {}
    out_tables: Dict[str, List[Dict[str, Any]]] = {}
    out_cols: Dict[str, List[str]] = {}
    title = None
    unverified: List[str] = []
    box_fields: Dict[str, Dict[str, Any]] = {}
    in_tables: set = set()                              # the text of every cell already read as part of a table
    for n, t in enumerate(tables, 1):
        if t.get("method") == "blank":                  # column labels over empty ruled lines: the columns are the template of the table
            key = labels.key(t.get("title") or f"{t['headers'][0]} table", f"table_{n}")
            out_tables[key if key not in out_tables else f"{key}_{n}"], out_cols[key if key not in out_cols else f"{key}_{n}"] = [], list(t["headers"])
            continue
        if _not_a_table(t):
            continue
        in_tables.update(locate.alnum(c) for r in t.get("rows", []) for c in r if c)
        if t.get("method") == "grid" and (t.get("header_rows") == 0 or _label_box(t)):          # a ruled box of "LABEL: value" cells (specimen, dates), above a table's header
            for label, value in _box_pairs(t):
                key = labels.key(label)
                if key and key not in box_fields:
                    box_fields[key] = {**_cell(value, page_num), "label": label, "section": None, "type": "text", "normalized": None, "bbox": locate.find(path, page_num, value, label)}
            continue
        t, cov, missing = await asyncio.to_thread(verified_table, path, page_num, t)
        header, body = split_header(t)
        body = [r for r in body if any(r)]
        ncols = max((len(r) for r in body), default=0)
        if not body or ncols < 2:
            continue
        cols = await asyncio.to_thread(geometric_labels, path, page_num, t["bbox"], header, ncols) or await header_labels(header, ncols, model, ask, memo)
        if ncols == 2 and all(_GENERIC_HEAD.fullmatch(c or "") for c in cols):      # a label and its amount, line after line: the page's pairs, not a table
            for r in body:
                if r[0].strip() and r[1].strip():
                    box_fields.setdefault(labels.key(r[0]) or f"field_{n}", {**_cell(r[1].strip(), page_num), "label": r[0].strip(), "section": None, "type": "text", "normalized": None, "bbox": locate.find(path, page_num, r[1].strip(), r[0].strip())})
            continue
        ttl = t["title"] if "title" in t else table_title(lines, body[0][0] if body[0] else "")      # a layout-found table names itself from the page
        if not ttl and len(body) >= 4 and body[0] and body[0][0].strip() and not any(c.strip() for c in body[0][1:]):
            ttl = body[0][0].strip().title() if body[0][0].isupper() else body[0][0].strip()         # its own first line, a section name (COMPLETE BLOOD COUNT)
            body = body[1:]
        title = title or ttl
        key = labels.key(ttl or "", f"table_{n}")
        key = key if key not in out_tables else f"{key}_{n}"
        keys: List[str] = []
        for j, c in enumerate(cols):
            k = labels.key(c, f"col_{j + 1}")
            keys.append(k if k not in keys else f"{k}_{j + 1}")
        out_tables[key], out_cols[key] = [], cols
        note = None
        if cov < COVERAGE_OK:                               # neither reading captured every number printed in this region
            note = f"{round((1 - cov) * 100)}% of the numbers in this table were not captured (for example {', '.join(missing[:3])})"
            unverified.append(note)
        padded = [r + [""] * (ncols - len(r)) for r in body]
        spots = iter(locate.in_order(path, page_num, t["bbox"], [c for r in padded for c in r]))
        for r in padded:
            row = {k: {**_cell(r[j], page_num), "label": cols[j], "bbox": next(spots)} for j, k in enumerate(keys)}
            if note:
                for c in row.values():
                    if c["value"]:
                        c.update({"status": "review", "reason": note})
            out_tables[key].append(row)
    fields: Dict[str, Dict[str, Any]] = dict(box_fields)
    lines = ["" if locate.alnum(l) in in_tables else l for l in lines]       # a table's own cells are not pairs of their own
    for p in kv.parse_kv(lines):
        label, value = p["label"], p["value"]
        if len(value) > MAX_VALUE_CHARS or len(lines[p["line"]]) > MAX_PAIR_LINE or re.search(r"\d", label) or _instruction(label, value):
            continue                                   # a sentence, or "Heading: running text": not a labelled value
        key = labels.key(label)
        if key and key not in fields:
            hit = locate.find_with_text(path, page_num, value, label)
            fields[key] = {**_cell(value, page_num), "label": label, "section": None, "type": "text", "normalized": None,
                           "bbox": hit[0] if hit else None}
    for f in facts or []:                              # pairs with no colon: a caption under a value, a label then an id or a date
        key = labels.key(f["label"])
        if key and key not in fields:
            fields[key] = {**_cell(f["value"], page_num), "label": f["label"], "section": None, "type": "text", "normalized": None, "bbox": f.get("bbox")}
    for f in form_fields or []:                        # a fillable form's own fields, named by the text beside them
        key = labels.key(f["label"], "field")
        same = fields.get(key)
        if same and (same.get("value") or "").strip() == (f["value"] or "").strip():
            same["bbox"] = same.get("bbox") or f["bbox"]                  # the table already gave this value: the field only says where it is
            continue
        base, k = key, 2
        while key in fields:
            key, k = f"{base}_{k}", k + 1
        fields[key] = {**_cell(f["value"], page_num), "label": f["label"], "section": None, "type": "text", "normalized": None, "bbox": f["bbox"]}
    taken: Dict[str, int] = {}
    for b in boxes or []:                              # a drawn tick or cross read from the text: exact, like every value on a digital page
        group = (b.get("group") or "").strip()
        base = _slug(" ".join(group.split()[:6])) if group else "checkboxes"
        key = f"{base}.{_slug(b['label'])}" if _slug(b["label"]) else ""
        if not key:
            continue
        taken[key] = taken.get(key, 0) + 1
        key = key if taken[key] == 1 else f"{key}_{taken[key]}"
        fields[key] = {"value": b["checked"], "status": "agreed", "alternatives": [], "method": "text-glyph", "bbox": b.get("bbox"), "page": page_num,
                       "label": b["label"], "section": group or None, "type": "checkbox", "normalized": None}
    if not tables and looks_tabular(lines):
        unverified.append("this page has rows of amounts but no table was detected")
    heading = page_title(lines)
    return {"title": title or heading, "heading": heading, "fields": fields, "tables": out_tables, "table_columns": out_cols,
            "readers": ["text-layer"], "unverified": unverified}


def as_ocr(result: Dict[str, Any], doubtful: bool) -> Dict[str, Any]:
    """A page read by code from a local OCR engine's layout, not from a PDF's own text: the same shape of result, but no value is
    exact, so each is marked as read once ("single"), or "review" when the engine was unsure of the page."""
    cells = list(result["fields"].values()) + [c for rows in result["tables"].values() for r in rows for c in r.values()]
    for c in cells:
        if c.get("status") == "empty":
            continue
        c["method"] = "ocr-local"
        if doubtful:
            c.update({"status": "review", "reason": "the local OCR engine was unsure of this page"})
        elif c.get("status") == "agreed":
            c["status"] = "single"
    result["readers"] = ["ocr-local"]
    return result


def looks_like_prose(lines: List[str]) -> bool:
    chars = sum(len(l) for l in lines) or 1
    long_chars = sum(len(l) for l in lines if len(l.split()) >= PROSE_WORDS_PER_LINE)
    return long_chars / chars > 0.6


def needs_model(lines: List[str], result: Dict[str, Any]) -> bool:
    """Only a form-like page that code could not make sense of: short label lines, no table, almost no "Label: value"."""
    if config.digital_model_mode() == "always":
        return True
    if config.digital_model_mode() == "never":
        return False
    if result.get("unverified"):                       # code could not prove it read the page completely
        return True
    structured = bool(result["tables"]) or len(result["fields"]) >= MIN_KV_FOR_STRUCTURE
    return not structured and len(lines) >= MIN_LINES_FOR_FORM and not looks_like_prose(lines)
