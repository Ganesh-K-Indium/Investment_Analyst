"""
Give a vision model's reading of a page the positions of Tesseract's reading of the same page.

A vision model returns good text and no positions; Tesseract returns weaker text and exact positions. Matching the two
line by line, by their text, gives the better words and the real layout, with no extra model call: the page was already
read by the model, and Tesseract's layer is already part of every scanned page.

The match is strict. A line borrows positions only when it matches a Tesseract cell (or a short run of neighbouring
cells) well; a page is accepted only when nearly all of its text found a place. Otherwise None is returned and the caller builds the structure from the
text alone, which is honest about having no positions instead of guessing them.
"""
import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

Cell = Tuple[str, float, float, float, float]       # text, x0, y0, x1, y1 as fractions of the page

MIN_SIMILARITY = 0.62      # a vision line and a Tesseract row below this are different lines
MIN_COVERAGE = 0.85        # share of the page's characters that must find a row
_ALNUM = re.compile(r"[^a-z0-9]")


def _flat(text: str) -> str:
    return _ALNUM.sub("", text.lower())


def _similarity(a: str, b: str) -> float:
    if len(a) < 3 or len(b) < 3:
        return 1.0 if a == b and a else 0.0
    sm = SequenceMatcher(None, a, b, autojunk=False)
    if sm.real_quick_ratio() < MIN_SIMILARITY or sm.quick_ratio() < MIN_SIMILARITY:
        return 0.0
    return sm.ratio()


def _spread(text: str, row: List[Cell]) -> List[Cell]:
    """The vision line's words on the Tesseract row's cells: each word goes to the cell of the Tesseract word it matches,
    and a word with no match follows the word before it. A cell that gets no words is dropped."""
    words = [w for w in text.replace("|", " ").split() if w]
    if not words:
        return []
    ref: List[Tuple[str, int]] = [(_flat(w), k) for k, c in enumerate(row) for w in c[0].split()]
    sm = SequenceMatcher(None, [_flat(w) for w in words], [t for t, _ in ref], autojunk=False)
    cell_of: List[Optional[int]] = [None] * len(words)
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            cell_of[a + k] = ref[b + k][1]
    last = None
    for k in range(len(words)):                                  # unmatched words follow the word before them
        if cell_of[k] is None:
            cell_of[k] = last
        else:
            last = cell_of[k]
    first = next((c for c in cell_of if c is not None), 0)
    cell_of = [first if c is None else c for c in cell_of]
    for k in range(1, len(words)):                               # keep the order of cells: a word cannot go back a cell
        cell_of[k] = max(cell_of[k], cell_of[k - 1])  # type: ignore[type-var]
    out: List[Cell] = []
    for k, c in enumerate(row):
        mine = [w for w, ci in zip(words, cell_of) if ci == k]
        if mine:
            out.append((" ".join(mine), c[1], c[2], c[3], c[4]))
    return out


MAX_RUN = 8                # a vision line may cover up to this many neighbouring cells of one row (a whole row)


MAX_LINES = 500            # pages with more lines than this are not matched
MAX_ROWS = 6               # ...or a run of this many rows, or cells stacked this deep in one column
STACK_TOL = 0.02           # cells in a column share their left edge within this (page width)

Pairs = Tuple[Tuple[int, int], ...]          # (row, cell) of each reference cell a vision line stands for


def _candidates(rows: List[List[Cell]]) -> List[Tuple[Pairs, str]]:
    """What one vision line may stand for, as (the reference cells, their flat text): a cell or a run of neighbouring cells
    in one row; a run of whole rows (a paragraph the model joined into one line); or cells stacked down one column (a
    paragraph or title that sits beside something else, which Tesseract keeps in the same rows)."""
    out: List[Tuple[Pairs, str]] = []

    def add(pairs: Pairs) -> None:
        flat = _flat(" ".join(rows[r][k][0] for r, k in pairs))
        if len(flat) >= 3:
            out.append((pairs, flat))

    for i, row in enumerate(rows):
        for a in range(len(row)):
            for b in range(a, min(len(row), a + MAX_RUN)):
                add(tuple((i, k) for k in range(a, b + 1)))
            chain = [(i, a)]                                    # cells stacked under this one, in a column
            for j in range(i + 1, min(len(rows), i + MAX_ROWS)):
                near = [k for k, c in enumerate(rows[j]) if abs(c[1] - row[a][1]) <= STACK_TOL]
                if not near:
                    break
                chain.append((j, near[0]))
                add(tuple(chain))
        pairs: List[Tuple[int, int]] = [(i, k) for k in range(len(row))]
        for j in range(i + 1, min(len(rows), i + MAX_ROWS)):
            pairs += [(j, k) for k in range(len(rows[j]))]
            add(tuple(pairs))
    return out


WINDOW = 0.15              # a line is first compared with the reference rows this share of the page either side of where it should be


def _score(vision: List[str], flat: List[str], cands: List[Tuple[Pairs, str]], m: int, only: List[int], window: Optional[float]) -> List[Tuple[float, int, int]]:
    n = len(vision)
    by_len = sorted(range(len(cands)), key=lambda ci: len(cands[ci][1]))
    lens = [len(cands[ci][1]) for ci in by_len]
    scored: List[Tuple[float, int, int]] = []
    import bisect
    for vi in only:
        v = flat[vi]
        if len(v) < 3:
            continue
        lo_len, hi_len = int(len(v) / 1.5 - 2), int(len(v) * 1.5 + 2)       # about the same length: the cheap test, as a range
        for k in range(bisect.bisect_left(lens, lo_len), bisect.bisect_right(lens, hi_len)):
            ci = by_len[k]
            pairs, t = cands[ci]
            if window is not None and abs(pairs[0][0] / m - vi / n) > window:
                continue
            if abs(len(v) - len(t)) > max(len(v), len(t)) * 0.5 + 2:
                continue
            sim = _similarity(v, t)
            if sim >= MIN_SIMILARITY:
                # where on the page it sits breaks ties between repeated text ("Yes", the same row twice)
                scored.append((sim - 0.03 * abs(pairs[0][0] / m - vi / n) + len(t) * 1e-5, vi, ci))
    return scored


def _assign(vision: List[str], rows: List[List[Cell]]) -> List[Optional[Pairs]]:
    """Each vision line's reference cells, or None. Best matches first, each cell used once, in any order, because a
    vision model lists a side-by-side layout column by column while Tesseract reads it row by row. Lines are compared with
    the rows near where they belong first (a page of hundreds of lines would otherwise take minutes); what that leaves
    unmatched is compared with the whole page."""
    flat = [_flat(t) for t in vision]
    cands = _candidates(rows)
    n, m = len(vision), max(len(rows), 1)
    out: List[Optional[Pairs]] = [None] * n
    used: set = set()

    def settle(scored: List[Tuple[float, int, int]]) -> None:
        scored.sort(reverse=True)
        for _, vi, ci in scored:
            if out[vi] is not None:
                continue
            pairs = cands[ci][0]
            if used & set(pairs):
                continue
            used.update(pairs)
            out[vi] = pairs

    # text that reads the same in both readings needs no comparing: the nearest unused place for it
    exact: Dict[str, List[int]] = {}
    for ci, (pairs, t) in enumerate(cands):
        exact.setdefault(t, []).append(ci)
    for vi in sorted(range(n), key=lambda i: -len(flat[i])):
        if len(flat[vi]) < 3 or flat[vi] not in exact:
            continue
        free = [ci for ci in exact[flat[vi]] if not used & set(cands[ci][0])]
        if free:
            ci = min(free, key=lambda c: (abs(cands[c][0][0][0] / m - vi / n), -len(cands[c][0])))
            used.update(cands[ci][0])
            out[vi] = cands[ci][0]
    everyone = [vi for vi in range(n) if out[vi] is None]
    settle(_score(vision, flat, cands, m, everyone, WINDOW if n > 40 else None))
    left = [vi for vi in everyone if out[vi] is None and len(flat[vi]) >= 3]
    if left and n > 40:
        settle(_score(vision, flat, cands, m, left, None))
    return out


def _regroup(cells: List[Cell]) -> List[List[Cell]]:
    """Cells back into rows: those that share a line of the page, left to right, top to bottom."""
    if not cells:
        return []
    typical = sorted(c[4] - c[2] for c in cells)[len(cells) // 2]
    rows: List[List[Cell]] = []
    for c in sorted(cells, key=lambda c: ((c[2] + c[4]) / 2, c[1])):
        mid = (c[2] + c[4]) / 2
        if rows and abs(mid - sum((x[2] + x[4]) / 2 for x in rows[-1]) / len(rows[-1])) <= typical * 0.6:
            rows[-1].append(c)
        else:
            rows.append([c])
    return [sorted(r, key=lambda c: c[1]) for r in rows]


def place(vision_lines: List[str], reference: List[List[Cell]]) -> Optional[Tuple[List[List[Cell]], float]]:
    """(rows of cells, share of the text's characters that found a place), or None when the page does not match well
    enough to trust the positions. The words are the vision model's, the boxes are Tesseract's. `reference` is Tesseract's
    rows of cells (Reading.line_cells)."""
    lines = [l for l in vision_lines if _flat(l)]
    if not lines or not reference or len(lines) > MAX_LINES or len(reference) > MAX_LINES:
        return None                                              # a very dense page: matching would be slow, so keep the text-only structure
    hits = _assign(lines, reference)
    total = sum(len(_flat(l)) for l in lines)
    found = sum(len(_flat(l)) for l, h in zip(lines, hits) if h is not None)
    if total == 0 or found / total < MIN_COVERAGE:
        return None
    heights = sorted(c[4] - c[2] for row in reference for c in row)
    height = heights[len(heights) // 2]
    top = min(c[2] for row in reference for c in row)
    placed: List[Cell] = []
    last: Optional[Cell] = None
    for line, hit in zip(lines, hits):
        cells: List[Cell] = []
        if hit is not None:
            run = [reference[r][k] for r, k in hit]
            cells = _spread(line, run) if len(run) > 1 else [(" ".join(line.replace("|", " ").split()), run[0][1], run[0][2], run[0][3], run[0][4])]
        if not cells:
            # a line nothing matched goes just under the line before it, in the same place across, as one cell
            x0, x1 = (last[1], last[3]) if last else (min(c[1] for row in reference for c in row), max(c[3] for row in reference for c in row))
            y0 = last[4] + height * 0.2 if last else max(0.0, top - height * 1.2)
            cells = [(" ".join(line.replace("|", " ").split()), x0, y0, x1, y0 + height)]
        placed += cells
        last = cells[-1]
    return _regroup(placed), found / total
