"""
Rule checks over one extracted document (cross-document checks live in cross.py). Each finding: {rule, severity: error|warn|info, field?, detail}.
These are what a reviewer reads first, so they should be few, specific and actionable.
"""
import re
from typing import Any, Dict, List

from app.docproc.services.extraction import normalize as N

_ICD10 = re.compile(r"^[A-TV-Z]\d[0-9A-Z](\.?[0-9A-Z]{1,4})?$")


def _f(rule, severity, detail, field=None):
    out = {"rule": rule, "severity": severity, "detail": detail}
    if field:
        out["field"] = field
    return out


def _format_ok(ftype: str, raw: str) -> bool:
    norm = N.normalize(raw, ftype) or ""
    if ftype == "npi":
        return len(norm) == 10
    if ftype == "tax_id":
        return len(norm) == 9
    if ftype == "phone":
        return len(norm) in (10, 11)
    if ftype == "zip":
        return len(norm) in (5, 9)
    if ftype == "state":
        return bool(re.fullmatch(r"[A-Z]{2}", norm))
    if ftype == "icd10":
        return bool(_ICD10.match(norm))
    if ftype == "date":
        return N.parse_date(raw) is not None
    return True


def validate_form(template: Dict[str, Any], fields: Dict[str, Dict[str, Any]], tables: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for sec in template["sections"]:
        for f in sec["fields"]:
            got = fields.get(f["key"], {})
            val = got.get("value")
            empty = val in (None, "", [])
            if f.get("required") and empty:
                out.append(_f("required_missing", "error", f"Required field '{f['label']}' is empty.", f["key"]))
            elif not empty and isinstance(val, str) and f.get("pattern") and not re.fullmatch(f["pattern"], val.strip().upper()):
                out.append(_f("format", "warn", f"'{val}' does not match the expected {f['label']} format.", f["key"]))
            elif not empty and isinstance(val, str) and not _format_ok(f["type"], val):
                out.append(_f("format", "warn", f"'{val}' does not look like a valid {f['type'].replace('_', ' ')}.", f["key"]))
            if got.get("status") == "review":
                out.append(_f("needs_review", "info", f"Readers disagree or are unsure about '{f['label']}'.", f["key"]))
    for rule in template.get("rules", []):  # declared per template, e.g. an end date that must not precede a start date
        if rule["type"] == "date_order":
            a, b = fields.get(rule["start"], {}).get("value"), fields.get(rule["end"], {}).get("value")
            da, db = N.parse_date(a or ""), N.parse_date(b or "")
            if da and db and db < da:
                out.append(_f("date_order", "error", f"{rule.get('label', 'End date')}: {b} is before {a}.", rule["end"]))
    for tbl in template.get("tables", []):
        rows = tables.get(tbl["key"], [])
        if not rows and tbl.get("required"):
            out.append(_f("table_empty", "error", f"No rows found in the {tbl['key'].replace('_', ' ')} table."))
        for i, row in enumerate(rows, 1):
            for col in tbl["columns"]:
                val = (row.get(col["key"], {}).get("value") or "").strip()
                if val and col.get("pattern") and not re.fullmatch(col["pattern"], val.upper()):
                    out.append(_f("format", "warn", f"Row {i}: '{val}' does not match the expected {col['label']} format.", f"{tbl['key']}[{i}].{col['key']}"))
            for col in tbl["columns"]:
                if col.get("required") and not (row.get(col["key"], {}).get("value") or "").strip():
                    out.append(_f("required_missing", "error", f"Row {i}: '{col['label']}' is empty.", f"{tbl['key']}[{i}].{col['key']}"))
    return out


def review_notes(fields: Dict[str, Dict[str, Any]], tables: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Findings for an untemplated extraction. With no template there are no required fields or formats to check, so
    the only honest findings are the places the readers were not sure."""
    out = []
    for key, c in fields.items():
        if c.get("status") in ("review", "undetermined"):
            out.append(_f("needs_review", "info", f"Check '{c.get('label') or key}': {c.get('reason') or 'readers disagree or are unsure'}.", key))
    for tkey, rows in tables.items():
        n = sum(1 for r in rows for c in r.values() if c.get("status") == "review")
        if n:
            out.append(_f("needs_review", "info", f"{n} cell{'s' if n != 1 else ''} in the {tkey.replace('_', ' ')} table need a check."))
    return out
