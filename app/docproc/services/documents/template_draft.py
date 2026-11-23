"""
Templates from examples, so nobody writes JSON by hand:

  draft_from_blank   a scan or PDF of an empty form -> a draft template (fields, types, required marks, choice groups,
                     tables) and the registry entry that makes the app recognise that form
  from_discovery     an extraction that was reviewed -> the same, built from the discovered fields

Drafts are returned for a person to correct; nothing is registered until it is saved (validate_template guards that).
"""
import asyncio
import re
from typing import Any, Dict, List, Optional

from app.docproc.services.documents import classify
from app.docproc.services.extraction import discover
from app.docproc.services.reading import pdf_pages, ocr

FIELD_TYPES = {"text", "date", "phone", "state", "zip", "npi", "tax_id", "icd10", "number", "currency", "email", "id",
               "enum", "multi_enum", "checkbox"}
MODEL_TYPE = {"id": "text", "number": "text", "currency": "text", "email": "text"}
MAX_FIELDS = 400
CANONICAL_KINDS = {"name", "date", "text", "set", "mentioned"}


def _entry(doc_type: str, title: str) -> Dict[str, Any]:
    words = [w for w in re.split(r"\s+", title.strip().lower()) if w]
    return {"type": doc_type, "label": title.strip() or doc_type, "score": 3,
            "any": [r"\s+".join(re.escape(w) for w in words)] if words else [], "template": doc_type, "extractor": "template_form"}


def build(title: str, fields: List[Dict[str, Any]], groups: List[Dict[str, Any]], tables: List[Dict[str, Any]], single_page: bool) -> Dict[str, Any]:
    """Assemble a template dict from normalised draft pieces."""
    title = title or "Untitled document"
    doc_type = discover.slug(title) or "custom_document"
    sections: Dict[str, Dict[str, Any]] = {}
    used: Dict[str, int] = {}

    def add(section: Optional[str], f: Dict[str, Any]) -> None:
        sid = discover.slug(section) or "main"
        sec = sections.setdefault(sid, {"id": sid, "header": section if sid != "main" else None, "fields": []})
        base = (f"{sid}." if sid != "main" else "") + (discover.slug(f["label"]) or "field")
        used[base] = used.get(base, 0) + 1
        sec["fields"].append({**f, "key": base if used[base] == 1 else f"{base}_{used[base]}"})

    for f in fields:
        add(f.get("section"), {"label": f["label"], "type": MODEL_TYPE.get(f.get("type", "text"), f.get("type", "text")) if f.get("type", "text") in FIELD_TYPES else "text",
                               **({"required": True} if f.get("required") else {})})
    for g in groups:
        add(g.get("section"), {"label": g["label"], "type": "multi_enum" if g.get("multiple") else "enum", "options": g["options"],
                               **({"required": True} if g.get("required") else {})})
    tbls = []
    for n, t in enumerate(tables):
        cols = discover._unique([discover.slug(c["label"]) or f"col_{i + 1}" for i, c in enumerate(t["columns"])])
        tbls.append({"key": discover.slug(t.get("title")) or f"table_{n + 1}", "header": t.get("title"),
                     "columns": [{"key": k, "label": c["label"], **({"required": True} if c.get("required") else {})} for k, c in zip(cols, t["columns"])]})
    return {"template_id": f"custom-{doc_type}", "doc_type": doc_type, "title": title, "single_page": single_page,
            "sections": list(sections.values()), "tables": tbls, "canonical": {}, "rules": []}


async def draft_from_blank(path: str, models: List[str], reader=None) -> Dict[str, Any]:
    """Read every page of a blank form with the first (stronger) model and draft a template."""
    n = await asyncio.to_thread(pdf_pages.page_count, path)
    reader = reader or discover._read
    fields: List[Dict[str, Any]] = []
    groups: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    title = None
    for page in range(1, n + 1):
        jpeg = await asyncio.to_thread(pdf_pages.render_jpeg, path, page)
        got = discover.parse(await asyncio.wait_for(reader(models[0], jpeg, discover.BLANK_PROMPT), ocr.ENGINE_TIMEOUT_SECONDS + 5))
        title = title or got["title"]
        fields += got["fields"]
        groups += got["choice_groups"]
        tables += [{"title": t["title"], "columns": [{"label": c, "required": False} for c in t["columns"]]} for t in got["tables"]]
    tpl = build(title or "Untitled document", fields, groups, tables, single_page=(n == 1))
    return {"template": tpl, "entry": _entry(tpl["doc_type"], tpl["title"])}


def from_discovery(form: Dict[str, Any], title: Optional[str]) -> Dict[str, Any]:
    """Promote a discovered extraction to a template. Checkbox fields become single-box checkboxes (no way to know the
    original grouping); types are carried over from what was discovered."""
    fields, tables = [], []
    for key, c in form["fields"].items():
        is_box = c.get("method") in ("pixel", "model") or isinstance(c.get("value"), bool)
        fields.append({"label": c.get("label") or key, "section": c.get("section"),
                       "type": "checkbox" if is_box else ("date" if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", str(c.get("value") or "")) else "text")})
    for key, cols in (form.get("table_columns") or {}).items():
        tables.append({"title": key.replace("_", " "), "columns": [{"label": c, "required": False} for c in cols]})
    tpl = build(title or form.get("label") or "Untitled document", fields, [], tables, single_page=len(form.get("pages", [])) <= 1)
    return {"template": tpl, "entry": _entry(tpl["doc_type"], tpl["title"])}


def validate_template(t: Any) -> List[str]:
    """Reasons a template must not be saved (empty list when fine)."""
    errs: List[str] = []
    if not isinstance(t, dict):
        return ["Template must be an object."]
    doc_type = t.get("doc_type")
    if not isinstance(doc_type, str) or not re.fullmatch(r"[a-z0-9_]{2,48}", doc_type):
        errs.append("doc_type must be 2-48 lowercase letters, digits or underscores.")
    elif doc_type in {d["type"] for d in classify.DOC_TYPES} | {"unknown", "blank_or_logo"}:
        errs.append(f"'{doc_type}' is a built-in type; choose another name.")
    if not str(t.get("title") or "").strip():
        errs.append("A title is required.")
    keys, count = set(), 0
    for sec in t.get("sections") or []:
        for f in sec.get("fields") or []:
            count += 1
            k = f.get("key")
            if not isinstance(k, str) or not re.fullmatch(r"[a-z0-9_.]{1,100}", k):
                errs.append(f"Bad field key {k!r}.")
            elif k in keys:
                errs.append(f"Duplicate field key '{k}'.")
            keys.add(k)
            if f.get("type") not in FIELD_TYPES:
                errs.append(f"Field '{k}' has unknown type {f.get('type')!r}.")
            if f.get("type") in ("enum", "multi_enum") and not f.get("options"):
                errs.append(f"Field '{k}' needs options.")
    if count == 0 and not t.get("tables"):
        errs.append("Add at least one field or table.")
    if count > MAX_FIELDS:
        errs.append(f"At most {MAX_FIELDS} fields.")
    for tbl in t.get("tables") or []:
        for c in tbl.get("columns") or []:
            if c.get("pattern") is not None:
                errs.append("Column patterns cannot be set through the API.")
    for sec in t.get("sections") or []:
        for f in sec.get("fields") or []:
            if f.get("pattern") is not None:
                errs.append("Field patterns cannot be set through the API.")
    tables = {tbl.get("key"): {c.get("key") for c in tbl.get("columns") or []} for tbl in t.get("tables") or []}
    for name, spec in (t.get("canonical") or {}).items():       # links to other documents: structure checked, no regexes
        if not isinstance(spec, dict) or spec.get("kind") not in CANONICAL_KINDS:
            errs.append(f"Link '{name}' needs a kind: one of {', '.join(sorted(CANONICAL_KINDS))}.")
            continue
        if "pattern" in spec:
            errs.append(f"Link '{name}': patterns cannot be set through the API.")
        if spec["kind"] == "set":
            if spec.get("column") not in tables.get(spec.get("table"), set()):
                errs.append(f"Link '{name}' points at a table column that does not exist.")
        elif not spec.get("fields") or not set(spec["fields"]) <= keys:
            errs.append(f"Link '{name}' points at fields that do not exist.")
    for rule in t.get("rules") or []:
        if not isinstance(rule, dict) or rule.get("type") != "date_order" or not {rule.get("start"), rule.get("end")} <= keys:
            errs.append("A rule must be a date_order between two existing fields.")
    return errs


def save_custom_type(workspace_id: str, template: Dict[str, Any], *, replace: bool = False) -> str:
    """Add a document type to one workspace. Raises ValueError with the reasons when the template is not valid, and
    FileExistsError when the workspace already has a type of that name (unless `replace`)."""
    from app.docproc import db
    errs = validate_template(template)
    if errs:
        raise ValueError("; ".join(errs))
    doc_type = template["doc_type"]
    # defaults the pipeline relies on; a template saved through the API may omit them
    template = {"single_page": False, "canonical": {}, "rules": [], "tables": [], **template, "template_id": f"custom-{doc_type}"}
    # where a field sits on the blank form is for the draft screen only; it does not belong in the stored template
    template["sections"] = [{"header": None, **s, "fields": [{k: v for k, v in f.items() if k not in ("bbox", "page")} for f in s["fields"]]}
                            for s in template["sections"]]
    entry = _entry(doc_type, template["title"])  # rebuilt from the template: callers cannot inject patterns
    with db.session() as s:
        row = s.query(db.CustomType).filter_by(workspace_id=workspace_id, doc_type=doc_type).first()
        if row and not replace:
            raise FileExistsError(f"This workspace already has a document type named '{template['title']}'. Choose another name.")
        if row:
            row.title, row.template, row.entry = template["title"], template, entry
        else:
            s.add(db.CustomType(workspace_id=workspace_id, doc_type=doc_type, title=template["title"], template=template, entry=entry))
        s.commit()
    return doc_type


def delete_custom_type(workspace_id: str, doc_type: str) -> bool:
    from app.docproc import db
    with db.session() as s:
        row = s.query(db.CustomType).filter_by(workspace_id=workspace_id, doc_type=doc_type).first()
        if not row:
            return False
        s.delete(row)
        s.commit()
        return True


def locate_fields(path: str, template: Dict[str, Any]) -> None:
    """Add `page` and `bbox` (where its label sits on the blank form) to each drafted field, so the draft screen can show
    exactly what was found. Section headings narrow the search, so "First Name" in the member block and in the provider
    block land in their own places. Fields it cannot place are left without a location."""
    import tempfile
    from pathlib import Path
    from app.docproc.services.extraction import form_extractor
    from app.docproc.services.reading import locate

    n = pdf_pages.page_count(path)
    with tempfile.TemporaryDirectory() as tmp:
        work = str(Path(tmp) / "blank.work.pdf")
        pdf_pages.build_work_pdf(path, work, pdf_pages.scanned_pages(path))
        bands = {p: form_extractor._bands(work, p, template) for p in range(1, n + 1)}
        for sec in template["sections"]:
            for f in sec["fields"]:
                for page in range(1, n + 1):
                    if sec.get("header") and sec["id"] not in bands[page]:
                        continue                                  # this section's heading is not on this page
                    band = bands[page].get(sec["id"], (0.0, 1e9))
                    hit = locate.find_with_text(work, page, f["label"], None, band)
                    if hit and hit[2] >= 0.85:
                        f["page"], f["bbox"] = page, hit[0]
                        break
