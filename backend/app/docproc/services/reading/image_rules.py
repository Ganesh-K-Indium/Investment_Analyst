"""Printed lines found in a page picture.

A born-digital page keeps its table rules as drawing commands; a scan has only pixels. The long thin dark shapes in the picture are
the same rules, so they are turned back into line segments (in PDF points) and handed to the grid reader in drawn_grids.py, which
cuts them into cells exactly as it does for strokes drawn in the file. Found with OpenCV morphology: a rule survives an opening with
a long thin kernel, text does not. Without OpenCV installed the page simply has no rules (everything else still works)."""
from typing import List, Tuple

MIN_H = 0.03           # a horizontal rule is at least this share of the page width
MIN_V = 0.03           # a vertical rule is at least this share of the page height
MAX_THICK = 0.006      # a "line" thicker than this share of the page is a filled shape (a bar, a photo), not a rule
GAP = 0.0016           # breaks in a rule up to this share of the page are scanner noise
MAX_SEGS = 4000        # a page with more lines than this is a drawing or noise


def available() -> bool:
    try:
        import cv2  # noqa: F401
        return True
    except Exception:
        return False


def _lines(ink, horizontal: bool, min_len: int, gap: int, max_thick: int) -> "List[List[float]]":
    """[position, start, end] in pixels of every long thin shape in the binary picture `ink` (ink = 255)."""
    import cv2
    import numpy as np
    size = (gap, 1) if horizontal else (1, gap)
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, size))      # bridge scanner dropouts
    size = (min_len, 1) if horizontal else (1, min_len)
    opened = cv2.morphologyEx(closed, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, size))   # only long straight runs survive
    n, labels, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    out: "List[List[float]]" = []
    for i in range(1, n):
        x, y, w, h, _a = stats[i]
        length = w if horizontal else h
        if length < min_len:
            continue
        mask = labels[y:y + h, x:x + w] == i
        # a rule that touches the stroke of a letter is one component with it: the line itself is the rows (or columns) filled along most of its length
        counts = mask.sum(axis=1) if horizontal else mask.sum(axis=0)
        strong = counts >= 0.7 * length
        thick = int(strong.sum())
        if not thick or thick > max_thick:
            continue
        idx = np.flatnonzero(strong)
        pos = (y if horizontal else x) + idx.mean() + 0.5
        sel = mask[idx[0]:idx[-1] + 1].any(axis=0) if horizontal else mask[:, idx[0]:idx[-1] + 1].any(axis=1)
        run = np.flatnonzero(sel)
        start = (x if horizontal else y) + run[0]
        end = (x if horizontal else y) + run[-1] + 1
        if end - start >= min_len:
            out.append([float(pos), float(start), float(end)])
    return out


def find_rules(png: bytes, width_pt: float, height_pt: float) -> "Tuple[List[List[float]], List[List[float]]]":
    """(horizontal, vertical) line segments in PDF points: horizontal [y, x0, x1], vertical [x, y0, y1]. Empty when the page has no rules."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return [], []
    gray = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return [], []
    H, W = gray.shape
    # local threshold: a faint grey rule and a black one both count, and uneven paper does not matter
    ink = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 12)
    if ink.mean() / 255 > 0.35:
        return [], []                                       # a black page
    hs = _lines(ink, True, max(8, int(MIN_H * W)), max(2, int(GAP * W)), max(2, int(MAX_THICK * H)))
    vs = _lines(ink, False, max(8, int(MIN_V * H)), max(2, int(GAP * H)), max(2, int(MAX_THICK * W)))
    if len(hs) + len(vs) > MAX_SEGS:
        return [], []
    sx, sy = width_pt / W, height_pt / H
    return ([[y * sy, x0 * sx, x1 * sx] for y, x0, x1 in hs], [[x * sx, y0 * sy, y1 * sy] for x, y0, y1 in vs])
