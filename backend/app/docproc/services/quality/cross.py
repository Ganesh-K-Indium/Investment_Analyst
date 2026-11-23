"""
Cross-document consistency, driven entirely by what each template declares under "canonical".

A template says which of its fields (or table column) carry an idea that other documents may also carry:

    "canonical": {
      "person.dob":  {"kind": "date", "label": "Date of birth", "fields": ["member.dob"]},
      "line_codes":  {"kind": "set",  "label": "Service codes", "table": "cpt", "column": "code", "pattern": "\\d{5}"}
    }

Documents that share a canonical key are compared by its kind:
    name       same set of name words (order and case ignored; one contained in the other counts as a match)
    date       same calendar date
    text       same normalised text
    set        same set of codes (pattern picks the code out of each cell); extras on either side are reported
    mentioned  the value must appear somewhere in the other documents' text (a code quoted in the notes)
Nothing here knows what a patient, a form or a code means, so a new document type needs only a template.
"""
import re
from typing import Any, Dict, List, Optional

from app.docproc.services.extraction import normalize as N


def _val(form: Dict[str, Any], key: str) -> str:
    c = form["fields"].get(key) or {}
    v = c.get("value")
    return str(v).strip() if v not in (None, "", [], False) else ""


def canonical_value(form: Dict[str, Any], spec: Dict[str, Any]) -> Optional[Any]:
    kind = spec["kind"]
    if kind == "set":
        codes = set()
        for row in form["tables"].get(spec["table"], []):
            raw = str((row.get(spec["column"]) or {}).get("value") or "").upper()
            found = re.findall(spec.get("pattern", r"[A-Z0-9]+"), raw)
            codes.update(found if found else ([re.sub(r"[^A-Z0-9]", "", raw)] if raw.strip() else []))
        return codes or None
    joined = " ".join(v for v in (_val(form, k) for k in spec["fields"]) if v)
    if not joined:
        return None
    if kind == "date":
        return N.parse_date(joined)
    if kind == "name":
        return {t for t in re.split(r"[^a-z]+", joined.lower()) if len(t) > 1} or None
    if kind == "mentioned":
        return re.sub(r"[^A-Z0-9]", "", joined.upper()) or None
    return N.normalize(joined, "text")


def _first_field(form: Dict[str, Any], spec: Dict[str, Any]) -> Optional[str]:
    for k in spec.get("fields", []):
        if _val(form, k):
            return k
    return (spec.get("fields") or [None])[0]


def check(forms: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`forms`: [{doc_index, doc_type, pages, fields, tables, text, template}] for every extracted document."""
    out: List[Dict[str, Any]] = []
    keys: Dict[str, Dict[str, Any]] = {}
    for f in forms:
        for k, spec in (f["template"].get("canonical") or {}).items():
            keys.setdefault(k, spec)
    for k, spec in keys.items():
        have = [(f, canonical_value(f, f["template"]["canonical"][k])) for f in forms if k in (f["template"].get("canonical") or {})]
        have = [(f, v) for f, v in have if v]
        label = spec.get("label", k)
        where = lambda f: f"{f['doc_type'].replace('_', ' ')} (p{f['pages'][0]}-{f['pages'][-1]})"
        kind = spec["kind"]
        if kind == "mentioned":
            for f, v in have:
                for g in forms:
                    if g is f or k in (g["template"].get("canonical") or {}):
                        continue
                    if v not in re.sub(r"[^A-Z0-9]", "", (g.get("text") or "").upper()):
                        out.append({"rule": "cross_not_mentioned", "severity": "warn", "doc_index": f["doc_index"],
                                    "field": _first_field(f, f["template"]["canonical"][k]),
                                    "detail": f"{label} {_val(f, f['template']['canonical'][k]['fields'][0])} from the {where(f)} does not appear in the {where(g)}."})
            continue
        for ai in range(len(have)):
            for bi in range(ai + 1, len(have)):
                (fa, va), (fb, vb) = have[ai], have[bi]
                if kind == "set":
                    only_a, only_b = sorted(va - vb), sorted(vb - va)
                    if only_a:
                        out.append({"rule": "cross_set_diff", "severity": "warn", "doc_index": fa["doc_index"],
                                    "detail": f"{label} in the {where(fa)} but not in the {where(fb)}: {', '.join(only_a)}."})
                    if only_b:
                        out.append({"rule": "cross_set_diff", "severity": "info", "doc_index": fb["doc_index"],
                                    "detail": f"{label} in the {where(fb)} but not in the {where(fa)}: {', '.join(only_b)}."})
                    continue
                same = (va <= vb or vb <= va) if kind == "name" else va == vb
                if not same:
                    show = lambda f, v: (", ".join(sorted(v)) if isinstance(v, set) else str(v))
                    out.append({"rule": "cross_mismatch", "severity": "error", "doc_index": fa["doc_index"],
                                "field": _first_field(fa, fa["template"]["canonical"][k]),
                                "detail": f"{label} is {show(fa, va)} in the {where(fa)} but {show(fb, vb)} in the {where(fb)}."})
    return out
