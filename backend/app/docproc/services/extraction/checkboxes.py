"""
Checkbox state without asking a language model.

1. Text glyph: a checked/empty box glyph immediately before the option label (born-digital forms and good OCR layers).
2. Pixels: ink inside the square left of the label's word box (scans / drawn boxes), relative to its siblings.
Anything neither can decide is returned as None so the caller can fall back and flag it for review.
"""
import re
from typing import Dict, List, Optional, Tuple

import pymupdf as fitz

from app.docproc.services.reading.pdf_pages import FITZ_LOCK, open_pdf

CHECKED = "☑☒✔✓■"
EMPTY = "☐□"
_GLYPH = f"[{re.escape(CHECKED + EMPTY)}]"


def glyph_state(text: str, label: str) -> Optional[bool]:
    probe = re.escape(label[:30]).replace(r"\ ", r"\s+")
    m = re.search(rf"({_GLYPH})\s*{probe}", text, re.I)
    if not m:
        return None
    return m.group(1) in CHECKED


def _binary(pix: "fitz.Pixmap", x0: int, y0: int, x1: int, y1: int):
    """Dark-pixel grid for a window, as a list of rows of 0/1."""
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(x1, pix.width), min(y1, pix.height)
    s, n = pix.samples, pix.n
    return x0, y0, [[1 if s[y * pix.stride + x * n] < 195 else 0 for x in range(x0, x1)] for y in range(y0, y1)]


def _components(grid):
    """8-connected components of 1s as (x0, y0, x1, y1, pixel_count)."""
    h, w = len(grid), len(grid[0]) if grid else 0
    seen = [[False] * w for _ in range(h)]
    out = []
    for sy in range(h):
        for sx in range(w):
            if grid[sy][sx] and not seen[sy][sx]:
                stack, seen[sy][sx] = [(sx, sy)], True
                x0 = x1 = sx
                y0 = y1 = sy
                count = 0
                while stack:
                    x, y = stack.pop()
                    count += 1
                    x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            nx, ny = x + dx, y + dy
                            if 0 <= nx < w and 0 <= ny < h and grid[ny][nx] and not seen[ny][nx]:
                                seen[ny][nx] = True
                                stack.append((nx, ny))
                out.append((x0, y0, x1, y1, count))
    return out


def find_box(pix: "fitz.Pixmap", label: "fitz.Rect", zoom: float) -> Optional[Tuple[float, float]]:
    """Find the checkbox drawn just left of `label` -> (distance_to_label, inner_ink), or None if no box is there.

    A box is a dark outline, roughly square, about as tall as the text, touching nothing else and sitting right
    next to the label. Letters of neighbouring words and stray fax specks fail one of those tests.
    """
    H = label.height * zoom
    lx0 = label.x0 * zoom
    gx0, gy0, grid = _binary(pix, int(lx0 - 2.4 * H), int(label.y0 * zoom - .5 * H), int(lx0 + .15 * H), int(label.y1 * zoom + .5 * H))
    best = None
    for cx0, cy0, cx1, cy1, _ in _components(grid):
        w, h = cx1 - cx0 + 1, cy1 - cy0 + 1
        if not (.6 * H <= w <= 1.6 * H and .6 * H <= h <= 1.6 * H and .75 <= w / h <= 1.33):
            continue
        edge = sum(grid[cy0][x] + grid[cy1][x] for x in range(cx0, cx1 + 1)) + sum(grid[y][cx0] + grid[y][cx1] for y in range(cy0, cy1 + 1))
        if edge / (2 * (w + h)) < .3:  # outline must be mostly present on all four sides (faxed lines break up)
            continue
        dist = (lx0 - (gx0 + cx1)) / H
        if not -.15 <= dist <= 1.3:
            continue
        mx, my = int(w * .26), int(h * .26)
        inner = [grid[y][x] for y in range(cy0 + my, cy1 - my + 1) for x in range(cx0 + mx, cx1 - mx + 1)]
        ink = sum(inner) / len(inner) if inner else 0.0
        if best is None or dist < best[0]:
            best = (dist, ink)
    return best


def _candidates(page, option: str, longer: List[str], lo: float, hi: float):
    """Occurrences of `option` on the page, minus those that are really part of a longer label ('Outpatient' inside
    'Outpatient Hospital') and outside the band."""
    rects = [r for r in page.search_for(option[:30]) if lo <= r.y0 <= hi]
    for big in longer:
        if big.lower() != option.lower() and option.lower() in big.lower():
            inside = page.search_for(big[:30])
            rects = [r for r in rects if not any(b.intersects(r) for b in inside)]
    return rects


def pixel_states(path: str, page_num: int, labels: Dict[str, Tuple[float, float]],
                 others: Optional[List[str]] = None) -> Dict[str, Optional[bool]]:
    """labels: option -> (y_min, y_max) search band. Returns option -> checked? (None when no occurrence of the label
    has a checkbox beside it, e.g. the word appears only in running text). `others` are labels from other groups,
    used to tell 'Outpatient' from 'Outpatient Hospital'."""
    zoom = 3.0
    longer = list(labels) + list(others or [])
    out: Dict[str, Optional[bool]] = {o: None for o in labels}
    with FITZ_LOCK, open_pdf(path) as pdf:
        page = pdf[page_num - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY)
        for opt, (lo, hi) in labels.items():
            found = [b for b in (find_box(pix, r, zoom) for r in _candidates(page, opt, longer, lo, hi)) if b]
            if found:
                out[opt] = min(found)[1] >= 0.12
    return out
