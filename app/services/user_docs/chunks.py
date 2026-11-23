"""
Extraction result -> searchable chunks, and the review items that decide when a page may be indexed.

Pure functions (no Qdrant, no database): the manager calls them, tests exercise them directly.

Text comes from the page's layout blocks (headings, paragraphs, label/value groups, lists), tables from the reviewed form tables when
the engine built them (cell-level confidence and corrections) and from the layout tables otherwise. Each table chunk starts with its
caption and the units the page states ("in thousands"), because a number without its unit is the most common way a financial answer
goes wrong. Every chunk carries the page and the block's position, so an answer's citation can open the page and highlight the passage.
"""
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from rag.verify.numbers import chunk_unit

_ID_NAMESPACE = uuid.UUID("c0a3b3f2-62a9-4a0e-8d1c-3f55d0a7e911")
TABLE_PREFIX = "[TABLES]\n"
_UNIT_NAME = {1e3: "thousands", 1e6: "millions", 1e9: "billions"}
_CURRENCY = [(re.compile(r"\bUSD\b|US\$|U\.S\. dollars|\$"), "USD"), (re.compile(r"\bEUR\b|€"), "EUR"), (re.compile(r"\bGBP\b|£"), "GBP"),
             (re.compile(r"\bJPY\b|¥"), "JPY"), (re.compile(r"\bRMB\b|\bCNY\b"), "CNY"), (re.compile(r"\bINR\b|₹"), "INR"), (re.compile(r"\bCHF\b"), "CHF")]
_PERIOD = [re.compile(r"\b(?:FY|fiscal(?: year)?)\s*'?(20\d{2})\b", re.I), re.compile(r"\bQ([1-4])\s*'?(20\d{2})\b", re.I),
           re.compile(r"\b(?:year|quarter|period)s? ended\s+([A-Z][a-z]+ \d{1,2}, 20\d{2})", re.I)]


def point_id(doc_id: str, page: int, index: int) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, f"{doc_id}:{page}:{index}"))


def _ingestion():
    """The ingestion package's splitter and table splitter (it is a script folder, not a package)."""
    root = str(Path(__file__).resolve().parents[3] / "ingestion")
    if root not in sys.path:
        sys.path.insert(0, root)
    import chunking
    return chunking


def detect_units(text: str) -> Optional[str]:
    u = chunk_unit(text)
    return _UNIT_NAME.get(u)


def detect_currency(text: str) -> Optional[str]:
    for rx, code in _CURRENCY:
        if rx.search(text or ""):
            return code
    return None


def detect_period(text: str) -> Optional[str]:
    """The first fiscal period a page states ("FY2025", "Q3 2025", "year ended December 31, 2025"), or None."""
    for rx in _PERIOD:
        m = rx.search(text or "")
        if m:
            if rx is _PERIOD[0]:
                return f"FY{m.group(1)}"
            if rx is _PERIOD[1]:
                return f"Q{m.group(1)} {m.group(2)}"
            return m.group(1)
    return None


# ---------------------------------------------------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------------------------------------------------

def _forms_by_doc(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    return result.get("forms") or []


def review_items(result: Dict[str, Any], corrections: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Everything a person may need to look at: OCR lines the engine was unsure of, and form/table values the two readers did not
    agree on. Each has a stable id so a correction survives a re-read of the same file."""
    items: List[Dict[str, Any]] = []
    for p in result.get("pages", []):
        for i, ln in enumerate(p.get("lines") or []):
            if ln.get("status") in ("review", "undetermined"):
                items.append({"id": f"p{p['page']}:l{i}", "kind": "line", "page": p["page"], "value": ln["text"],
                              "alternatives": ln.get("alternatives") or [], "reason": ln.get("reason")})
    for f in _forms_by_doc(result):
        d = f["doc_index"]

        def cell(cid: str, kind: str, label: str, c: Dict[str, Any]):
            if c.get("status") in ("review", "undetermined"):
                items.append({"id": cid, "kind": kind, "page": c.get("page") or (f.get("pages") or [None])[0], "label": label,
                              "value": c.get("value"), "alternatives": c.get("alternatives") or [], "reason": c.get("reason"), "doc_index": d})

        for k, c in (f.get("fields") or {}).items():
            cell(f"f{d}:{k}", "field", c.get("label") or k, c)
        for tname, rows in (f.get("tables") or {}).items():
            for ri, row in enumerate(rows):
                for col, c in row.items():
                    cell(f"f{d}:{tname}[{ri}].{col}", "cell", f"{tname} row {ri + 1}, {c.get('label') or col}", c)
    items.extend(arithmetic_items(result, corrections or {}))
    return items


SCANNED_SOURCES = ("ocr", "ocr-local", "text-layer-unverified")
_RULE_TITLE = {"total_does_not_add_up": "A total does not add up", "balance_sheet_does_not_balance": "The balance sheet does not balance",
               "gross_profit_does_not_match": "Gross profit does not match", "operating_income_does_not_match": "Operating income does not match",
               "cash_does_not_reconcile": "Cash does not reconcile", "unreadable_figure": "A figure is not a valid number"}


def arithmetic_items(result: Dict[str, Any], corrections: Dict[str, Any]) -> List[Dict[str, Any]]:
    """On SCANNED pages, a table whose arithmetic fails most likely has a misread digit: a statement's totals repeat its lines, so OCR cannot
    drift without the sums noticing. One "check" item states the problem; the cells involved are offered for correction. Computed on the
    corrected values, so once the figures are fixed the items disappear. (Digital and native tables are exact copies of the source, so a
    mismatch there is the document's own and is only noted in the passage.)"""
    out: List[Dict[str, Any]] = []
    seen = set()
    for p in result.get("pages", []):
        if p.get("source") not in SCANNED_SOURCES:
            continue
        for fin in finance_for_page(result, p["page"], corrections):
            t = fin["table"]
            for f in fin["findings"]:
                out.append({"id": f"k{p['page']}.{fin['index']}:{f['rule']}:{f['row']}:{f['col']}", "kind": "check", "page": p["page"], "label": _RULE_TITLE.get(f["rule"], "Arithmetic check"),
                            "value": "", "alternatives": [], "reason": f["detail"]})
                for r, j in f["cells"]:
                    cid = t["cell_ids"][r][j + 1] if r < len(t["cell_ids"]) and j + 1 < len(t["cell_ids"][r]) else None
                    if cid and cid not in seen:
                        seen.add(cid)
                        col = t["columns"][j + 1] if j + 1 < len(t["columns"]) else f"column {j + 2}"
                        out.append({"id": cid, "kind": "cell", "page": p["page"], "label": f"{t['rows'][r][0]} ({col})", "value": t["rows"][r][j + 1], "alternatives": [],
                                    "reason": "part of a total that does not add up"})
    return out


SKIP_PREFIX = "skip:"
UNREADABLE_RATIO, UNREADABLE_MIN_LINES = 0.6, 6


def skipped_pages(confirmed: Iterable[str]) -> set:
    """Pages a person chose to leave out ("skip:3" in the confirmed list). They are neither indexed nor counted as waiting for review."""
    return {int(c[len(SKIP_PREFIX):]) for c in confirmed if str(c).startswith(SKIP_PREFIX) and str(c)[len(SKIP_PREFIX):].isdigit()}


def pending_by_page(items: Iterable[Dict[str, Any]], corrections: Dict[str, Any], confirmed: Iterable[str]) -> Dict[int, List[Dict[str, Any]]]:
    confirmed = list(confirmed)
    done = set(corrections) | set(confirmed)
    skipped = skipped_pages(confirmed)
    out: Dict[int, List[Dict[str, Any]]] = {}
    for it in items:
        if it["id"] not in done and it["page"] not in skipped:
            out.setdefault(it["page"], []).append(it)
    return out


def unreadable_pages(result: Dict[str, Any]) -> Dict[int, Dict[str, int]]:
    """Pages where most lines were doubtful: the scan is too poor for local OCR, so checking line by line would be pointless.
    Returns {page: {"doubtful": n, "lines": n}}."""
    out: Dict[int, Dict[str, int]] = {}
    for p in result.get("pages", []):
        lines = p.get("lines") or []
        bad = sum(1 for l in lines if l.get("status") in ("review", "undetermined"))
        if len(lines) >= UNREADABLE_MIN_LINES and bad / len(lines) >= UNREADABLE_RATIO:
            out[p["page"]] = {"doubtful": bad, "lines": len(lines)}
    return out


# ---------------------------------------------------------------------------------------------------------------------
# chunks
# ---------------------------------------------------------------------------------------------------------------------

def _apply_text_corrections(text: str, page: int, result: Dict[str, Any], corrections: Dict[str, Any]) -> str:
    page_obj = next((p for p in result["pages"] if p["page"] == page), None)
    if not page_obj:
        return text
    for i, ln in enumerate(page_obj.get("lines") or []):
        fix = corrections.get(f"p{page}:l{i}")
        if fix is not None and fix.get("value") is not None and ln["text"] in text:
            text = text.replace(ln["text"], str(fix["value"]))
    return text


def _block_text(b: Dict[str, Any]) -> str:
    t = b["type"]
    if t in ("heading", "paragraph", "text"):
        return b.get("text", "")
    if t == "key_value":
        return "\n".join(f"{i.get('label')}: {i.get('value')}" for i in b.get("items", []) if i.get("value") not in (None, ""))
    if t == "list":
        return "\n".join(f"- {i}" if isinstance(i, str) else f"- {i.get('text', i)}" for i in b.get("items", []))
    return ""


def _md_table(columns: List[str], rows: List[List[str]], caption: Optional[str], units: Optional[str], currency: Optional[str]) -> str:
    head = f"Table: {caption}\n" if caption else "Table\n"
    notes = ", ".join(x for x in (f"amounts in {units}" if units else "", f"currency {currency}" if currency else "") if x)
    if notes:
        head += f"({notes})\n"
    esc = lambda c: str(c if c is not None else "").replace("|", "/")
    lines = ["| " + " | ".join(esc(c) for c in columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    lines += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return head + "\n".join(lines)


def _form_tables(result: Dict[str, Any], page: int, corrections: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for f in _forms_by_doc(result):
        for tname, rows in (f.get("tables") or {}).items():
            if not rows:
                continue
            first = next(iter(rows[0].values()), {})
            if (first.get("page") or (f.get("pages") or [None])[0]) != page:
                continue
            keys = list(rows[0].keys())
            cols = (f.get("table_columns") or {}).get(tname) or [rows[0][k].get("label") or k for k in keys]
            d, body, ids = f["doc_index"], [], []
            for ri, row in enumerate(rows):
                body.append([(corrections.get(f"f{d}:{tname}[{ri}].{k}") or {}).get("value", row[k].get("value")) if k in row else "" for k in keys])
                ids.append([f"f{d}:{tname}[{ri}].{k}" for k in keys])
            out.append({"title": tname.replace("_", " ").title(), "columns": [str(c) for c in cols], "rows": body, "cell_ids": ids, "bbox": first.get("bbox")})
    return out


def _structure_tables(result: Dict[str, Any], page: int, corrections: Dict[str, Any]) -> List[Dict[str, Any]]:
    page_obj = next((p for p in result["pages"] if p["page"] == page), None)
    out, first = [], True
    ti = -1
    for b in ((page_obj or {}).get("structure") or {}).get("blocks", []):
        if b["type"] != "table":
            continue
        ti += 1
        ids, rows = [], []
        for ri, r in enumerate(b.get("rows") or []):
            cid = [f"t{page}.{ti}[{ri}].{ci}" for ci in range(len(r))]
            ids.append(cid)
            rows.append([(corrections.get(i) or {}).get("value", v) for i, v in zip(cid, r)])
        t = {"title": b.get("title"), "columns": b.get("columns") or [], "rows": rows, "cell_ids": ids, "bbox": b.get("bbox"), "range": b.get("range"), "sheet": b.get("sheet")}
        out.append(_stitch(result, page, t) if first else t)
        first = False
    return out


def _line_tables(result: Dict[str, Any], page: int, corrections: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Tables rebuilt from OCR lines when layout analysis found none (right-aligned statement numbers defeat it); see finance/linetables.py."""
    from app.docproc.services.finance import linetables
    page_obj = next((p for p in result["pages"] if p["page"] == page), None)
    if not page_obj or page_obj.get("source") not in SCANNED_SOURCES:
        return []
    out = []
    for ti, t in enumerate(linetables.tables_from_lines([l["text"] for l in page_obj.get("lines") or []])):
        ids = [[f"x{page}.{ti}[{ri}].{ci}" for ci in range(len(r))] for ri, r in enumerate(t["rows"])]
        rows = [[(corrections.get(i) or {}).get("value", v) for i, v in zip(idr, r)] for idr, r in zip(ids, t["rows"])]
        out.append({"title": t["title"], "columns": t["columns"], "rows": rows, "cell_ids": ids, "bbox": None, "line_range": t["line_range"]})
    return out


def page_tables(result: Dict[str, Any], page: int, corrections: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The tables of a page as they will be indexed, corrections applied: the engine's labelled form tables when it built them, else the
    layout tables. Each carries `cell_ids`, the id a correction to that cell is stored under."""
    corrections = corrections or {}
    return _form_tables(result, page, corrections) or _structure_tables(result, page, corrections) or _line_tables(result, page, corrections)


def finance_for_page(result: Dict[str, Any], page: int, corrections: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Arithmetic checks of every table on a page: [{"index", "table", "statement", "checked", "findings"}]."""
    from app.docproc.services.finance import checks
    out = []
    for ti, t in enumerate(page_tables(result, page, corrections)):
        body = [list(r) for r in t["rows"]]
        if not body:
            continue
        res = checks.check_table(body)
        out.append({"index": ti, "table": t, **res})
    return out


_GENERIC_COL = re.compile(r"^Column \d+$")


def _stitch(result: Dict[str, Any], page: int, t: Dict[str, Any]) -> Dict[str, Any]:
    """A table that continues from the previous page (same width, starts near the top, header missing or repeated) takes the previous
    table's caption and column names, so its rows are not a block of unlabelled numbers."""
    prev = next((p for p in result["pages"] if p["page"] == page - 1), None)
    if not prev or page <= 1:
        return t
    pt = [b for b in (prev.get("structure") or {}).get("blocks", []) if b["type"] == "table"]
    if not pt or not t["columns"]:
        return t
    last = pt[-1]
    bbox = t.get("bbox") or [0, 0, 0, 0]
    if len(last.get("columns") or []) != len(t["columns"]) or bbox[1] > 0.3:
        return t
    generic = all(_GENERIC_COL.match(str(c)) for c in t["columns"])
    if not (generic or [str(c) for c in t["columns"]] == [str(c) for c in last["columns"]]):
        return t
    return {**t, "columns": last["columns"], "title": f"{last.get('title') or 'Table'} (continued)"}


def page_chunks(result: Dict[str, Any], page: int, base_meta: Dict[str, Any], corrections: Optional[Dict[str, Any]] = None,
                review_state: str = "confirmed") -> List[Dict[str, Any]]:
    """The chunks of one page: [{"text", "metadata"}]. `review_state` goes on every chunk ("confirmed" | "flagged") so retrieval can say
    how much to trust it."""
    corrections = corrections or {}
    ch = _ingestion()
    page_obj = next((p for p in result["pages"] if p["page"] == page), None)
    if page_obj is None:
        return []
    page_text = page_obj.get("text") or ""
    units, currency = detect_units(page_text), detect_currency(page_text)
    blocks = (page_obj.get("structure") or {}).get("blocks") or []
    text_parts: List[Tuple[str, Optional[List[float]]]] = []
    tables = page_tables(result, page, corrections)
    from_lines = [t["line_range"] for t in tables if t.get("line_range")]
    for b in blocks:
        if b["type"] not in ("table", "figure"):
            t = _block_text(b)
            if from_lines and t.strip() and any(len(l) for l in t.splitlines()) and all(" | " in l for l in t.splitlines() if l.strip()) \
                    and any(" | " in l for l in t.splitlines()):
                continue            # a paragraph that is only a row of a rebuilt table: it is in the table chunk, not repeated as text
            if t.strip():
                text_parts.append((t, b.get("bbox")))
    finance = {f["index"]: f for f in finance_for_page(result, page, corrections)}
    if not blocks:
        text_parts = [(page_text, None)] if page_text.strip() else []
    meta = {**base_meta, "page_num": page, "units": units, "currency": currency, "review_state": review_state,
            "period": base_meta.get("period") or detect_period(page_text), "content_type": "text", "source_kind": "upload"}
    out: List[Dict[str, Any]] = []
    splitter = ch.make_text_splitter()
    idx = 0
    text = _apply_text_corrections("\n\n".join(t for t, _ in text_parts), page, result, corrections)
    bbox = next((bb for _, bb in text_parts if bb), None)
    if text.strip():
        from langchain_core.documents import Document
        for d in splitter.split_documents([Document(page_content=text, metadata={})]):
            out.append({"text": d.page_content, "metadata": {**meta, "chunk_kind": "text", "chunk_index": idx, "bbox": bbox}})
            idx += 1
    for ti, t in enumerate(tables):
        fin = finance.get(ti) or {}
        verdict = ("failed" if fin.get("findings") else "passed") if fin.get("checked") else "n/a"
        block = _md_table([str(c) for c in t["columns"]], [[str(c) for c in r] for r in t["rows"]], t.get("title"), units, currency)
        if verdict == "failed":      # said in the passage itself, so an answer written from it can say so too
            block = block.replace("\n", "\n(note: the totals in this table do not add up as printed, so a figure may be misread or wrong in the source)\n", 1)
        for part in ch.split_table_block(block):
            extra = {"cell_range": t.get("range"), "sheet": t.get("sheet")} if t.get("range") else {}
            out.append({"text": TABLE_PREFIX + part, "metadata": {**meta, "chunk_kind": "table", "chunk_index": idx, "bbox": t.get("bbox"),
                                                                  "statement": fin.get("statement"), "arithmetic_check": verdict, **extra}})
            idx += 1
    for c in out:
        c["id"] = point_id(base_meta["doc_id"], page, c["metadata"]["chunk_index"])
        c["metadata"]["context_header"] = ch.context_header(
            {"company": base_meta.get("entity"), "filing_type": base_meta.get("doc_type_label"), "page_num": page, "period_end_date": None,
             "year": None}, base_meta.get("label")) or None
    return out
