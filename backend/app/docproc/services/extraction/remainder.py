"""
"Read the rest": whatever a templated document's pages hold that its template does not cover.

A template extracts its own fields and nothing else, so a letter above a form, notes below it, or a whole page of free
text between two forms would otherwise stay as plain text in pages.json. This reads the same pages with discovery
(no template) and then removes everything the template already accounts for:

    same label as a template field, option or table column     -> covered
    sits on top of a value the template extracted (page boxes)  -> covered
    the same long value the template extracted                  -> covered
    empty blanks                                                -> dropped (nothing to extract)

What is left is shown separately as "Other content", never merged into the form's own fields.
Printed boilerplate of the form (phone numbers in a header, a legend table) can survive this; it is shown so the
reviewer can ignore it, because no rule can tell boilerplate from data without a blank copy of the form.
"""
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from app.docproc.services.extraction import discover, normalize as N
from app.docproc.services.quality import validation

LABEL_COVER = 0.9
TABLE_COVER = 0.8
BOX_COVER = 0.5   # share of a discovered value's box that lies inside a template value's box


def _canon(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b, autojunk=False).ratio() if a and b else 0.0


def _labels(template: Dict[str, Any]) -> List[str]:
    out = [template.get("title", "")]
    for sec in template.get("sections", []):
        for f in sec.get("fields", []):
            out += [f["label"], *(f.get("options") or [])]
    for t in template.get("tables", []):
        out += [c["label"] for c in t.get("columns", [])]
    return [c for c in (_canon(x) for x in out) if c]


def _covered_boxes(form: Dict[str, Any]) -> Dict[int, List[List[float]]]:
    boxes: Dict[int, List[List[float]]] = {}
    cells = list(form["fields"].values()) + [c for rows in form["tables"].values() for r in rows for c in r.values()]
    for c in cells:
        if c.get("bbox"):
            boxes.setdefault(c["page"], []).append(c["bbox"])
    return boxes


def _inside(box: List[float], other: List[float]) -> float:
    """Share of `box` that lies inside `other`."""
    w = max(0.0, min(box[2], other[2]) - max(box[0], other[0]))
    h = max(0.0, min(box[3], other[3]) - max(box[1], other[1]))
    area = max(1e-6, (box[2] - box[0]) * (box[3] - box[1]))
    return w * h / area


def _empty(v: Any) -> bool:
    return v in (None, "", [], False) and not isinstance(v, bool)


def subtract(found: Dict[str, Any], form: Dict[str, Any], template: Dict[str, Any]) -> Dict[str, Any]:
    """`found` (a discovery result) minus what `form`/`template` already cover. Returns the same shape."""
    labels = _labels(template)
    boxes = _covered_boxes(form)
    values = {_canon(str(c["value"])) for c in form["fields"].values() if isinstance(c.get("value"), str)}
    values |= {_canon(str(c["value"])) for rows in form["tables"].values() for r in rows for c in r.values() if isinstance(c.get("value"), str)}
    values = {v for v in values if len(v) >= 6}

    def covered(cell: Dict[str, Any]) -> bool:
        if _empty(cell.get("value")) and not isinstance(cell.get("value"), bool):
            return True                                        # an empty blank carries nothing
        label = _canon(cell.get("label"))
        if label and any(_similar(label, l) >= LABEL_COVER for l in labels):
            return True
        bb = cell.get("bbox")
        if bb and any(_inside(bb, o) >= BOX_COVER for o in boxes.get(cell.get("page"), [])):
            return True
        return isinstance(cell.get("value"), str) and _canon(cell["value"]) in values

    fields = {k: c for k, c in found["fields"].items() if not covered(c)}
    tables, columns = {}, {}
    template_cols = [_canon(" ".join(c["label"] for c in t["columns"])) for t in template.get("tables", [])]
    for key, rows in found["tables"].items():
        head = _canon(" ".join(found["table_columns"].get(key, [])))
        known = any(_similar(head, tc) >= TABLE_COVER for tc in template_cols)
        body = [r for r in rows if any(not _empty(c.get("value")) for c in r.values())]
        if body and not known:
            tables[key], columns[key] = body, found["table_columns"][key]
    return {**found, "fields": fields, "tables": tables, "table_columns": columns}


async def remainder_form(work: str, form: Dict[str, Any], template: Dict[str, Any], models: List[str], reader=None) -> Optional[Dict[str, Any]]:
    """A form-shaped entry for the rest of `form`'s pages, or None when the template covers everything."""
    found = await discover.discover_document(work, form["pages"], models, reader)
    rest = subtract(found, form, template)
    if not rest["fields"] and not rest["tables"]:
        return None
    pages = form["pages"]
    where = f"page {pages[0]}" if len(pages) == 1 else f"pages {pages[0]}-{pages[-1]}"
    return {"doc_type": "other_content", "template_id": "discovered", "discovered": True, "remainder": True,
            "remainder_of": form["doc_index"], "label": f"Other content on {where}", "pages": pages, "readers": rest["readers"],
            "fields": rest["fields"], "tables": rest["tables"], "table_columns": rest["table_columns"],
            "validation": validation.review_notes(rest["fields"], rest["tables"])}
