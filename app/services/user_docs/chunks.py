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


def review_items(result: Dict[str, Any]) -> List[Dict[str, Any]]:
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
    return items


def pending_by_page(items: Iterable[Dict[str, Any]], corrections: Dict[str, Any], confirmed: Iterable[str]) -> Dict[int, List[Dict[str, Any]]]:
    done = set(corrections) | set(confirmed)
    out: Dict[int, List[Dict[str, Any]]] = {}
    for it in items:
        if it["id"] not in done:
            out.setdefault(it["page"], []).append(it)
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
            d, body = f["doc_index"], []
            for ri, row in enumerate(rows):
                body.append([(corrections.get(f"f{d}:{tname}[{ri}].{k}") or {}).get("value", row[k].get("value")) if k in row else "" for k in keys])
            out.append({"title": tname.replace("_", " ").title(), "columns": [str(c) for c in cols], "rows": body, "bbox": first.get("bbox")})
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
    tables: List[Dict[str, Any]] = []
    form_tables = _form_tables(result, page, corrections)
    first_table = True
    for b in blocks:
        if b["type"] == "table":
            if not form_tables:
                t = {"title": b.get("title"), "columns": b.get("columns") or [], "rows": b.get("rows") or [], "bbox": b.get("bbox"),
                     "range": b.get("range"), "sheet": b.get("sheet")}
                if first_table:
                    t = _stitch(result, page, t)
                tables.append(t)
            first_table = False
        elif b["type"] != "figure":
            t = _block_text(b)
            if t.strip():
                text_parts.append((t, b.get("bbox")))
    tables = form_tables or tables
    if not blocks:
        text_parts = [(page_text, None)] if page_text.strip() else []
    elif page_obj.get("source") == "native" or not text_parts:
        pass
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
    for t in tables:
        block = _md_table([str(c) for c in t["columns"]], [[str(c) for c in r] for r in t["rows"]], t.get("title"), units, currency)
        for part in ch.split_table_block(block):
            extra = {"cell_range": t.get("range"), "sheet": t.get("sheet")} if t.get("range") else {}
            out.append({"text": TABLE_PREFIX + part, "metadata": {**meta, "chunk_kind": "table", "chunk_index": idx, "bbox": t.get("bbox"), **extra}})
            idx += 1
    for c in out:
        c["id"] = point_id(base_meta["doc_id"], page, c["metadata"]["chunk_index"])
        c["metadata"]["context_header"] = ch.context_header(
            {"company": base_meta.get("entity"), "filing_type": base_meta.get("doc_type_label"), "page_num": page, "period_end_date": None,
             "year": None}, base_meta.get("label")) or None
    return out
