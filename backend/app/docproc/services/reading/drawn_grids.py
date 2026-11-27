"""Tables drawn as separate line strokes.

Some PDFs draw every cell edge as its own short line (a vaccine record card, a hand-built form) and PyMuPDF's table finder sees no
table there. Here the strokes are merged into long lines, the lines that touch are grouped into grids, and each grid is cut into
cells at the lines that really run between them. A cell with no line between it and its neighbour is one spanning cell. The result
has the shape _grid_rows expects (col_count, rows[].cells with None for a spanned cell, cells, bbox), so header handling and
text-filling are shared with the ruled-table path."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

TOUCH = 1.5            # pt: lines this close touch
MIN_LINE = 3.0         # pt: shorter strokes are marks, not rules
MAX_GRIDS = 60         # a page with more drawn grids than this is a drawing, not a form


@dataclass
class Row:
    cells: List[Optional[Tuple[float, float, float, float]]]


@dataclass
class Grid:
    bbox: Tuple[float, float, float, float]
    col_count: int
    rows: List[Row]
    cells: List[Tuple[float, float, float, float]] = field(default_factory=list)
    title: Optional[str] = None
    title_bar: Optional[Tuple[float, float, float, float]] = None


Seg = List[float]       # horizontal: [y, x0, x1]; vertical: [x, y0, y1]


def _strokes(page) -> Tuple[List[Seg], List[Seg], List[Tuple[float, float, float, float]]]:
    """(horizontal, vertical, dark filled bars) from the page's drawings."""
    hs: List[Seg] = []
    vs: List[Seg] = []
    bars: List[Tuple[float, float, float, float]] = []

    def line(ax: float, ay: float, bx: float, by: float) -> None:
        if abs(ay - by) < 0.6 and abs(ax - bx) >= MIN_LINE:
            hs.append([(ay + by) / 2, min(ax, bx), max(ax, bx)])
        elif abs(ax - bx) < 0.6 and abs(ay - by) >= MIN_LINE:
            vs.append([(ax + bx) / 2, min(ay, by), max(ay, by)])

    for d in page.get_drawings():
        stroked = d.get("color") is not None and (d.get("width") or 0) > 0
        fill = d.get("fill")
        for it in d["items"]:
            if it[0] == "l" and stroked:
                line(it[1].x, it[1].y, it[2].x, it[2].y)
            elif it[0] == "re":
                r = it[1]
                if r.width < 1.5 and r.height >= MIN_LINE and (fill or stroked):
                    vs.append([(r.x0 + r.x1) / 2, r.y0, r.y1])          # a rule drawn as a thin filled bar
                elif r.height < 1.5 and r.width >= MIN_LINE and (fill or stroked):
                    hs.append([(r.y0 + r.y1) / 2, r.x0, r.x1])
                elif stroked and not fill and r.width >= MIN_LINE and r.height >= MIN_LINE:
                    hs += [[r.y0, r.x0, r.x1], [r.y1, r.x0, r.x1]]       # a cell drawn as an outlined box
                    vs += [[r.x0, r.y0, r.y1], [r.x1, r.y0, r.y1]]
                elif fill and sum(fill[:3]) < 0.9 and r.width > 20 and 6 < r.height < 40:
                    bars.append((r.x0, r.y0, r.x1, r.y1))                # a black title bar
            elif it[0] == "qu":
                pass
    return hs, vs, bars


def _merge(segs: List[Seg]) -> List[Seg]:
    """Strokes on one line, end to end or overlapping, become one."""
    out: List[Seg] = []
    for s in sorted(segs, key=lambda s: (round(s[0] / TOUCH), s[1])):
        if out and abs(out[-1][0] - s[0]) <= 0.8 and s[1] <= out[-1][2] + TOUCH:
            out[-1][2] = max(out[-1][2], s[2])
        else:
            out.append(list(s))
    return out


def _clusters(values: List[float], tol: float = TOUCH) -> List[float]:
    out: List[List[float]] = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(c) / len(c) for c in out]


def _crossings(seg: Seg, others: List[Seg], vertical: bool) -> int:
    """How many of `others` (the lines running the other way) touch `seg`."""
    n = 0
    for o in others:
        if vertical:        # seg is [x, y0, y1]; o is [y, x0, x1]
            if o[1] - TOUCH <= seg[0] <= o[2] + TOUCH and seg[1] - TOUCH <= o[0] <= seg[2] + TOUCH:
                n += 1
        elif seg[1] - TOUCH <= o[0] <= seg[2] + TOUCH and o[1] - TOUCH <= seg[0] <= o[2] + TOUCH:
            n += 1
    return n


def _components(hs: List[Seg], vs: List[Seg]) -> List[Tuple[List[Seg], List[Seg]]]:
    n = len(hs) + len(vs)
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i, h in enumerate(hs):
        for j, v in enumerate(vs):
            if h[1] - TOUCH <= v[0] <= h[2] + TOUCH and v[1] - TOUCH <= h[0] <= v[2] + TOUCH:
                parent[find(i)] = find(len(hs) + j)
    groups: Dict[int, Tuple[List[Seg], List[Seg]]] = {}
    for i, h in enumerate(hs):
        groups.setdefault(find(i), ([], []))[0].append(h)
    for j, v in enumerate(vs):
        groups.setdefault(find(len(hs) + j), ([], []))[1].append(v)
    return list(groups.values())


def _covers(segs: List[Seg], pos: float, lo: float, hi: float) -> bool:
    """Is there a line at `pos` that runs across the span lo..hi (its middle, with a little slack at the ends)?"""
    mid = (lo + hi) / 2
    return any(abs(s[0] - pos) <= TOUCH and s[1] - TOUCH <= mid <= s[2] + TOUCH and s[1] - TOUCH <= lo + (hi - lo) * 0.25 and s[2] + TOUCH >= hi - (hi - lo) * 0.25 for s in segs)


def _grid(hs: List[Seg], vs: List[Seg]) -> Optional[Grid]:
    xs = _clusters([v[0] for v in vs])
    ys = _clusters([h[0] for h in hs])
    if len(xs) < 3 or len(ys) < 3:
        return None
    nc, nr = len(xs) - 1, len(ys) - 1
    # a cell is part of the grid when it has a line above or below it and one on its left or right
    inside = [[(_covers(hs, ys[r], xs[c], xs[c + 1]) or _covers(hs, ys[r + 1], xs[c], xs[c + 1]))
               and (_covers(vs, xs[c], ys[r], ys[r + 1]) or _covers(vs, xs[c + 1], ys[r], ys[r + 1])) for c in range(nc)] for r in range(nr)]
    for r in range(nr):                       # a shaded band with no rules between its labels: the cells between two ruled ones belong to the grid
        got = [c for c in range(nc) if inside[r][c]]
        if len(got) >= 2:
            for c in range(got[0], got[-1]):
                inside[r][c] = True
    parent = {(r, c): (r, c) for r in range(nr) for c in range(nc) if inside[r][c]}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for (r, c) in list(parent):
        if (r, c + 1) in parent and not _covers(vs, xs[c + 1], ys[r], ys[r + 1]):
            parent[find((r, c + 1))] = find((r, c))
        if (r + 1, c) in parent and not _covers(hs, ys[r + 1], xs[c], xs[c + 1]):
            parent[find((r + 1, c))] = find((r, c))
    regions: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    for k in parent:
        regions.setdefault(find(k), []).append(k)
    rows: List[Row] = [Row([None] * nc) for _ in range(nr)]
    cells: List[Tuple[float, float, float, float]] = []
    for members in regions.values():
        r0, r1 = min(m[0] for m in members), max(m[0] for m in members)
        c0, c1 = min(m[1] for m in members), max(m[1] for m in members)
        if len(members) != (r1 - r0 + 1) * (c1 - c0 + 1):
            for r, c in members:                                  # not a rectangle: keep the cells as they are
                b = (xs[c], ys[r], xs[c + 1], ys[r + 1])
                rows[r].cells[c] = b
                cells.append(b)
            continue
        b = (xs[c0], ys[r0], xs[c1 + 1], ys[r1 + 1])
        rows[r0].cells[c0] = b
        cells.append(b)
    rows = [r for r in rows if any(r.cells)]
    if len(rows) < 2 or not cells:
        return None
    x0, y0 = min(c[0] for c in cells), min(c[1] for c in cells)
    x1, y1 = max(c[2] for c in cells), max(c[3] for c in cells)
    return Grid((x0, y0, x1, y1), nc, rows, cells)


def _bar_text(bar, words: List[Any]) -> str:
    return " ".join(w[4] for w in sorted((w for w in words if bar[0] - 1 <= (w[0] + w[2]) / 2 <= bar[2] + 1 and bar[1] - 1 <= (w[1] + w[3]) / 2 <= bar[3] + 1),
                                         key=lambda w: (round(w[1] / 3), w[0]))).strip()


def _clip(hs: List[Seg], vs: List[Seg], lo: float, hi: float) -> Tuple[List[Seg], List[Seg]]:
    """The lines of a grid that lie between y = lo and y = hi (verticals cut to that band)."""
    h2 = [h for h in hs if lo - TOUCH <= h[0] <= hi + TOUCH]
    v2 = [[v[0], max(v[1], lo), min(v[2], hi)] for v in vs if min(v[2], hi) - max(v[1], lo) >= MIN_LINE]
    return h2, v2


def _structure_bands(hs: List[Seg], vs: List[Seg]) -> List[Tuple[float, float]]:
    """Cut a grid into stacked tables where its columns change: rows whose inner rules sit in one set of places belong together, and a
    row band ruled in quite different places starts another table (a specimen box over a results table inside one frame).
    A band with no inner rules at all (a title or section row) joins the table above it. Returns [(top, bottom)], one for an uncut grid."""
    xs, ys = _clusters([v[0] for v in vs]), _clusters([h[0] for h in hs])
    if len(xs) < 4 or len(ys) < 4:
        return [(min(ys, default=0), max(ys, default=0))]
    sigs = []
    for r in range(len(ys) - 1):
        sigs.append(frozenset(i for i in range(1, len(xs) - 1) if _covers(vs, xs[i], ys[r], ys[r + 1])))
    cuts, last = [0], None
    for r, sg in enumerate(sigs):
        if not sg:
            continue
        if last is not None and len(sg & last) / min(len(sg), len(last)) < 0.5:       # a subset is the same table with some cells merged
            top = r
            while top - 1 > cuts[-1] and not sigs[top - 1]:        # the band of labels above its figures has no inner rules: it heads the table below
                top -= 1
            cuts.append(top)
        last = sg
    if len(cuts) <= 1:
        return [(ys[0], ys[-1])]
    bounds = cuts + [len(ys) - 1]
    return [(ys[bounds[k]], ys[bounds[k + 1]]) for k in range(len(bounds) - 1)]


def find_grids(page, words: List[Any], rules: Optional[Tuple[List[Seg], List[Seg]]] = None) -> List[Grid]:
    """The tables drawn on `page` as separate strokes. A dark title bar across a grid cuts it into sections: the bar's text is the
    name of the section under it, and a bar just above a grid names that grid. `rules` are lines found in a scan's picture
    (image_rules.find_rules), used in place of drawing commands the page does not have."""
    hs, vs, bars = _strokes(page)
    if rules:
        hs, vs = hs + [list(h) for h in rules[0]], vs + [list(v) for v in rules[1]]
    if len(hs) + len(vs) < 12:
        return []
    hs, vs = _merge(hs), _merge(vs)
    if rules:                                                 # lines read from a picture include chart bars and tall letters: a table rule crosses at least two others
        for _ in range(2):
            vs = [v for v in vs if _crossings(v, hs, True) >= 2]
            hs = [h for h in hs if _crossings(h, vs, False) >= 2]
    out: List[Grid] = []
    for ch, cv in _components(hs, vs):
        if len(ch) < 3 or len(cv) < 3:
            continue
        cx0, cx1 = min(h[1] for h in ch), max(h[2] for h in ch)
        cy0, cy1 = min(v[1] for v in cv), max(v[2] for v in cv)
        across = sorted((b for b in bars if min(b[2], cx1) - max(b[0], cx0) >= 0.6 * min(b[2] - b[0], cx1 - cx0) and cy0 - 4 <= b[1] and b[3] <= cy1 + 4), key=lambda b: b[1])
        bands: List[Tuple[float, float, Optional[Tuple[float, float, float, float]]]] = []
        if across:
            if across[0][1] - cy0 > 12:
                bands.append((cy0, across[0][1], None))                 # a section above the first bar, with no bar of its own
            for k, b in enumerate(across):
                bands.append((b[3], across[k + 1][1] if k + 1 < len(across) else cy1 + 1, b))
        elif rules:
            bands = [(lo - TOUCH, hi + TOUCH, None) for lo, hi in _structure_bands(ch, cv)]
        else:
            bands.append((cy0, cy1 + 1, None))
        for lo, hi, bar in bands:
            bh, bv = _clip(ch, cv, lo, hi)
            if len(bh) < 3 or len(bv) < 3:
                continue
            g = _grid(bh, bv)
            if g is None:
                continue
            if bar is None:                                             # a bar just above this grid, over most of its width
                for b in bars:
                    if -2 <= g.bbox[1] - b[3] <= 4 and min(b[2], g.bbox[2]) - max(b[0], g.bbox[0]) >= 0.6 * min(b[2] - b[0], g.bbox[2] - g.bbox[0]):
                        bar = b
                        break
            if bar is not None and _bar_text(bar, words):
                g.title, g.title_bar = _bar_text(bar, words), bar
            out.append(g)
            if len(out) > MAX_GRIDS:
                return []
    return out
