"""Shape the stored result into the downloadable JSON files."""
from typing import Any, Dict


def pages_json(ex) -> Dict[str, Any]:
    r = ex.result or {}
    pages = r.get("pages", [])         # each page: how it was read, its text and lines, and its structure (blocks, entities)
    return {"extraction_id": ex.id, "filename": ex.filename, "page_count": len(pages),
            "reading": r.get("reading"), "documents": r.get("documents", []), "pages": pages}


def structure_json(ex) -> Dict[str, Any]:
    """Each document's pages as blocks in reading order: headings, paragraphs, label/value groups, tables, lists, and the
    dates, amounts and so on found in them. Built from layout alone, so it exists for every document whether or not a
    template matched it. This is the content-only view of what pages.json holds page by page, grouped by document."""
    r = ex.result or {}
    by_page = {p["page"]: p for p in r.get("pages", [])}
    docs = []
    for d in r.get("documents", []):
        pages = []
        for n in d.get("pages", []):
            p = by_page.get(n)
            if not p:
                continue
            st = p.get("structure") or {"blocks": [], "entities": [], "layout": False}
            pages.append({"page": n, "source": p.get("source"), "layout": st.get("layout", False), "positions_from": st.get("positions_from"), "blocks": st.get("blocks", []),
                          "entities": st.get("entities", []), "running": st.get("running", [])})
        docs.append({"doc_index": d.get("doc_index"), "type": d.get("type"), "label": d.get("label"), "pages": pages})
    return {"extraction_id": ex.id, "filename": ex.filename, "reading": r.get("reading"), "documents": docs}


def apply_corrections(form: Dict[str, Any], corrections: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay a reviewer's edits. Keys are "<doc_index>:<field key>" for a field and "<doc_index>:<table>[<row>].<column>"
    for a table cell (row counted from 0)."""
    di = form["doc_index"]

    def corrected(cell: Dict[str, Any], key: str) -> Dict[str, Any]:
        c = dict(cell)
        if key in corrections:
            c.update({"original": cell.get("value"), "value": corrections[key], "status": "corrected"})
        return c

    fields = {k: corrected(c, f"{di}:{k}") for k, c in form["fields"].items()}
    tables = {t: [{col: corrected(cell, f"{di}:{t}[{ri}].{col}") for col, cell in row.items()} for ri, row in enumerate(rows)]
              for t, rows in form["tables"].items()}
    return {**form, "fields": fields, "tables": tables}


def review_count(result: Dict[str, Any], corrections: Dict[str, Any]) -> int:
    """Values still waiting for a human: flagged fields and table cells that have not been confirmed or edited."""
    n = 0
    for f in (result or {}).get("forms", []):
        f = apply_corrections(f, corrections or {})
        cells = list(f["fields"].values()) + [c for rows in f["tables"].values() for r in rows for c in r.values()]
        n += sum(1 for c in cells if c.get("status") in ("review", "undetermined"))
    return n


def _flat(form: Dict[str, Any]) -> Dict[str, Any]:
    values: Dict[str, Any] = {k: c.get("value") for k, c in form["fields"].items()}
    for tkey, rows in form["tables"].items():
        values[tkey] = [{col: cell.get("value") for col, cell in row.items()} for row in rows]
    return values


def _enrich(form: Dict[str, Any], template: Dict[str, Any]) -> Dict[str, Any]:
    """Give cells their field type and choices from the template. Extractions made before cells carried them (and any
    whose template was edited since) then still get the right controls on screen."""
    fields = dict(form["fields"])
    for sec in template.get("sections", []):
        for f in sec.get("fields", []):
            cell = fields.get(f["key"])
            if cell is not None and "type" not in cell:
                fields[f["key"]] = {**cell, "type": f["type"], **({"options": f["options"]} if f.get("options") else {})}
    return {**form, "fields": fields}


def fields_json(ex, templates: Dict[str, Dict[str, Any]] | None = None) -> Dict[str, Any]:
    r = ex.result or {}
    forms = []
    for f in r.get("forms", []):
        f = apply_corrections(f, ex.corrections or {})
        if templates and not f.get("discovered") and f.get("doc_type") in templates:
            f = _enrich(f, templates[f["doc_type"]])
        forms.append({**f, "values": _flat(f)})
    return {"extraction_id": ex.id, "filename": ex.filename, "forms": forms,
            "other_documents": r.get("other_documents", []), "validation": r.get("validation", []),
            "rest_checked": r.get("rest_checked", []),
            "stats": r.get("stats", {})}


def truth_json(ex) -> Dict[str, Any]:
    """Answer-key shape consumed by eval/score.py: one entry per extracted document, plain values only."""
    out = []
    for f in fields_json(ex)["forms"]:
        out.append({"doc_type": f["doc_type"] if not f.get("discovered") else (f.get("label") or f["doc_type"]),
                    "pages": f["pages"], "discovered": bool(f.get("discovered")),
                    "fields": {k: c.get("value") for k, c in f["fields"].items()},
                    "tables": {t: [{col: cell.get("value") for col, cell in row.items()} for row in rows] for t, rows in f["tables"].items()}})
    return {"note": f"Answer key from {ex.filename}; verify before trusting.", "documents": out}
