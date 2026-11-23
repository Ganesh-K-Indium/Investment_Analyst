"""
Fill a template's text fields from a page's OCR text, with no model.

Used when a page is read locally (no vision model in the plan, or the models failed): the template says which labels
to look for, the OCR text says what follows them. Returns the same shape a model reply has, so merging, grounding on the
page and review flags work unchanged. Check boxes are not handled here; they are judged from the page's pixels.

A label is looked for as printed, tolerant of what OCR does to it: missing spaces, a stray apostrophe or asterisk, a
trailing colon. Within a section only (a label such as "First Name" repeats across sections), and a value must look like
its field's type (a date field takes a date), so a wrong guess becomes an empty field to review, not a wrong value.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

COLUMN_SEP = " | "
_DATE = re.compile(r"\b\d{1,2}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*(?:\d{4}|\d{2})\b")
_PHONE = re.compile(r"\(?\d{3}\)?[\s.\-]*\d{3}[\s.\-]*\d{4}")
_ZIP = re.compile(r"\b\d{5}(?:-\d{4})?\b")
_STATE = re.compile(r"\b[A-Z]{2}\b")
_DIGITS = {"npi": 10, "tax_id": 9}
_ICD = re.compile(r"\b[A-TV-Z]\d{2}(?:\.?[A-Z0-9]{1,4})?\b")
_GAP = r"[\s/\-()]*"        # between the words of a label: spaces may be missing, a slash or bracket may sit there
_CHECKBOX = ("enum", "multi_enum", "checkbox")


def _label_pattern(label: str) -> Optional["re.Pattern[str]"]:
    """The printed label as a pattern. Anything in parentheses is a hint of ours ("Surgery (date and type)"), not print."""
    base = re.sub(r"\([^)]*[A-Za-z][^)]*\)", " ", label)     # "(date and type)" is a hint; "(1)" is part of the print
    words = re.findall(r"[A-Za-z0-9]+", base)
    if not words:
        return None
    body = _GAP.join(re.escape(w) for w in words)
    return re.compile(r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z])[\s*#'’`.:;\-]*", re.I)


def _windows(lines: List[str], sections: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Each headed section's own stretch of lines: from its printed header to the next header's."""
    found: List[Tuple[int, str]] = []
    for sec in sections:
        if not sec.get("header"):
            continue
        words = re.findall(r"[A-Za-z0-9]+", sec["header"])
        pat = re.compile(_GAP.join(re.escape(w) for w in words), re.I)
        for i, line in enumerate(lines):
            if pat.search(line):
                found.append((i, sec["id"]))
                break
    found.sort()
    out: Dict[str, List[str]] = {}
    for n, (i, sid) in enumerate(found):
        end = found[n + 1][0] if n + 1 < len(found) else len(lines)
        out[sid] = lines[i:end]
    return out


def _fit(value: str, ftype: str) -> Optional[str]:
    """The part of `value` that suits the field's type, else None."""
    value = value.strip(" |:;*'’`\t")
    if not value:
        return None
    if ftype == "date":
        m = _DATE.search(value)
        return re.sub(r"\s+", "", m.group(0)) if m else None
    if ftype == "phone":
        m = _PHONE.search(value)
        return m.group(0).strip() if m else None
    if ftype == "zip":
        m = _ZIP.search(value)
        return m.group(0) if m else None
    if ftype == "state":
        m = _STATE.search(value)
        return m.group(0) if m else None
    if ftype == "icd10":
        m = _ICD.search(value)
        return m.group(0) if m else None
    if ftype in _DIGITS:
        digits = re.sub(r"\D", "", value)
        return digits if len(digits) == _DIGITS[ftype] else None
    return re.sub(r"^\([^)]{1,24}\)\s*", "", value).strip() or None      # drop a printed hint such as "(Date/Type)" before the answer


def _wrapped(next_line: str, wanted) -> str:
    """A long value wraps onto the next line. That line continues it when it starts in lower case, carries no label of
    ours, and has nothing beside it in another column."""
    first = next_line.split(COLUMN_SEP)
    cell = first[0].strip()
    if len(first) > 1 or not cell or not cell[0].islower() or any(p.search(cell) for _, p in wanted):
        return ""
    return " " + cell


def _flat(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _column_name(label: str) -> str:
    return _flat(re.sub(r"\([^)]*\)", " ", label))


def _header_map(cells: List[str], columns: List[Dict[str, Any]]) -> Dict[int, str]:
    """Which cell of a line is which column, by the printed column names. Empty unless at least two columns are found."""
    names = [(c["key"], _column_name(c["label"])) for c in columns]
    got: Dict[int, str] = {}
    for i, cell in enumerate(cells):
        flat = _flat(cell)
        best = None
        for key, name in names:
            if len(name) >= 2 and key not in got.values() and (name in flat or (len(flat) >= 4 and flat in name)):
                if best is None or len(name) > len(best[1]):
                    best = (key, name)
        if best:
            got[i] = best[0]
    return got if len(got) >= min(2, len(columns)) else {}


def _fits(value: str, col: Dict[str, Any]) -> bool:
    pat = col.get("pattern")
    return bool(re.fullmatch(pat, value.strip().upper())) if pat else True


def read_table(lines: List[str], table: Dict[str, Any]) -> List[Dict[str, str]]:
    """The rows of one template table from a page's OCR lines, which already hold one row per line with its cells
    joined by " | ". With the printed header found, cells map to columns by name; without one, the column that has a
    pattern (a CPT code) anchors each row and the other columns fill in around it in order. Only the first block is
    read, and a row whose pattern column does not fit is not a row."""
    columns = table["columns"]
    limit = int(table.get("max_rows") or 50)
    keys = [c["key"] for c in columns]
    anchor = next((c for c in columns if c.get("pattern")), None)
    start, mapping = 0, {}
    if table.get("header"):                                     # the printed section title, when the template names one
        words = re.findall(r"[A-Za-z0-9]+", table["header"])
        pat = re.compile(_GAP.join(re.escape(w) for w in words), re.I)
        start = next((i for i, l in enumerate(lines) if pat.search(l)), 0)
    hdr, hdr_width = None, 0
    for i in range(start, len(lines)):
        cells = [c.strip() for c in lines[i].split(COLUMN_SEP)]
        m = _header_map(cells, columns)
        if m:
            hdr, mapping, hdr_width = i, m, len(cells)
            break
    rows: List[Dict[str, str]] = []
    for i in range(hdr + 1 if hdr is not None else start, len(lines)):
        cells = [c.strip() for c in lines[i].split(COLUMN_SEP)]
        if _header_map(cells, columns):
            if rows:
                break                                           # a second copy of the table starts: the first block is the table
            continue
        row: Dict[str, str] = {}
        if mapping and max(mapping) < len(cells) and len(cells) == hdr_width:
            for idx, key in mapping.items():
                if cells[idx]:
                    row[key] = cells[idx]
        elif anchor is not None:
            at = next((j for j, c in enumerate(cells) if _fits(c, anchor)), None)
            if at is not None:
                order = keys.index(anchor["key"])
                before, after = cells[:at], cells[at + 1:]
                row[anchor["key"]] = cells[at]
                for k, v in zip(reversed(keys[:order]), reversed(before)):
                    if v:
                        row[k] = v
                for k, v in zip(keys[order + 1:], after):
                    if v:
                        row[k] = v
        if anchor is not None and not _fits(row.get(anchor["key"], ""), anchor):
            row = {}
        if len(row) < 2 or (anchor is None and len(cells) < 2):
            if rows:
                break                                           # the table has ended
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def read_fields(text: str, template: Dict[str, Any]) -> Dict[str, Any]:
    """{"fields": {key: value}, "table_rows": [...]} for one page's text; fields it cannot find are left out."""
    lines = [l for l in (text or "").splitlines() if l.strip()]
    sections = template.get("sections", [])
    windows = _windows(lines, sections)
    fields: Dict[str, str] = {}
    for sec in sections:
        wanted = [(f, _label_pattern(f["label"])) for f in sec["fields"] if f["type"] not in _CHECKBOX]
        wanted = [(f, p) for f, p in wanted if p is not None]
        if not wanted:
            continue
        if sec.get("header") and sec["id"] not in windows:
            continue                                  # its header was not found: a value from elsewhere would be a guess, so leave it blank
        stretch = windows.get(sec["id"], lines)       # a section with no printed header may use the whole page, first match wins
        for li, line in enumerate(stretch):
            for ci, cell in enumerate(line.split(COLUMN_SEP)):
                hits = [(m.start(), m.end(), f) for f, pat in wanted for m in pat.finditer(cell)]
                hits.sort(key=lambda h: (-(h[1] - h[0]), h[0]))          # a longer label wins where two overlap
                taken: List[Tuple[int, int, Dict[str, Any]]] = []
                for h in hits:
                    if not any(h[0] < t[1] and t[0] < h[1] for t in taken):
                        taken.append(h)
                taken.sort(key=lambda h: h[0])
                for i, (_, e, f) in enumerate(taken):
                    if f["key"] in fields:
                        continue
                    end = taken[i + 1][0] if i + 1 < len(taken) else len(cell)
                    got = _fit(cell[e:end], f["type"])
                    if got and f["type"] == "text" and ci == 0 and end == len(cell) and li + 1 < len(stretch):
                        got += _wrapped(stretch[li + 1], wanted)
                    if got:
                        fields[f["key"]] = got
    tables = template.get("tables") or []
    return {"fields": fields, "table_rows": read_table(lines, tables[0]) if tables else []}
