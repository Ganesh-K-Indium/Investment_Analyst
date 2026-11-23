"""
Generic 'Label: value' reader for documents without a fixed template (clinical notes, cover sheets), plus the aliases
that map their labels onto the canonical keys used for cross-document checks.
"""
import re
from typing import Any, Dict, List

_LABEL_RE = re.compile(r"(?<![\w/])([A-Z][A-Za-z/#()\.\-]*(?: [A-Za-z/#()\.\-]+){0,4}?):\s+")

ALIASES = {
    "patient name": "patient.name",
    "date of birth": "patient.dob",
    "dob": "patient.dob",
    "diagnosis": "diagnosis",
    "treatment diagnosis": "treatment_diagnosis",
    "referring physician/npp": "referring_physician",
    "referring physician": "referring_physician",
    "visit no.": "visit_no",
    "insurance name": "insurance",
    "date of initial examination": "exam_date",
    "date of daily note": "note_date",
    "document date": "document_date",
    "injury/onset/change of status date": "onset_date",
    "surgery": "surgery",
    "treatment side": "treatment_side",
    "pain location": "pain_location",
}


_KNOWN_RE = re.compile(r"(?<![\w/])(" + "|".join(re.escape(k) for k in sorted(ALIASES, key=len, reverse=True)) + r"):\s+", re.I)


def _anchors(line: str):
    """Label matches in a line. Known labels win: a value's trailing words ("Morgan Taylor") must not be read as the
    start of the next label ("Taylor Date of Initial Examination")."""
    known = list(_KNOWN_RE.finditer(line))
    spans = [m.span() for m in known]
    generic = [m for m in _LABEL_RE.finditer(line)
               if not any(m.start() < e and s < m.end() for s, e in spans)]
    return sorted(known + generic, key=lambda m: m.start())


def parse_kv(lines: List[str]) -> List[Dict[str, Any]]:
    """Every 'Label: value' pair, left to right, a value ending where the next label begins."""
    pairs = []
    for ln, line in enumerate(lines):
        matches = _anchors(line)
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
            # a value stops at the next column: " | " is where the reading found a gap, not part of the value
            value = line[m.end():end].split(" | ")[0].strip(" |\t")
            if value:
                pairs.append({"label": m.group(1).strip(), "value": value, "line": ln})
    return pairs


def canonical(pairs: List[Dict[str, Any]]) -> Dict[str, str]:
    """First value seen for each aliased label (header fields repeat on every page)."""
    out: Dict[str, str] = {}
    for p in pairs:
        key = ALIASES.get(p["label"].lower())
        if key and key not in out:
            out[key] = p["value"]
    return out


_CPT_RE = re.compile(r"\bGP:(\d{5})\b")


def cpt_codes(text: str) -> List[str]:
    return sorted(set(_CPT_RE.findall(text or "")))
