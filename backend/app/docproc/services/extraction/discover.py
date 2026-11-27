"""
Template-free extraction: read whatever a page holds, whatever kind of document it is.

Two vision models each list every labelled field, checkbox and table they can see (the printed label as written, the
value as written). The two lists are matched by label and merged with the same trust model as templated extraction:
agreed / review / single / empty. Checkboxes are judged from pixels where the box can be located; every value is
located on the page (Tesseract layer) and checked against Tesseract's own reading.

The result has the same shape as a templated form, so the review UI, JSON export, corrections and answer-key export
all work unchanged. Fields are keyed "<section>.<label>" in snake case.
"""
import asyncio
import json
import logging
import re
import time
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from app.docproc.services.extraction import adjudicate, checkboxes, digital, form_extractor as fx, labels, normalize as N
from app.docproc.services.reading import locate, ocr, pdf_pages

logger = logging.getLogger("app.services.discover")

TYPE_MAP = {"date": "date", "phone": "phone"}  # anything else compares as plain text
LABEL_MATCH = 0.86

PAGE_PROMPT = """You are extracting ALL the data on one page of a document, with no template. The page may be a form, a
letter, a statement, a note, an invoice or anything else.

Return JSON only:
{"title": "<the document's title as printed, or null>",
 "fields": [{"label": "<printed label>", "value": "<value as written, or null if the blank is empty>",
             "section": "<printed section heading it sits under, or null>",
             "type": "text|date|number|currency|phone|email|id"}],
 "checkboxes": [{"label": "<option text beside the box>", "group": "<question or heading it belongs to, or null>", "checked": true|false}],
 "tables": [{"title": "<caption or null>", "columns": ["<header>", ...], "rows": [["<cell>", ...], ...]}]}

Rules:
- Copy values exactly, digit for digit. If a character is unreadable write [?]. Never guess or complete.
- Keep a leading decimal point (.2% is not 2%), percent and currency signs, ranges (7-20), fractions and units exactly as written.
- Names and rare words: copy letter by letter, never swap in a common word. When a value is written both in digits and in words, give the digits.
- Each value belongs to ONE field. Never list the same value under two labels, and do not list a value again as a table cell.
- Some pages (handwritten notes, sketches, free-form records) have no printed labels: use the writer's own words as the label, short and in the same wording every time.
- A line written as "<name> - <amount>" (or "name: amount") is ONE field: the label is the name, the value is everything written after it, units and substance names included. Never leave the value null and put the whole line in the label.
- Lines under a label that have no label of their own (bullets, a second line, a list of items) belong to that label: give them as its value, in order, separated by "; ". Their heading line is the label, not a section.
- Faint or mirrored writing showing through from the other side of the paper is not part of this page.
- Include labelled blanks that are empty (value null): an empty field is information.
- A label is something printed that names a value ("Date of Birth", "Invoice No."). Do not turn sentences of running text into fields.
- A checkbox is checked only if the box contains a tick, cross or fill.
- Marks printed on the form are not values: a degree or minute sign, a unit, a currency sign, a frame. A blank that holds only such marks and nothing written is empty (null). A stamp (RECEIVED, a data-bank or archive stamp, a watermark) is not the value of the field it overlaps.
- A crossed-out entry is not the value: give what was written in its place, or null if nothing was.
- Letters and digits can look alike in handwriting. Decide from what the entry is: a place or country code is letters (N.Z.), an identifier or a quantity is digits. Do not read a letter as a digit, or the reverse, only because the shapes are similar.
- Put repeated structures (line items, rows of measurements) in "tables", every row on this page.
- In a table every row has exactly one cell per column, in the printed column order. A cell with nothing printed in it is "" : never leave it out and shift the next value left, and decide each value's column from where it is printed under the header, not from the order of the values.
- A column header printed over two or three lines is one column name ("Date of" over "Service" is "Date of Service"). Text that continues on the next line inside one cell is one cell, not another row. A row that spans the whole table (a section name such as COMPLETE BLOOD COUNT) is not a data row: use it as the table's title.
- Ignore fax header/footer stamps and page numbers.
"""

TEXT_NOTE = """

You are given the page as text instead of an image. Table rows are on one line with the cells separated by " | ".
Numbers in parentheses, such as (1,234), are negative: copy them exactly as written, with the parentheses.

PAGE TEXT:
"""

FOLLOW_NOTE = """

ANOTHER READER already looked at this page and listed the items below (labels only, no values). Read the value of each
item yourself, independently, from the page, using exactly the same label, section and table title/columns so the two
readings can be compared. Read every table row yourself. If the page holds something labelled that is NOT in the list,
add it as well. If an item below is not actually on the page, leave it out.

ITEMS ALREADY LISTED:
"""

BLANK_PROMPT = """This is a BLANK form (nothing filled in) from a PDF. List its structure so a template can be made.

Return JSON only:
{"title": "<the form's title as printed>",
 "fields": [{"label": "<printed label>", "section": "<printed section heading or null>",
             "type": "text|date|phone|number|currency|email|id|state|zip|tax_id|icd10", "required": true|false}],
 "choice_groups": [{"label": "<the question or heading>", "section": "<section or null>", "options": ["<option text>", ...],
                    "multiple": true|false, "required": true|false}],
 "tables": [{"title": "<caption or null>", "columns": [{"label": "<header>", "required": true|false}]}]}

Rules:
- Every blank to be filled in is a field, in reading order. Use the exact printed label without the * mark.
- "required" is true when the label carries a star, asterisk or the word "required".
- Rows of check boxes belong in choice_groups; "multiple" is true when more than one may be ticked.
- A grid with column headers and empty rows is a table.
- "section" is the nearest printed heading above the item (MEDICAL HISTORY, FOR WOMEN ONLY, ...). Use null only when the page has no heading above it.
- A blank that only makes sense after another question ("If yes, when?", "If so, how much?", "Dose:") is labelled with that question: "Flu Vaccine - if yes, when", "Alcohol use - if yes, amount". Never give two blanks the same label.
- The parts of one blank (Last / First / Middle Initial under LEGAL NAME) are fields named after it: "LEGAL NAME - Last", "LEGAL NAME - First", "LEGAL NAME - Middle Initial".
- A question with check boxes (Yes / No) is one choice group; do not list it again as a text field.
"""


def slug(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:48]


def _canon(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


async def _read(model: str, jpeg: Optional[bytes], prompt: str) -> Dict[str, Any]:
    """One model reads a page. `jpeg` None means a page read from its text (no image tokens)."""
    from app.docproc.engines.providers import llm
    return parse(await llm.complete(model, prompt, jpeg, stage="discover"))


def _s(v: Any) -> Optional[str]:
    if v is None:
        return None
    t = str(v).strip()
    return t or None


def parse(raw: Any) -> Dict[str, Any]:
    """Defensive parse of a model reply (or an already-parsed dict) into the shape the rest of this module uses."""
    if isinstance(raw, str):
        try:
            raw = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
        except ValueError:
            raw = {}
    d = raw if isinstance(raw, dict) else {}
    fields = [{"label": _s(f.get("label")), "value": _s(f.get("value")), "section": _s(f.get("section")),
               "type": str(f.get("type") or "text").lower(), "required": bool(f.get("required"))}
              for f in (d.get("fields") or []) if isinstance(f, dict) and _s(f.get("label"))]
    boxes = [{"label": _s(c.get("label")), "group": _s(c.get("group")), "checked": bool(c.get("checked"))}
             for c in (d.get("checkboxes") or []) if isinstance(c, dict) and _s(c.get("label"))]
    tables = []
    for t in d.get("tables") or []:
        if not isinstance(t, dict):
            continue
        cols = t.get("columns") or []
        cols = [(_s(c.get("label")) if isinstance(c, dict) else _s(c)) or "" for c in cols]
        if cols and not cols[0] and len(cols) > 1:
            cols[0] = "Item"                      # the unlabelled first column of a statement or grid holds the row labels
        rows = [[_s(c) for c in r] for r in (t.get("rows") or []) if isinstance(r, list)]
        if cols:
            tables.append({"title": _s(t.get("title")), "columns": cols, "rows": rows})
    groups = [{"label": _s(g.get("label")), "section": _s(g.get("section")), "options": [o for o in (_s(x) for x in (g.get("options") or [])) if o],
               "multiple": bool(g.get("multiple")), "required": bool(g.get("required"))}
              for g in (d.get("choice_groups") or []) if isinstance(g, dict) and _s(g.get("label"))]
    return {"title": _s(d.get("title")), "fields": fields, "checkboxes": boxes, "tables": tables, "choice_groups": groups}


def _unique(keys: List[str]) -> List[str]:
    seen: Dict[str, int] = {}
    out = []
    for k in keys:
        seen[k] = seen.get(k, 0) + 1
        out.append(k if seen[k] == 1 else f"{k}_{seen[k]}")
    return out


def _field_keys(items: List[Dict[str, Any]]) -> List[str]:
    """Canonical key per field (see labels.py). The section only joins the key when the same label occurs twice, so a
    key does not change just because a reader filed the field under a different heading."""
    base = [labels.key(i["label"]) for i in items]
    count = {k: base.count(k) for k in base}
    return _unique([(f"{slug(i.get('section'))}." if count[k] > 1 and slug(i.get("section")) else "") + k for k, i in zip(base, items)])


def _pair(a_items, a_keys, b_items, b_keys) -> List[Tuple[Optional[int], Optional[int]]]:
    """Match the second reader's items to the first's, in three passes:
       1. the same key (section and label),
       2. a close label in the same section,
       3. a very close label in ANY section (the readers often file a field under different headings),
       4. the same value under a different label (handwritten notes, where the labels are the writer's own words).
    Among several candidates the one at the nearest relative position on the page wins, so a repeated label ("Tax ID"
    in two blocks) pairs with its own twin."""
    used: set = set()
    match: Dict[int, int] = {}
    na, nb = max(1, len(a_items)), max(1, len(b_items))

    def sim(i, j):
        return SequenceMatcher(None, _canon(a_items[i]["label"]), _canon(b_items[j]["label"]), autojunk=False).ratio()

    def best(i, ok):
        cands = [(abs(i / na - j / nb), j) for j in range(len(b_items)) if j not in used and ok(j)]
        return min(cands)[1] if cands else None

    def same_value(i, j):
        """Handwritten notes have no fixed labels, so two readers name the same value differently ("Name" / "President").
        An identical, substantial value is the same field whatever each reader called it."""
        va, vb = a_items[i].get("value"), b_items[j].get("value")
        return bool(va and vb) and len(_canon(va)) >= 3 and N.normalize(va, "text") == N.normalize(vb, "text")

    passes = (lambda i, j: a_keys[i] == b_keys[j],
              lambda i, j: slug(a_items[i].get("section")) == slug(b_items[j].get("section")) and sim(i, j) >= LABEL_MATCH,
              lambda i, j: sim(i, j) >= 0.92,
              same_value)
    for ok in passes:
        for i in range(len(a_items)):
            if i in match:
                continue
            j = best(i, lambda j, i=i: ok(i, j))
            if j is not None:
                match[i] = j
                used.add(j)
    pairs = [(i, match.get(i)) for i in range(len(a_items))]
    pairs.extend((None, j) for j in range(len(b_items)) if j not in used)
    return pairs


def merge_fields(a: List[Dict[str, Any]], b: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    two = b is not None
    b = b or []
    ak, bk = _field_keys(a), _field_keys(b)
    out: Dict[str, Dict[str, Any]] = {}
    for i, j in _pair(a, ak, b, bk):
        fa, fb = (a[i] if i is not None else None), (b[j] if j is not None else None)
        src = fa or fb
        key = ak[i] if i is not None else bk[j]
        ftype = TYPE_MAP.get(src["type"], "text")
        cell = fx.merge_field(fa["value"] if fa else None, fb["value"] if fb else None, ftype, two)
        if two and (fa is None or fb is None):
            cell["value"] = (fa or fb)["value"]
            cell["alternatives"] = []
            if cell["value"] is None:
                cell["status"] = "empty"          # a blank the other reader did not list: nothing to check
            else:
                cell["status"] = "review"
                cell["reason"] = "only one reader found this field"
        cell.update({"label": src["label"], "section": src.get("section"), "type": src["type"], "normalized": N.normalize(cell["value"], ftype)})
        out[key] = cell
    return out


def _grid_index(tables: List[Dict[str, Any]]) -> Dict[str, Tuple[int, int, int, str]]:
    """'<row label> <column header>' (either order) -> (table, row, col, value), for tables whose first column labels the rows."""
    idx: Dict[str, Tuple[int, int, int, str]] = {}
    for ti, t in enumerate(tables):
        for ri, row in enumerate(t["rows"]):
            label = row[0] if row else None
            for ci, cell in enumerate(row):
                head = t["columns"][ci] if ci < len(t["columns"]) else ""
                if ci and label and head and cell is not None:
                    for k in (_canon(f"{label} {head}"), _canon(f"{head} {label}")):
                        idx[k] = (ti, ri, ci, cell)
    return idx


def reconcile(a: Dict[str, Any], b: Dict[str, Any]) -> Tuple[set, set]:
    """One reader may return a grid as a table and the other as flat fields ("Flexion Right: 170"). Same data, different
    shape. A flat field that equals a cell of the other reader's table confirms that cell and is dropped (its data lives
    in the table). Returns the confirmed (table, row, col) cells of a's tables and of b's tables."""
    conf_a: set = set()
    conf_b: set = set()

    def sweep(fields, other_index, confirmed):
        keep = []
        for f in fields:
            hit = other_index.get(_canon(f["label"]))
            if hit and _canon(f["value"] or "") == _canon(hit[3] or ""):
                confirmed.add(hit[:3])
            else:
                keep.append(f)
        fields[:] = keep

    sweep(a["fields"], _grid_index(b["tables"]), conf_b)
    sweep(b["fields"], _grid_index(a["tables"]), conf_a)
    for conf in (conf_a, conf_b):          # a row label is confirmed by any value in its row
        conf.update({(t, r, 0) for t, r, _ in list(conf)})
    return conf_a, conf_b


def _match_tables(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> List[Tuple[Optional[int], Optional[int]]]:
    used: set = set()
    pairs = []
    for i, ta in enumerate(a):
        pick = None
        for j, tb in enumerate(b):
            if j in used:
                continue
            same_title = bool(ta["title"]) and _canon(ta["title"]) == _canon(tb["title"] or "")
            head = SequenceMatcher(None, _canon(" ".join(ta["columns"])), _canon(" ".join(tb["columns"]))).ratio()
            if same_title or (len(ta["columns"]) == len(tb["columns"]) and head >= 0.8):
                pick = j
                break
        if pick is not None:
            used.add(pick)
        pairs.append((i, pick))
    pairs.extend((None, j) for j in range(len(b)) if j not in used)
    return pairs


def _row_key(row: List[Optional[str]]) -> str:
    return next((_canon(c) for c in row if c and _canon(c)), "")


def _is_heading(row: List[Optional[str]]) -> bool:
    """A row that is only a label ("Current assets:", "Revenue:"): nothing in it to check."""
    return bool(row) and bool(row[0]) and all(not c for c in row[1:])


def align_rows(ra: List[List[Optional[str]]], rb: List[List[Optional[str]]]) -> List[Tuple[Optional[int], Optional[int]]]:
    """Pair the two readers' rows by their row label, not by position. One reader listing a heading row the other skipped
    (or splitting a row in two) would otherwise shift every row below it and fill cells with the wrong row's numbers."""
    ka, kb = [_row_key(r) for r in ra], [_row_key(r) for r in rb]
    pairs: List[Tuple[Optional[int], Optional[int]]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, ka, kb, autojunk=False).get_opcodes():
        if tag == "equal":
            pairs.extend(zip(range(i1, i2), range(j1, j2)))
        elif tag == "delete":
            pairs.extend((i, None) for i in range(i1, i2))
        elif tag == "insert":
            pairs.extend((None, j) for j in range(j1, j2))
        else:                                     # replace: pair rows whose labels are close (a misread word), in order
            nxt = j1
            for i in range(i1, i2):
                best = max(((SequenceMatcher(None, ka[i], kb[j]).ratio(), j) for j in range(nxt, j2)), default=(0.0, -1))
                if best[0] >= 0.75:
                    pairs.extend((None, j) for j in range(nxt, best[1]))
                    pairs.append((i, best[1]))
                    nxt = best[1] + 1
                else:
                    pairs.append((i, None))
            pairs.extend((None, j) for j in range(nxt, j2))
    return pairs


def merge_tables(a: List[Dict[str, Any]], b: Optional[List[Dict[str, Any]]], conf_a: Any = frozenset(), conf_b: Any = frozenset()):
    two = b is not None
    b = b or []
    pairs = _match_tables(a, b)
    titles = [(a[i] if i is not None else b[j])["title"] for i, j in pairs]
    keys = _unique([labels.key(t or "", f"table_{n + 1}") for n, t in enumerate(titles)])
    tables: Dict[str, List[Dict[str, Any]]] = {}
    columns: Dict[str, List[str]] = {}
    for key, (i, j) in zip(keys, pairs):
        ta, tb = (a[i] if i is not None else None), (b[j] if j is not None else None)
        base = ta or tb
        base_conf = conf_a if ta else conf_b
        base_index = i if ta else j
        cols = _unique([labels.key(c or "", f"col_{x + 1}") for x, c in enumerate(base["columns"])])
        both = ta is not None and tb is not None and len(ta["columns"]) == len(tb["columns"])
        rows = []
        row_pairs = align_rows(ta["rows"], tb["rows"]) if both else [(r, None) for r in range(len(base["rows"]))]
        for ia, ib in row_pairs:
            ra = ta["rows"][ia] if both and ia is not None else None
            rb = tb["rows"][ib] if both and ib is not None else None
            src = ra if ra is not None else (rb if both else base["rows"][ia])
            row = {}
            for c, ckey in enumerate(cols):
                va = ra[c] if ra and c < len(ra) else None
                vb = rb[c] if rb and c < len(rb) else None
                if both:
                    cell = fx.merge_field(va, vb, "text", two)
                    if two and (ra is None or rb is None):
                        cell.update({"value": va if ra is not None else vb, "alternatives": []})
                        if _is_heading(src):                    # a heading one reader skipped: nothing to verify
                            cell["status"] = "agreed" if cell["value"] else "empty"
                            cell.pop("reason", None)
                        else:
                            cell.update({"status": "review" if cell["value"] else "empty", "reason": "only one reader saw this row"})
                else:
                    value = (src[c] if src and c < len(src) else None)
                    cell = {"value": value, "status": "single", "alternatives": []}
                    if two and (base_index, ia, c) in base_conf:
                        cell["status"] = "agreed"              # the other reader gave this cell as a flat field: same data
                    elif two and value is not None:
                        cell.update({"status": "review", "reason": "the readers did not find the same table"})
                cell["label"] = base["columns"][c]
                row[ckey] = cell
            rows.append(row)
        tables[key], columns[key] = rows, list(base["columns"])
    return tables, columns


def merge_checkboxes(path: str, page_num: int, a: List[Dict[str, Any]], b: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    two = b is not None
    b = b or []
    ak, bk = (_unique([(f"{slug(i['group'])}." if slug(i.get("group")) else "checkboxes.") + (slug(i["label"]) or "box") for i in items]) for items in (a, b))
    pairs = _pair([{**i, "section": i.get("group")} for i in a], ak, [{**i, "section": i.get("group")} for i in b], bk)
    labels = {}
    for i, j in pairs:
        src = a[i] if i is not None else b[j]
        labels[src["label"]] = (0.0, 1e9)
    try:
        px = checkboxes.pixel_states(path, page_num, labels) if labels else {}
    except Exception as e:
        logger.warning("pixel checkbox read failed: %s", e)
        px = {}
    out: Dict[str, Dict[str, Any]] = {}
    for i, j in pairs:
        ca, cb = (a[i] if i is not None else None), (b[j] if j is not None else None)
        src = ca or cb
        key = ak[i] if i is not None else bk[j]
        ma = ca["checked"] if ca else None
        mb = cb["checked"] if cb else None
        seen = px.get(src["label"])
        if seen is not None:
            models = [m for m in (ma, mb) if m is not None]
            agrees = all(m == seen for m in models)
            cell = {"value": seen, "status": "agreed" if agrees else "review", "method": "pixel", "alternatives": [] if agrees else [str(models[0] if models else "")],
                    **({} if agrees else {"reason": "the model reading differs from the pixels"})}
        else:
            cell = fx.model_checkbox("checkbox", ma, mb, two) or {"value": None, "status": "undetermined", "alternatives": [], "method": None}
            if two and (ca is None or cb is None):
                cell["status"], cell["reason"] = "review", "only one reader found this box"
        cell.update({"label": src["label"], "section": src.get("group"), "type": "checkbox"})
        out[key] = cell
    return out


def _tokens(label: Optional[str]) -> set:
    return set(re.findall(r"[a-z0-9]+", (label or "").lower()))


def prune_empty_noise(fields: Dict[str, Dict[str, Any]], title: Optional[str]) -> int:
    """Models sometimes list the page title, or a whole sentence of running text, as an empty "blank". A blank is a short
    printed label with nothing written after it; neither of those is one. Filled fields are never touched."""
    drop = [k for k, c in fields.items() if c.get("status") == "empty" and c.get("type") != "checkbox"
            and ((title and _canon(c.get("label") or "") == _canon(title)) or len(re.findall(r"\S+", c.get("label") or "")) >= 7)]
    for k in drop:
        del fields[k]
    return len(drop)


def dedupe(fields: Dict[str, Dict[str, Any]], tables: Dict[str, List[Dict[str, Any]]]) -> int:
    """Drop repeats of one value that discovery lists twice, the way handwritten pages tend to produce them.

    A field repeats another when it has the same value and either the same place on the page or labels that are one
    inside the other ("Corporation" / "Corporation Name", "Day" / "Date - day"). A flat field whose long text is already
    a table cell is the table's data in another shape. The better-supported cell is kept. Returns how many were dropped."""
    rank = lambda c: fx._RANK.get(c.get("status"), 4)
    keys = [k for k, c in fields.items() if c.get("type") != "checkbox" and isinstance(c.get("value"), str)
            and len(_canon(c["value"])) >= 4 and c.get("status") not in ("empty", "corrected")]
    drop: set = set()
    for x, a in enumerate(keys):
        for b in keys[x + 1:]:
            if a in drop or b in drop:
                continue
            ca, cb = fields[a], fields[b]
            if N.normalize(ca["value"], "text") != N.normalize(cb["value"], "text"):
                continue
            ta, tb = _tokens(ca.get("label")), _tokens(cb.get("label"))
            same_place = bool(ca.get("bbox")) and ca.get("bbox") == cb.get("bbox")
            nested = bool(ta and tb) and (ta <= tb or tb <= ta)
            if same_place or nested:
                drop.add(b if rank(cb) >= rank(ca) else a)
    cells = {N.normalize(c.get("value"), "text") or "" for rows in tables.values() for r in rows for c in r.values()
             if isinstance(c.get("value"), str)}
    for k in keys:
        v = N.normalize(fields[k]["value"], "text") or ""
        if k not in drop and len(v) >= 12 and any(v in cell for cell in cells):
            drop.add(k)
    for k in drop:
        del fields[k]
    return len(drop)


def _hint(a: Dict[str, Any]) -> str:
    """What the first reader found, as labels only. The second reader reads values against these labels, so the two
    readings pair exactly instead of being matched afterwards by guesswork; values are left out so it stays independent."""
    lines = []
    for f in a["fields"]:
        lines.append(f'- field "{f["label"]}"' + (f' (section "{f["section"]}")' if f["section"] else ""))
    for c in a["checkboxes"]:
        lines.append(f'- checkbox "{c["label"]}"' + (f' (group "{c["group"]}")' if c["group"] else ""))
    for t in a["tables"]:
        lines.append(f'- table "{t["title"] or "untitled"}" with columns {json.dumps(t["columns"], ensure_ascii=False)}')
    return "\n".join(lines) or "(nothing was found)"


async def read_page_twice(models: List[str], jpeg: Optional[bytes], page_text: Optional[str], reader) -> List[Dict[str, Any]]:
    """Reader one proposes what is on the page; reader two reads the same items independently and adds what was missed.
    Sequential on purpose: the second needs the first's labels. A failed first reader hands the lead to the second."""
    replies: List[Dict[str, Any]] = []
    for m in models[:2]:
        prompt = PAGE_PROMPT + (FOLLOW_NOTE + _hint(replies[0]) if replies else "") + (TEXT_NOTE + page_text if page_text is not None else "")
        try:
            got = parse(await asyncio.wait_for(reader(m, jpeg, prompt), ocr.ENGINE_TIMEOUT_SECONDS + 5))
            got["model"] = m                       # who actually read it: a failed reader must not be credited
            replies.append(got)
        except Exception as e:
            logger.warning("Discovery reader %s failed: %s", m, e)
    return replies


MIN_TABLE_NUMBERS = 4      # fewer amounts than this in rows of figures is not enough to say a table lost any
LAYER_TRUSTED = 0.6       # the layer must confirm at least this share of the readers' figures before it is allowed to say one is missing
COMPLETE_OK = 0.95         # a little below the digital bar: the numbers come from an OCR layer, which misreads a few


def rescue_tables(path: str, page_num: int, tables: Dict[str, List[Dict[str, Any]]], columns: Dict[str, List[str]]) -> int:
    """A table the page rules off, whose rows the readers left out (a model gives up after a few rows of a long table): the table measured from
    the rules and read from the page's words replaces the incomplete one. Every cell is marked for a person to check, because it is
    one reading from OCR or from the text layer and no model has seen it. Returns how many tables were replaced or added."""
    from collections import Counter
    try:
        info = pdf_pages.analyze_page(path, page_num)
    except Exception:
        return 0
    n = 0
    for t in info.get("tables", []):
        if t.get("method") != "grid" or not t.get("bbox"):
            continue
        lt = digital.labelled_table(path, page_num, t)
        if not lt or len(lt["rows"]) < RESCUE_MIN_ROWS:
            continue
        want = Counter(locate.alnum(c) for r in lt["rows"] for c in r if c.strip())
        best, shared = None, 0
        for k, rows in tables.items():
            have = Counter(locate.alnum(c.get("value")) for r in rows for c in r.values() if c.get("value"))
            ov = sum(min(v, have[x]) for x, v in want.items())
            if ov > shared:
                best, shared = k, ov
        if best is not None and len(tables[best]) >= RESCUE_KEEP * len(lt["rows"]):
            continue                                                  # the readers got most of it
        keys: List[str] = []
        for j, c in enumerate(lt["columns"]):
            k = labels.key(c, f"col_{j + 1}")
            keys.append(k if k not in keys else f"{k}_{j + 1}")
        why = f"the readers gave {len(tables[best]) if best else 0} of this table's {len(lt['rows'])} rows; this one is read from its printed rules"
        rows_out = [{k: {"value": (r[j].strip() or None), "status": "review" if r[j].strip() else "empty", "alternatives": [], "method": "ocr-local",
                         "label": lt["columns"][j], "reason": why if r[j].strip() else None, "bbox": None, "page": page_num}
                     for j, k in enumerate(keys)} for r in lt["rows"]]
        key = best or f"table_{len(tables) + 1}"
        tables[key], columns[key] = rows_out, list(lt["columns"])
        n += 1
    return n


def uncaptured(path: str, page_num: int, fields: Dict[str, Dict[str, Any]], tables: Dict[str, List[Dict[str, Any]]]) -> Optional[str]:
    """A note when amounts printed in rows of figures on the page are in none of the extracted cells: a row or a column a reader
    dropped. Two readers can both skip the same row, so agreement does not prove a table is whole; the page's own text layer
    (Tesseract's, for a scan) is the check. None when the page accounts for its figures."""
    try:
        text = pdf_pages.layout_text(path, page_num)
    except Exception:
        return None
    # figures only: "70,066", "(1,234)", "12.5%", "±3". A digit run inside a part number or a code (LM358B, 5.3, V+) is not an amount.
    figure = re.compile(r"[($±+\u2013-]*\d[\d,]*(?:\.\d+)?%?\)?")
    tokens: List[str] = []
    for line in text.splitlines():
        nums = [t for t in line.replace("|", " ").split() if figure.fullmatch(t)]
        if len(nums) >= 2 and re.search(r"[A-Za-z]{3}", line):
            tokens += nums
    all_printed = digital._numbers(tokens)
    printed = type(all_printed)({k: v for k, v in all_printed.items() if len(k) >= 3 or "." in k})          # not footnote marks and small counters
    if sum(printed.values()) < MIN_TABLE_NUMBERS:
        return None
    cells = [c.get("value") for c in fields.values()] + [c.get("value") for rs in tables.values() for r in rs for c in r.values()]
    got = digital._numbers([v for v in cells if isinstance(v, str)])
    found = type(got)({k: v for k, v in got.items() if len(k) >= 3 or "." in k})
    if found and sum((found & printed).values()) / sum(found.values()) < LAYER_TRUSTED:
        return None                       # the text layer confirms little of what the readers found: it is the poor one here (a photo), so it cannot judge them
    missing = printed - got
    lost = sum(missing.values())
    share = 1 - lost / sum(printed.values())
    if share >= COMPLETE_OK:
        return None
    return f"page {page_num}: {lost} of {sum(printed.values())} amounts printed in rows were not captured (for example {', '.join(sorted(missing.elements())[:4])}); a row or column may be missing"


async def discover_page(path: str, page_num: int, models: List[str], reader=None, handwritten: bool = False,
                        adjudicator=None, page_text: Optional[str] = None) -> Dict[str, Any]:
    """One page, read with no template. `page_text` set means a digital page: it is read from its text (a fraction of the
    cost of an image, and exact), and there is no image to show an adjudicator. Otherwise the page image is read."""
    injected = reader is not None          # a caller-supplied reader (tests) means no real model calls, so no real third look either
    reader = reader or _read
    jpeg = None
    replies: List[Dict[str, Any]] = []
    if models:
        if page_text is None:
            jpeg = await asyncio.to_thread(pdf_pages.render_jpeg, path, page_num)
        replies = await read_page_twice(models, jpeg, page_text, reader)
    two = len(replies) > 1
    a = replies[0] if replies else parse({})
    b = replies[1] if two else None
    conf_a, conf_b = reconcile(a, b) if b else (set(), set())
    fields = merge_fields(a["fields"], b["fields"] if b else None)
    boxes = await asyncio.to_thread(merge_checkboxes, path, page_num, a["checkboxes"], b["checkboxes"] if b else None)
    fields.update({k: v for k, v in boxes.items() if k not in fields})
    tables, columns = merge_tables(a["tables"], b["tables"] if b else None, conf_a, conf_b)
    await asyncio.to_thread(rescue_tables, path, page_num, tables, columns)

    page_alnum = await asyncio.to_thread(locate.page_alnum, path, page_num)

    if two and jpeg is not None and (adjudicator is not None or not injected):
        # one focused extra call for this page, only because the readers left something in dispute
        items = [(f"f:{k}", c, TYPE_MAP.get(c.get("type"), "text")) for k, c in fields.items()]
        items += [(f"t:{k}:{r}:{ck}", c, "text") for k, rows in tables.items() for r, row in enumerate(rows) for ck, c in row.items()]
        await adjudicate.settle(jpeg, items, adjudicator, readers=models[:2])

    def ground():
        # a table's cells are found in reading order, so the fifth "0.00" is the fifth one on the page and not the first
        tcells = [c for rows in tables.values() for r in rows for c in r.values()]
        placed = {}
        try:
            spots = locate.in_order(path, page_num, [0.0, 0.0, 1e5, 1e5], [str(c.get("value") or "") for c in tcells]) if tcells else []
            placed = {id(c): s for c, s in zip(tcells, spots) if s}
        except Exception:
            placed = {}
        for cell in list(fields.values()) + tcells:
            v = cell.get("value")
            if isinstance(v, str) and v:
                hit = locate.find_with_text(path, page_num, v, cell.get("label"))
                if id(cell) in placed:
                    hit = (placed[id(cell)], hit[1] if hit else v, hit[2] if hit else 1.0)
                cell["bbox"] = hit[0] if hit else None
                if hit is None and cell.get("label") and not cell.get("bbox"):
                    near = locate.beside(path, page_num, str(cell["label"]))        # not in the page's text (handwriting): the strip beside its label
                    if near:
                        cell["bbox"], cell["bbox_from"] = near, "label"
                if not handwritten:                  # Tesseract cannot read handwriting: its "second opinion" would only add noise
                    fx._third_opinion(cell, hit)
            elif cell.get("method") == "pixel" or isinstance(v, bool):
                hit = locate.find(path, page_num, cell.get("label") or "")
                cell["bbox"] = hit
            if two and cell.get("status") == "review" and not handwritten:
                fx._prefer_page_text(cell, "text", page_alnum)
    await asyncio.to_thread(ground)
    for cell in list(fields.values()) + [c for rows in tables.values() for r in rows for c in r.values()]:
        cell.setdefault("bbox", None)
        cell["page"] = page_num
    prune_empty_noise(fields, a["title"])
    dedupe(fields, tables)
    note = await asyncio.to_thread(uncaptured, path, page_num, fields, tables) if replies else None
    return {"title": a["title"], "fields": fields, "tables": tables, "table_columns": columns,
            "readers": [r["model"] for r in replies], "unverified": [note] if note else []}


RESCUE_MIN_ROWS = 6      # a ruled table at least this long is worth comparing with what the readers returned
RESCUE_KEEP = 0.7        # ...and is used instead when they returned fewer than this share of its rows
DISCOVER_CONCURRENCY = 4  # pages read at once; a 60-page document must not fire 60 model calls together


async def discover_document(path: str, pages: List[int], models: List[str], reader=None, handwritten: Any = (),
                            adjudicator=None, text_pages: Optional[Dict[int, str]] = None,
                            digital_pages: Optional[Dict[int, Dict[str, Any]]] = None, header_ask=None) -> Dict[str, Any]:
    """All pages of one untemplated document, merged.

    `digital_pages` maps born-digital pages to {"lines", "tables"}: those are read by code (digital.py) at no model cost,
    and a model reads one only when the page is form-like and code found nothing in it. `text_pages` maps pages to their
    text for the model path (read without images).

    A long digital document is read once per page by the primary model only (a second reader on every page of a
    hundred-page filing costs more than it is worth); those values are "single", not "review"."""
    from app.docproc import config
    text_pages = dict(text_pages or {})
    digital_pages = digital_pages or {}
    long_doc = len(pages) > config.long_doc_pages()
    sem = asyncio.Semaphore(DISCOVER_CONCURRENCY)
    memo: Dict[str, List[str]] = {}
    ask = header_ask if header_ask is not None else (None if reader is not None else digital.default_ask)
    small = models[-1] if models else None            # labelling a header is a short, easy call: the smaller model

    async def one(n):
        async with sem:
            if n in digital_pages:
                d = digital_pages[n]
                got = await digital.read_page(path, n, d["lines"], d["tables"], small, ask, memo, d.get("facts"), d.get("boxes"), d.get("form_fields"))
                if d.get("ocr"):
                    got = digital.as_ocr(got, d["ocr"] == "doubtful")
                if not (models and digital.needs_model(d["lines"], got)):
                    return got                      # code read it exactly: no model call for this page
                text_pages[n] = await asyncio.to_thread(pdf_pages.layout_text, path, n)
            use = models[:1] if (long_doc and n in text_pages) else models
            return await discover_page(path, n, use, reader, n in handwritten, adjudicator, text_pages.get(n))
    results = await asyncio.gather(*[one(n) for n in pages])
    fields: Dict[str, Dict[str, Any]] = {}
    tables: Dict[str, List[Dict[str, Any]]] = {}
    columns: Dict[str, List[str]] = {}
    for res in results:
        for k, cell in res["fields"].items():
            have = fields.get(k)
            if have is None:
                fields[k] = cell
            elif not have.get("value") or not cell.get("value") or N.normalize(have["value"], "text") == N.normalize(cell["value"], "text"):
                if fx._RANK.get(cell["status"], 4) < fx._RANK.get(have["status"], 4):
                    fields[k] = cell                  # the same value, or one is blank: keep the better-supported reading
            else:
                fields[f"{k}_p{cell['page']}"] = cell  # a different value on another page is more data (e.g. "Total" per statement), not a rival
        for k, rows in res["tables"].items():
            same = k in tables and len(columns[k]) == len(res["table_columns"][k])
            key = k if (k not in tables or same) else f"{k}_p{rows[0][next(iter(rows[0]))]['page']}" if rows else k
            tables.setdefault(key, []).extend(rows)
            columns.setdefault(key, res["table_columns"][k])
    title = next((r["title"] for r in results if r["title"]), None)
    if len(pages) > 3 and results and results[0].get("heading"):
        title = results[0]["heading"]          # a long document is named by its first page, not by whichever table came first
    return {"title": title, "fields": fields, "tables": tables, "table_columns": columns,
            "readers": results[0]["readers"] if results else [], "unverified": [n for r in results for n in r.get("unverified", [])]}
