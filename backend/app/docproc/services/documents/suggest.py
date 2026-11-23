"""
Notice when untemplated documents keep having the same shape, and offer to make that shape a template.

Nobody has to decide up front which document types exist: files are read with no template first. When a workspace
accumulates several documents whose fields match (same canonical keys, see labels.py), that is a recurring type, and
saving it as a template makes the next one faster, validated and stable. The suggestion carries the extractions it
came from; the existing "promote" endpoint turns one of them into the template.
"""
from collections import Counter
from typing import Any, Dict, List

from app.docproc.services.extraction import labels

MIN_FIELDS = 3        # a "shape" of one or two keys is not a document type
MAX_PAGES = 10        # a template describes a form or a short document, not a 68-page filing
MAX_KEYS = 80
MIN_DOCUMENTS = 2     # recurring means at least twice
SIMILARITY = 0.6      # share of keys two documents must have in common (Jaccard)


def signature(form: Dict[str, Any]) -> frozenset:
    """The shape of one discovered document: its field keys and its table columns (page-suffixed repeats excluded)."""
    keys = {k.split(".")[-1] for k, c in form.get("fields", {}).items()
            if c.get("type") != "checkbox" and not _is_page_repeat(k)}
    for t, cols in (form.get("table_columns") or {}).items():
        keys.add(f"table:{t}")
        keys.update(f"col:{t}:{labels.key(c)}" for c in cols if c)          # a table's columns are part of the layout
    return frozenset(keys)


def _is_page_repeat(key: str) -> bool:
    head, _, tail = key.rpartition("_p")
    return bool(head) and tail.isdigit()


def _similar(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def recurring_shapes(rows: List[Dict[str, Any]], existing_titles: List[str] = ()) -> List[Dict[str, Any]]:
    """`rows`: {"extraction_id", "filename", "forms": [...]} for finished extractions. Returns suggestions, biggest first."""
    clusters: List[Dict[str, Any]] = []
    for row in rows:
        for form in row.get("forms") or []:
            if not form.get("discovered") or len(form.get("pages") or []) > MAX_PAGES:
                continue
            sig = signature(form)
            if not MIN_FIELDS <= len(sig) <= MAX_KEYS:
                continue
            home = next((c for c in clusters if _similar(c["sig"], sig) >= SIMILARITY), None)
            if home is None:
                home = {"sig": sig, "members": [], "titles": Counter()}
                clusters.append(home)
            else:
                home["sig"] = home["sig"] | sig       # the cluster's shape grows to every key any member had
            sha = row.get("sha")
            if sha and any(m.get("sha") == sha for m in home["members"]):
                continue                              # the same file uploaded again is not a second example of a layout
            home["members"].append({"extraction_id": row["extraction_id"], "doc_index": form["doc_index"], "filename": row["filename"], "sha": sha})
            if form.get("label"):
                home["titles"][form["label"]] += 1
    taken = {t.strip().lower() for t in existing_titles}
    out = []
    for c in clusters:
        title = c["titles"].most_common(1)[0][0] if c["titles"] else None
        if len(c["members"]) < MIN_DOCUMENTS or (title and title.strip().lower() in taken):
            continue
        out.append({"title": title, "count": len(c["members"]), "fields": sorted(k for k in c["sig"] if not k.startswith(("table:", "col:"))),
                    "tables": sorted(k[6:] for k in c["sig"] if k.startswith("table:")), "examples": [{k: v for k, v in m.items() if k != "sha"} for m in c["members"][:5]]})
    return sorted(out, key=lambda s: -s["count"])
