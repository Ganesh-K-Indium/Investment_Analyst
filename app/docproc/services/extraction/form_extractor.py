"""
Template-driven form extraction.

For each field the value comes from two independent vision readers (same trust model as ocr.py):
    both agree -> "agreed" | disagree / one empty -> "review" (the other reading kept as an alternative)
    one reader only -> "single" | neither found anything -> "empty"
Checkbox groups never go to the models: they are read from glyphs, then pixels (checkboxes.py); only when both are
silent do we fall back to the models and flag the result for review.

Every value is then grounded: located in the page's embedded words so the UI can highlight it. Sections are used as
vertical bands so "Tax ID" in the provider block and in the facility block resolve to different boxes.
"""
import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.docproc.services.extraction import adjudicate, checkboxes, normalize as N
from app.docproc.services.extraction import local_reader
from app.docproc.services.reading import locate, ocr, pdf_pages

logger = logging.getLogger("app.services.form_extractor")

CHECKBOX_TYPES = ("enum", "multi_enum", "checkbox")


def _prompt(template: Dict[str, Any]) -> str:
    lines = []
    for sec in template["sections"]:
        for f in sec["fields"]:
            where = sec["header"] or "top/bottom of the form"
            if f["type"] == "enum":
                lines.append(f'- "{f["key"]}": which ONE box is checked, one of {f["options"]}, or null if none  [{where}]')
            elif f["type"] == "multi_enum":
                lines.append(f'- "{f["key"]}": a list of the options whose box is checked, from {f["options"]} (empty list if none)  [{where}]')
            elif f["type"] == "checkbox":
                lines.append(f'- "{f["key"]}": true if this box is checked, else false  [{where}]  ({f["label"]})')
            else:
                lines.append(f'- "{f["key"]}": {f["label"]}  [{where}; {f["type"]}]')
    cols = ", ".join(f'"{c["key"]}" ({c["label"]})' for c in template["tables"][0]["columns"]) if template.get("tables") else ""
    return f"""You are reading a "{template['title']}" for a data-entry team.

Return the value written or printed for each field below, exactly as it appears. For check boxes, a box is checked only if it contains a tick, cross or fill.
- Copy digits and letters exactly. If a character is unreadable write [?] in its place. Never guess or complete.
- Keep a leading decimal point (.2% is not 2%), percent and currency signs, ranges (7-20) and units exactly as written. Copy names letter by letter.
- If a field is blank, return null. Do not infer a value from another section.
- The same label (for example Tax ID, Phone, Street Address, City, First/Last Name) can occur in more than one section; use the section given for each key.
- Dates stay in the written format (mm/dd/yyyy).
- Ignore fax header/footer stamps and page numbers.
- This may be one page of a longer document: return null for anything not on this page.

Fields:
{chr(10).join(lines)}

Also return the table as "table_rows": a list of objects with keys {cols}, one per filled row on this page (skip empty rows); [] if there is none.

Return JSON only: {{"fields": {{"<key>": "<value or null>"}}, "table_rows": [{{...}}]}}"""


async def _read(model: str, jpeg: bytes, prompt: str) -> Dict[str, Any]:
    from app.docproc.engines.providers import llm
    return parse_reply(await llm.complete(model, prompt, jpeg, stage="form_extraction"))


def parse_reply(raw: str) -> Dict[str, Any]:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    try:
        data = json.loads(text)
    except ValueError:
        return {"fields": {}, "table_rows": []}
    if not isinstance(data, dict):
        return {"fields": {}, "table_rows": []}
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    rows = data.get("table_rows") if isinstance(data.get("table_rows"), list) else (data.get("cpt_rows") if isinstance(data.get("cpt_rows"), list) else [])
    return {"fields": fields, "table_rows": [r for r in rows if isinstance(r, dict)]}


def _clean(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def merge_field(a: Optional[str], b: Optional[str], ftype: str, two_readers: bool) -> Dict[str, Any]:
    """Combine the primary reading `a` with the optional second reading `b`."""
    a, b = _clean(a), _clean(b)
    out: Dict[str, Any] = {"value": a, "status": "agreed", "alternatives": []}
    if a is None and b is None:
        out["status"] = "empty"
        return out
    if "[?]" in (a or ""):
        out["status"] = "review"
        out["reason"] = "reader marked a character unreadable"
    if not two_readers:
        out["status"] = "single" if out["status"] != "review" else "review"
        return out
    if N.normalize(a, ftype) != N.normalize(b, ftype):
        out["status"] = "review"
        out["reason"] = out.get("reason") or "readers disagree"
        if b is not None:
            out["alternatives"] = [b]
        if a is None:
            out["value"] = b  # keep something visible rather than hiding the only reading
            out["alternatives"] = []
    return out


def model_checkbox(ftype: str, a: Any, b: Any, two: bool) -> Optional[Dict[str, Any]]:
    """Checkbox value from the model readers, used only when glyph and pixel reading could not decide.
    Returns None when neither reader said anything."""
    def canon(v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, list):
            return ", ".join(sorted(str(x) for x in v)) or "(none)"
        if isinstance(v, bool):
            return "yes" if v else "no"
        return _clean(v) or "(none)"
    ca, cb = canon(a), canon(b)
    if ca is None and cb is None:
        return None
    merged = merge_field(ca, cb, "text", two)
    value: Any = a if a is not None else b
    if ftype == "multi_enum" and not isinstance(value, list):
        value = [value] if value else []
    merged["value"] = value
    merged["method"] = "model"
    if not two and merged["status"] == "single":
        merged["status"] = "review"  # one unverified reading of a box is not enough to trust
        merged["reason"] = "only one reader, and the box could not be read from pixels"
    return merged


def _bands(path: str, page_num: int, template: Dict[str, Any]) -> Dict[str, Tuple[float, float]]:
    """section id -> (y_min, y_max) on the page, from where the section headers sit."""
    marks = []
    for sec in template["sections"]:
        if sec["header"]:
            rects = pdf_pages.search_rects(path, page_num, sec["header"])
            if rects:
                marks.append((rects[0][1], sec["id"]))
    marks.sort()
    bands: Dict[str, Tuple[float, float]] = {}
    for i, (y, sid) in enumerate(marks):
        bands[sid] = (y - 2, marks[i + 1][0] - 2 if i + 1 < len(marks) else 1e9)
    return bands


def _ground(path: str, page_num: int, value: str, label: str, band: Tuple[float, float]) -> Optional[List[float]]:
    return locate.find(path, page_num, value, label, band)


def _third_opinion(cell: Dict[str, Any], hit: Optional[tuple]) -> None:
    """Two models can share a misread. Tesseract is an independent third reader of the same pixels: when the agreed
    value is a near miss of what it read at that spot (similar but not identical), send the field to review and show
    Tesseract's reading. Exact matches, and values Tesseract could not find at all, are left alone: Tesseract is the
    weakest reader, so its silence proves nothing."""
    if not hit or cell.get("status") not in ("agreed", "single"):
        return
    box, seen, score = hit
    value = cell.get("value")
    if not isinstance(value, str) or len(locate.alnum(value)) < 5 or score >= 0.999:
        return
    if locate.alnum(seen) != locate.alnum(value):
        cell["status"] = "review"
        cell["reason"] = "the scanned text reads this differently"
        if seen not in cell["alternatives"]:
            cell["alternatives"] = list(cell["alternatives"]) + [seen]


def _all_options(template: Dict[str, Any]) -> List[str]:
    return [o for sec in template["sections"] for f in sec["fields"] if f["type"] in CHECKBOX_TYPES
            for o in (f.get("options") or [f["label"]])]


def _checkbox_fields(path: str, page_num: int, text: str, template: Dict[str, Any], bands) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for sec in template["sections"]:
        band = bands.get(sec["id"], (0.0, 1e9))
        for f in sec["fields"]:
            if f["type"] not in CHECKBOX_TYPES:
                continue
            options = f.get("options") or [f["label"]]
            states: Dict[str, Optional[bool]] = {o: checkboxes.glyph_state(text, o) for o in options}
            missing = [o for o, s in states.items() if s is None]
            method = "glyph"
            if missing and len(missing) == len(options):
                try:
                    px = checkboxes.pixel_states(path, page_num, {o: band for o in options}, others=_all_options(template))
                except Exception as e:  # PDF unreadable for rendering -> leave undecided
                    logger.warning("pixel checkbox read failed: %s", e)
                    px = {}
                states = {o: px.get(o) for o in options}
                method = "pixel"
            decided = [o for o, s in states.items() if s is not None]
            if not decided:
                out[f["key"]] = {"value": None, "status": "undetermined", "method": None, "alternatives": []}
                continue
            checked = [o for o, s in states.items() if s]
            if f["type"] == "checkbox":
                value: Any = bool(checked)
            elif f["type"] == "enum":
                value = checked[0] if len(checked) == 1 else (checked or None)
            else:
                value = checked
            status = "agreed"
            if f["type"] == "enum" and len(checked) > 1:
                status = "review"
            partial = len(decided) < len(options)
            if partial:
                status = "review"  # a label was not found, so its box was not judged; a model reading settles it below
            out[f["key"]] = {"value": value, "status": status, "method": method, "alternatives": [], "partial": partial}
    return out


def _same_choice(a: Any, b: Any) -> bool:
    def c(v):
        if isinstance(v, list):
            return tuple(sorted(str(x).lower() for x in v))
        if isinstance(v, bool) or v is None:
            return v
        return str(v).strip().lower()
    return c(a) == c(b) or (c(a) in ((), None, False) and c(b) in ((), None, False))


def _prefer_page_text(cell: Dict[str, Any], ftype: str, page_alnum: str) -> None:
    """Readers disagree: if exactly one candidate also appears in the page's own (Tesseract) text, put it first.
    Still flagged for review: the page text is a third opinion, not proof."""
    if cell.get("status") != "review" or not cell.get("alternatives") or not page_alnum:
        return
    cands = [cell["value"]] + list(cell["alternatives"])
    hits = [c for c in cands if isinstance(c, str) and len(locate.alnum(c)) >= 4 and locate.alnum(c) in page_alnum]
    if len(hits) == 1 and hits[0] != cell["value"]:
        cell["alternatives"] = [c for c in cands if c != hits[0]]
        cell["value"] = hits[0]
        cell["reason"] = "only this reading also appears in the page's scanned text"


async def extract_form(path: str, page_num: int, text: str, template: Dict[str, Any], models: List[str], reader=None,
                       handwritten: bool = False, adjudicator=None, page_text: str = "") -> Dict[str, Any]:
    """One page against a template -> {"fields": {key: cell}, "tables": {key: [row, ...]}, "readers": [...]}."""
    injected = reader is not None
    reader = reader or _read
    bands = await asyncio.to_thread(_bands, path, page_num, template)
    fields = await asyncio.to_thread(_checkbox_fields, path, page_num, text, template, bands)
    page_alnum = await asyncio.to_thread(locate.page_alnum, path, page_num)

    replies: List[Dict[str, Any]] = []
    jpeg = None
    if models:
        jpeg = await asyncio.to_thread(pdf_pages.render_jpeg, path, page_num)
        prompt = _prompt(template)
        results = await asyncio.gather(*[asyncio.wait_for(reader(m, jpeg, prompt), ocr.ENGINE_TIMEOUT_SECONDS + 5) for m in models],
                                       return_exceptions=True)
        for m, r in zip(models, results):
            if isinstance(r, BaseException):
                logger.warning("Form reader %s failed: %s", m, r)
            else:
                replies.append(r)
    if not replies and page_text.strip():
        # No model is in use (or none answered): the page's own text, read locally, fills the text fields from their printed labels.
        replies.append(local_reader.read_fields(page_text, template))
    two = len(replies) > 1

    for sec in template["sections"]:
        for f in sec["fields"]:
            a = replies[0]["fields"].get(f["key"]) if replies else None
            b = replies[1]["fields"].get(f["key"]) if two else None
            if f["type"] in CHECKBOX_TYPES:
                cur = fields.get(f["key"], {})
                if cur.get("status") == "undetermined" and replies:
                    got = model_checkbox(f["type"], a, b, two)
                    if got:
                        fields[f["key"]] = got
                elif cur.get("partial") and replies:
                    got = model_checkbox(f["type"], a, b, two)
                    if got and got["status"] == "agreed" and _same_choice(got["value"], cur["value"]):
                        cur.update({"status": "agreed", "method": "pixel+model"})
                    elif got:
                        cur["alternatives"] = [got["value"]] if not isinstance(got["value"], list) else [", ".join(got["value"]) or "(none)"]
                        cur["reason"] = "some boxes could not be located; the model reading differs"
                continue
            merged = merge_field(a, b, f["type"], two)
            if two and not handwritten:           # Tesseract cannot read handwriting: it must not outvote the models
                _prefer_page_text(merged, f["type"], page_alnum)
            merged["normalized"] = N.normalize(merged["value"], f["type"])
            fields[f["key"]] = merged

    tables: Dict[str, List[Dict[str, Any]]] = {}
    for tbl in template.get("tables", []):
        ra = (replies[0].get("table_rows") or []) if replies else []
        rb = (replies[1].get("table_rows") or []) if two else []
        rows = []
        for i in range(max(len(ra), len(rb))):
            row: Dict[str, Any] = {}
            for col in tbl["columns"]:
                va = ra[i].get(col["key"]) if i < len(ra) else None
                vb = rb[i].get(col["key"]) if i < len(rb) else None
                row[col["key"]] = merge_field(va, vb, "text", two)
            rows.append(row)
        tables[tbl["key"]] = rows

    if two and jpeg is not None and (adjudicator is not None or not injected):
        kinds = {f["key"]: f["type"] for sec in template["sections"] for f in sec["fields"]}
        items = [(f"f:{k}", c, kinds.get(k, "text") if kinds.get(k) in ("date", "phone", "npi", "tax_id", "zip", "state", "icd10") else "text")
                 for k, c in fields.items() if kinds.get(k) not in CHECKBOX_TYPES]
        items += [(f"t:{k}:{r}:{ck}", c, "text") for k, rows in tables.items() for r, row in enumerate(rows) for ck, c in row.items()]
        await adjudicate.settle(jpeg, items, adjudicator)

    def ground_all():
        for sec in template["sections"]:
            band = bands.get(sec["id"], (0.0, 1e9))
            for f in sec["fields"]:
                got = fields.get(f["key"])
                if got and isinstance(got.get("value"), str) and got["value"]:
                    hit = locate.find_with_text(path, page_num, got["value"], f["label"], band)
                    got["bbox"] = hit[0] if hit else None
                    if not handwritten:
                        _third_opinion(got, hit)
        for tbl in template.get("tables", []):
            band = next((bands[sec["id"]] for sec in template["sections"] if sec["id"] in bands and sec["header"] == tbl.get("header")), (0.0, 1e9))
            for row in tables.get(tbl["key"], []):
                for ckey, cell in row.items():
                    if isinstance(cell.get("value"), str) and cell["value"]:
                        hit = locate.find_with_text(path, page_num, cell["value"], "", band)
                        cell["bbox"] = hit[0] if hit else None
                        if not handwritten:
                            _third_opinion(cell, hit)
    await asyncio.to_thread(ground_all)
    for sec in template["sections"]:
        for f in sec["fields"]:
            if f["key"] in fields:                     # the UI picks a control from this: text, tick box, choice list
                fields[f["key"]]["type"] = f["type"]
                if f.get("options"):
                    fields[f["key"]]["options"] = f["options"]
    for got in fields.values():
        got.pop("partial", None)
        got.setdefault("bbox", None)
        got["page"] = page_num
    for rows in tables.values():
        for row in rows:
            for cell in row.values():
                cell.setdefault("bbox", None)
                cell["page"] = page_num
    return {"fields": fields, "tables": tables, "readers": models[:len(replies)] if models else []}


_RANK = {"agreed": 0, "corrected": 0, "single": 1, "review": 2, "undetermined": 3, "empty": 4}


async def extract_document(path: str, pages: List[int], texts: Dict[int, str], template: Dict[str, Any],
                           models: List[str], reader=None, handwritten: Any = (), adjudicator=None,
                           page_texts: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
    """A whole (possibly multi-page) document against one template.

    Each page is read for every field; a field's final cell is the best-supported reading across pages (agreed beats
    single beats review beats empty; the earliest page wins ties), and table rows are concatenated in page order.
    Templates with "single_page": true read only the first page (a fixed form that never continues).
    """
    use = pages[:1] if template.get("single_page") else pages
    results = await asyncio.gather(*[extract_form(path, n, texts.get(n, ""), template, models, reader, n in handwritten, adjudicator, (page_texts or {}).get(n, "")) for n in use])
    fields: Dict[str, Dict[str, Any]] = {}
    for res in results:
        for key, cell in res["fields"].items():
            have = fields.get(key)
            if have is None or _RANK.get(cell["status"], 4) < _RANK.get(have["status"], 4):
                fields[key] = cell
    tables: Dict[str, List[Dict[str, Any]]] = {}
    for res in results:
        for key, rows in res["tables"].items():
            tables.setdefault(key, []).extend(rows)
    return {"fields": fields, "tables": tables, "readers": results[0]["readers"] if results else []}
