"""
Check that the figures in an answer are in the evidence it was written from, and that units and scale line up.

No model is used: a model asked "is this supported?" can be wrong in the same way the answer was. Instead every number in the
answer is converted to an absolute value (using the words around it: "$4.2 billion", "12%") and looked for among the absolute
values in the retrieved chunks, where a figure in a table gets the unit the table states ("(in thousands)", "in millions").

    supported     the value is in the evidence (at the stated scale or as printed)
    scale_mismatch  the printed digits are in the evidence but at a different scale ("1,200 million" vs a table in thousands)
    unsupported   the value is not in the evidence at all (it may be a calculation: it is flagged for a person, not called wrong)

Years, dates, page numbers and small integers (counts under 10, list numbering) are ignored: they are rarely the claim.
"""
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

_SCALE_WORDS = {"thousand": 1e3, "k": 1e3, "million": 1e6, "mn": 1e6, "mm": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9, "trillion": 1e12, "tn": 1e12}
_NUM = re.compile(r"""(?P<neg>[-−(]\s*)?(?P<cur>[$€£¥]\s*)?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?P<pct>%|percent\b)?\s*(?P<scale>thousand|million|billion|trillion|bn|mn|mm|[kmb]\b)?""", re.I)
_UNITS_IN_TEXT = re.compile(r"\(?\bin\s+(thousands|millions|billions)\b|\$?\s*(thousands|millions|billions)\s+of\s+(?:u\.s\.\s+)?(?:dollars|usd)|\((?:amounts\s+)?in\s+(thousands|millions|billions)", re.I)
_SCALE_OF_UNIT = {"thousands": 1e3, "millions": 1e6, "billions": 1e9}
_YEAR = re.compile(r"^(19|20)\d{2}$")


def chunk_unit(text: str) -> float:
    """The scale a chunk states for its figures ("in thousands" -> 1e3), or 1 when it states none."""
    m = _UNITS_IN_TEXT.search(text or "")
    if not m:
        return 1.0
    word = next(g for g in m.groups() if g).lower()
    return _SCALE_OF_UNIT[word]


def extract(text: str) -> List[Dict[str, Any]]:
    """Numbers in `text` with their printed digits, scale word, percent flag and absolute value."""
    out = []
    for m in _NUM.finditer(text or ""):
        raw = m.group("num")
        digits = raw.replace(",", "")
        try:
            val = float(digits)
        except ValueError:
            continue
        scale_word = (m.group("scale") or "").lower()
        scale = _SCALE_WORDS.get(scale_word, 1.0)
        pct = bool(m.group("pct"))
        out.append({"raw": raw, "digits": digits, "value": val * scale, "printed": val, "scale": scale, "pct": pct, "scale_word": scale_word,
                    "currency": bool(m.group("cur")), "span": m.span(), "neg": bool(m.group("neg") and m.group("neg").strip() in "-−")})
    return out


def _ignorable(n: Dict[str, Any], text: str) -> bool:
    if _YEAR.match(n["digits"]) and not n["pct"] and not n["currency"] and not n["scale_word"]:
        return True
    if n["printed"] < 10 and not n["pct"] and not n["currency"] and not n["scale_word"] and "." not in n["digits"]:
        return True
    s, e = n["span"]                                              # "Q3", "10-K", "Item 7", "page 12", "fiscal 2024" style labels
    before = text[max(0, s - 6):s].lower()
    return bool(re.search(r"(item|page|note|figure|table|q|fy|10-)\s*$", before))


def _evidence(chunks: Iterable[str]) -> Tuple[Set[str], List[float], List[Tuple[float, float]]]:
    printed: Set[str] = set()
    absolutes: List[float] = []
    pairs: List[Tuple[float, float]] = []
    for c in chunks:
        unit = chunk_unit(c)
        for n in extract(c):
            printed.add(n["digits"].rstrip("0").rstrip(".") if "." in n["digits"] else n["digits"])
            absolutes.append(n["value"] * (unit if not n["scale_word"] and not n["pct"] else 1.0))
            pairs.append((n["printed"], unit))
    return printed, absolutes, pairs


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 0.005 * max(abs(a), abs(b), 1e-9)


def verify(answer: str, chunks: Iterable[str]) -> Dict[str, Any]:
    """Return {"checked": n, "supported": n, "issues": [{"text", "kind", "detail"}], "status": "verified|issues|nothing_to_check"}."""
    chunks = list(chunks)
    printed, absolutes, pairs = _evidence(chunks)
    issues, checked, supported = [], 0, 0
    for n in extract(answer):
        if _ignorable(n, answer):
            continue
        checked += 1
        key = n["digits"].rstrip("0").rstrip(".") if "." in n["digits"] else n["digits"]
        if n["pct"]:
            ok = key in printed or any(_close(n["printed"], p) for p, _ in pairs if p < 1000)
            if ok:
                supported += 1
            else:
                issues.append({"text": f"{n['raw']}%", "kind": "unsupported", "detail": "this percentage is not in the retrieved documents"})
            continue
        if any(_close(n["value"], a) for a in absolutes) or (not n["scale_word"] and key in printed):
            supported += 1
            continue
        s, e = n["span"]
        shown = answer[s:e].strip()
        if key in printed:
            issues.append({"text": shown, "kind": "scale_mismatch",
                           "detail": "these digits appear in the documents but at a different scale (thousands vs millions vs billions): check the units"})
        else:
            issues.append({"text": shown, "kind": "unsupported", "detail": "this figure is not in the retrieved documents (it may be a calculation)"})
    status = "nothing_to_check" if not checked else ("verified" if not issues else "issues")
    return {"checked": checked, "supported": supported, "issues": issues, "status": status}


def note(result: Dict[str, Any], mode: str) -> Optional[str]:
    """The text appended to (warn) or put in front of (strict) an answer with issues; None when there is nothing to say."""
    if mode == "off" or result["status"] != "issues":
        return None
    lines = [f"- **{i['text']}**: {i['detail']}" for i in result["issues"][:8]]
    head = ("**Not verified against the documents.** Check these figures before relying on this answer:" if mode == "strict"
            else "_Figures to double-check (not found in the retrieved documents):_")
    return head + "\n" + "\n".join(lines)
