"""
Find where a value sits on a page (for highlighting) and whether the page's own text agrees with it.

Works on any PDF with a text layer, including the Tesseract layer of a scan. OCR is imperfect, so after an exact
search fails we slide a window over the page's words and accept a close match, never a loose one.
"""
import re
from difflib import SequenceMatcher
from functools import lru_cache
from typing import List, Optional, Tuple

from app.docproc.services.reading import pdf_pages

FUZZY_MIN = 0.82
_ALNUM = re.compile(r"[^a-z0-9]")


def alnum(s: str) -> str:
    return _ALNUM.sub("", (s or "").lower())


@lru_cache(maxsize=64)
def _words(path: str, mtime: float, page_num: int) -> Tuple[Tuple[float, float, float, float, str], ...]:
    import pymupdf as fitz
    with pdf_pages.FITZ_LOCK, pdf_pages.open_pdf(path) as pdf:
        return tuple((w[0], w[1], w[2], w[3], w[4]) for w in pdf[page_num - 1].get_text("words") if w[4].strip())


def page_words(path: str, page_num: int):
    import os
    return _words(path, os.path.getmtime(path), page_num)


def page_alnum(path: str, page_num: int) -> str:
    return alnum("".join(w[4] for w in page_words(path, page_num)))


def _union(ws) -> List[float]:
    return [round(min(w[0] for w in ws), 2), round(min(w[1] for w in ws), 2),
            round(max(w[2] for w in ws), 2), round(max(w[3] for w in ws), 2)]


def _in_band(w, band: Tuple[float, float]) -> bool:
    return band[0] <= w[1] <= band[1]


def find(path: str, page_num: int, value: str, label: Optional[str] = None,
         band: Tuple[float, float] = (0.0, 1e9)) -> Optional[List[float]]:
    hit = find_with_text(path, page_num, value, label, band)
    return hit[0] if hit else None


def find_with_text(path: str, page_num: int, value: str, label: Optional[str] = None,
                   band: Tuple[float, float] = (0.0, 1e9)) -> Optional[Tuple[List[float], str, float]]:
    """(bbox [x0, y0, x1, y1], the page's own text there, similarity 0-1) for `value` within the vertical `band`, or
    None. When the value occurs more than once (the same Tax ID in two blocks) the occurrence nearest the field's
    `label` wins. The page text is what Tesseract (or the PDF) actually holds, so a caller can tell an exact match
    from a near one."""
    words = [w for w in page_words(path, page_num) if _in_band(w, band)]
    target = alnum(value)
    if not words or not target:
        return None
    k = max(1, len(value.split()))
    hits: List[Tuple[float, List[float]]] = []
    for size in sorted({max(1, k - 1), k, k + 1}):
        for i in range(0, len(words) - size + 1):
            win = words[i:i + size]
            if abs(win[0][1] - win[-1][1]) > 6 * max(1.0, win[0][3] - win[0][1]):  # words of one value share a line (or two)
                continue
            got = alnum("".join(w[4] for w in win))
            if not got:
                continue
            score = 1.0 if got == target else SequenceMatcher(None, got, target, autojunk=False).ratio()
            if score >= (1.0 if len(target) < 4 else FUZZY_MIN):
                hits.append((score, _union(win), " ".join(w[4] for w in win)))
    if not hits:
        return None
    top = max(h[0] for h in hits)
    best = [h for h in hits if h[0] >= top - 1e-9]
    if label and len(best) > 1:
        lw = [w for w in words if alnum(w[4]) and alnum(w[4]) in alnum(label.split("(")[0])][:6]
        if lw:
            best.sort(key=lambda h: min(abs(h[1][1] - w[1]) * 3 + abs(h[1][0] - w[2]) for w in lw))
    return best[0][1], best[0][2], best[0][0]


def in_order(path: str, page_num: int, region: List[float], values: List[str]) -> List[Optional[List[float]]]:
    """Where each of `values` (a table's cells, row by row) is printed inside `region`. A value is looked for after the
    previous one, so the fifth "0.00" of a table is the fifth one on the page, not the first. A value not found in order
    is looked for anywhere in the region; one not there at all gets None."""
    x0, y0, x1, y1 = region
    ws = [w for w in page_words(path, page_num) if x0 - 2 <= (w[0] + w[2]) / 2 <= x1 + 2 and y0 - 2 <= (w[1] + w[3]) / 2 <= y1 + 2]
    ws.sort(key=lambda w: (round((w[1] + w[3]) / 8), w[0]))
    def norm(t: str) -> str:
        return alnum(t) or "".join(t.split())              # "%" or "-" has no letters or digits: compare it as printed
    toks = [norm(w[4]) for w in ws]

    def match(v: str, start: int) -> Optional[Tuple[int, int]]:
        target = norm(v)
        if not target:
            return None
        for i in range(start, len(ws)):
            acc = ""
            for j in range(i, min(len(ws), i + 8)):
                acc += toks[j]
                if acc == target:
                    return i, j + 1
                if not target.startswith(acc):
                    break
        return None

    out: List[Optional[List[float]]] = []
    cursor = 0
    for v in values:
        hit = match(v, cursor) or (match(v, 0) if v.strip() else None)
        if hit and match(v, cursor):
            cursor = hit[1]
        out.append(_union(ws[hit[0]:hit[1]]) if hit else None)
    return out


def beside(path: str, page_num: int, label: str, reach: float = 0.45) -> Optional[List[float]]:
    """The strip on the page to the right of the printed `label`, on its line: where its value is written. Used for a value the page's text
    does not contain (handwriting, a mark): the highlight cannot be exact, but it points at the right place. None when the label is not found."""
    box = find(path, page_num, label.rstrip(": ")) if label and len(alnum(label)) >= 3 else None
    ws = page_words(path, page_num)
    if not box or not ws:
        return None
    right = max(w[2] for w in ws)
    left = max(w[0] for w in ws)
    x0 = box[2] + 2
    x1 = min(right, x0 + max(120.0, reach * max(1.0, right - left)))
    return [round(x0, 2), round(box[1] - 1, 2), round(x1, 2), round(box[3] + 1, 2)] if x1 - x0 > 20 else None
