"""
Figures: pictures, charts, diagrams and drawings on a page. Finding them costs nothing; reading one costs a model call, so it
only happens when somebody asks.

FINDING (no model)
  Digital page   embedded images give exact boxes; charts and drawings are vector graphics, found by clustering what the page
                 draws. A table's ruling lines are drawings too, so a cluster that is mostly a table is not a figure.
  Scanned page   a region with ink that is not text: the page is drawn small, the words the OCR found are painted out, and what
                 ink is left is grouped. A region full of words is a table or a form, not a figure.
  Either way the text printed inside a figure (axis labels, part names, pin names) is kept as `labels`: it is exact on a digital
  page, and it is what a model is told to rely on when it reads the figure.

READING (one model call, only on request)
  One crop of the figure, one model, one reply: not the whole page, and not two readers. The reply says what kind of figure it is
  and gives its data. A value the model worked out from the geometry (the height of a bar) is marked estimated; a value that is
  written on the figure is not, and that claim is checked against the labels found by code. A figure that turns out to be a logo is
  dropped. The same crop is never read twice.
"""
import asyncio
import hashlib
import io
import json
import logging
import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("app.services.figures")

MIN_AREA = 0.012          # a figure covers at least this share of the page
MIN_SIDE_PT = 45.0        # ...and is at least this big (points) on its short side
MAX_AREA = 0.85           # an image over nearly the whole page is the scan itself, not a figure in it
EDGE = 0.12               # a small picture in the top or bottom margin is a logo or a stamp
SMALL_IMAGE = 0.06        # ...small meaning less than this share of the page
CLUSTER_GAP = 12.0        # drawn parts of one chart (axes, plot, legend) are this close or closer
MAX_LABELS = 80
TABLE_OVERLAP = 0.5       # a drawn cluster this much inside a table is the table's rules
MIN_DRAWN = 8             # a drawing has at least this many pieces; a boxed note has a border and little more
DENSE_RULES = 40          # this many straight rules and empty boxes in one drawing is a form, whatever else is drawn among them
MAX_ASPECT = 5.0          # a drawn region more than this many times longer than wide is a band or a rule
LOGO_PAGES = 3            # the same picture on this many pages is a logo or a letterhead

BBox = List[float]


def _area(b: BBox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def overlap(a: BBox, b: BBox) -> float:
    """Share of the smaller box that lies inside the other."""
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    return w * h / max(1e-9, min(_area(a), _area(b)))


def _union(a: BBox, b: BBox) -> BBox:
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _near(a: BBox, b: BBox, gap: float) -> bool:
    return a[0] - gap <= b[2] and b[0] - gap <= a[2] and a[1] - gap <= b[3] and b[1] - gap <= a[3]


def _merge(boxes: List[Dict[str, Any]], gap: float) -> List[Dict[str, Any]]:
    """Boxes that touch or nearly touch become one figure (a chart and its legend; a diagram drawn in several pieces)."""
    boxes = [dict(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                if _near(boxes[i]["bbox"], boxes[j]["bbox"], gap):
                    boxes[i]["bbox"] = _union(boxes[i]["bbox"], boxes[j]["bbox"])
                    if boxes[j].get("source") == "image":
                        boxes[i]["source"] = "image"
                        boxes[i]["digest"] = boxes[i].get("digest") or boxes[j].get("digest")
                    del boxes[j]
                    changed = True
                    break
            if changed:
                break
    return boxes


SATELLITE_REACH = 50.0    # a legend or a note drawn this near a figure, though not touching it, belongs to it
SATELLITE_SHARE = 0.5     # ...when it is this much smaller than the figure


def _attach_satellites(kept: List[Dict[str, Any]], small: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A chart's legend, a diagram's key: drawn apart from the main drawing, too small to be a figure alone. Each one near a figure
    (and much smaller than it) joins it, so the figure's box, and the crop a model reads, take it in."""
    for sat in small:
        near = [k for k in kept if _near(k["bbox"], sat["bbox"], SATELLITE_REACH) and _area(sat["bbox"]) <= SATELLITE_SHARE * _area(k["bbox"])]
        if near:
            best = min(near, key=lambda k: _gap(k["bbox"], sat["bbox"]))
            best["bbox"] = _union(best["bbox"], sat["bbox"])
    return kept


def _gap(a: BBox, b: BBox) -> float:
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return (dx * dx + dy * dy) ** 0.5


def _is_band(b: BBox, w: float, h: float, drawn: int) -> bool:
    """A long thin region that is page furniture: few pieces, or spanning the page, or in the top or bottom margin."""
    long, short = max(b[2] - b[0], b[3] - b[1]), min(b[2] - b[0], b[3] - b[1])
    return long > MAX_ASPECT * max(short, 1.0) and (drawn < BAND_PIECES or (b[2] - b[0]) >= BAND_WIDTH * w or b[3] < h * EDGE or b[1] > h * (1 - EDGE))


def _keep(b: BBox, width: float, height: float) -> bool:
    area = _area(b) / (width * height)
    return MIN_AREA <= area <= MAX_AREA and min(b[2] - b[0], b[3] - b[1]) >= MIN_SIDE_PT


LABEL_REACH = 30.0        # axis labels, titles and part names sit this close to the drawing they belong to
LABEL_CHARS = 36          # ...and are short: a long line next to a chart is the text of the page


def _ruled(draws: List[Any], b: BBox) -> bool:
    """Is what is drawn in `b` a grid of ruling lines or a form's empty boxes, and not a picture? Lines that all run straight across
    or down, or unfilled rectangles, many of them, and nothing else (no bars, curves, slanted lines or colour-filled shapes)."""
    lines = boxes = shapes = 0
    inside = lambda r: b[0] - 2 <= r[0] and r[2] <= b[2] + 2 and b[1] - 2 <= r[1] and r[3] <= b[3] + 2        # noqa: E731
    horizontal = vertical = 0
    for d in draws:
        fill = d.get("fill")
        plain = fill is None or all(c >= 0.93 for c in fill)                     # no fill, or white or nearly: a box to write in
        for it in d["items"]:
            if it[0] == "l":
                a, c = it[1], it[2]
                if not inside([min(a.x, c.x), min(a.y, c.y), max(a.x, c.x), max(a.y, c.y)]):
                    continue
                if abs(a.y - c.y) < 0.5:
                    horizontal += 1
                    lines += 1
                elif abs(a.x - c.x) < 0.5:
                    vertical += 1
                    lines += 1
                else:
                    shapes += 1
            elif it[0] == "re":
                r = it[1]
                if not inside([r.x0, r.y0, r.x1, r.y1]):
                    continue
                if r.width < 1.5:
                    vertical += 1
                    lines += 1
                elif r.height < 1.5:
                    horizontal += 1
                    lines += 1
                elif plain:
                    boxes += 1
                elif r.width > 3 and r.height > 3:
                    shapes += 1
            elif it[0] in ("qu", "c"):
                shapes += 1
    straight = lines + boxes
    if straight >= DENSE_RULES and shapes <= 0.4 * straight:
        return True                                    # a page of form boxes and rules, with a few ticks and circles among them
    return shapes <= 0.15 * max(1, straight) and ((horizontal >= 4 and vertical >= 3) or boxes >= 6 or straight >= 12)


def _drawn(draws: List[Any], b: BBox) -> int:
    """How many pieces (lines, rectangles, curves) are drawn inside `b`: a boxed note has a border and little else."""
    n = 0
    for d in draws:
        r = d.get("rect")
        if r is not None and b[0] - 2 <= r.x0 and r.x1 <= b[2] + 2 and b[1] - 2 <= r.y0 and r.y1 <= b[3] + 2:
            n += len(d["items"])
    return n


ICON_PIECES = 6           # a region made of this many separate small pieces...
ICON_BIGGEST = 0.35       # ...none of which is more than this share of it, is a field of icons
BAND_PIECES = 5           # a long thin region with fewer pieces than this, or one this wide across the page, or one in the margin, is a band
BAND_WIDTH = 0.8          # share of the page width
_UNUSED_BAND = 12          # a long thin region with fewer drawn pieces than this is a band or a rule


def _icon_field(page: Any, b: BBox) -> bool:
    """Is this region many small separate shapes (a grid of icons, a row of bullets, a legend of swatches) and not one drawing? A chart
    is held together by its axes and a diagram by its arrows, so at a tight tolerance each is one large piece; icons stay apart."""
    import pymupdf
    try:
        pieces = [list(r) for r in page.cluster_drawings(clip=pymupdf.Rect(*b), x_tolerance=3, y_tolerance=3)]
    except Exception:
        return False
    if len(pieces) < ICON_PIECES:
        return False
    total = _area(b)
    return max(_area(p) for p in pieces) < ICON_BIGGEST * total


FAR_REACH = 85.0          # a legend entry or a key sits this near the figure, though not touching its drawing
FAR_CHARS = 22            # ...and is only a word or two
_CAPTION_WORD = re.compile(r"^\s*(figure|fig\.?|chart|graph|diagram|plot|table)\b", re.I)


Lines = Dict[Tuple[int, int], List[Tuple[float, float, float, float, str]]]


def _grow(b: BBox, lines: Lines, draws: Optional[List[Any]] = None) -> Tuple[BBox, List[str]]:
    """The drawing's own box grown to take in the short text round it (axis ticks, a title, pin names, a legend) and the little
    drawn marks beside that text (a legend's colour swatch), and all the words inside. A caption is never taken in.
    `lines` are the page's words grouped by (block, line), each word as (x0, y0, x1, y1, text)."""
    box = list(b)
    near = [b[0] - LABEL_REACH, b[1] - LABEL_REACH, b[2] + LABEL_REACH, b[3] + LABEL_REACH]
    block_lines: Dict[int, int] = {}
    for (blk, _ln) in lines:
        block_lines[blk] = block_lines.get(blk, 0) + 1
    words: List[str] = []
    for (blk, _ln), ws in lines.items():
        text = " ".join(w[4] for w in sorted(ws, key=lambda w: w[0]))
        line_box = [min(w[0] for w in ws), min(w[1] for w in ws), max(w[2] for w in ws), max(w[3] for w in ws)]
        if line_box[2] < b[0] - FAR_REACH or line_box[0] > b[2] + FAR_REACH or line_box[3] < b[1] - LABEL_REACH or line_box[1] > b[3] + LABEL_REACH:
            continue
        inside = [w for w in ws if b[0] - 1 <= (w[0] + w[2]) / 2 <= b[2] + 1 and b[1] - 1 <= (w[1] + w[3]) / 2 <= b[3] + 1]
        close = line_box[0] >= near[0] and line_box[2] <= near[2] and line_box[1] >= near[1] and line_box[3] <= near[3]
        if inside and len(text) > LABEL_CHARS:
            take = inside
        elif _CAPTION_WORD.match(text):
            continue
        elif (close and len(text) <= LABEL_CHARS) or (inside and len(text) <= LABEL_CHARS) \
                or (len(text) <= FAR_CHARS and block_lines[blk] <= 3 and line_box[1] >= b[1] - 15 and line_box[3] <= b[3] + 15):
            take = ws
        else:
            continue
        for w in take:
            words.append(w[4])
            box = _union(box, [w[0], w[1], w[2], w[3]])
        if take is ws and not inside:                                  # a legend entry: its swatch is a small drawn mark just beside the text
            lb = [line_box[0] - 16, line_box[1] - 3, line_box[2] + 3, line_box[3] + 3]
            for d in draws or []:
                r = d.get("rect")
                if r is not None and r.width <= 40 and r.height <= 40 and overlap([r.x0, r.y0, r.x1, r.y1], lb) > 0.5:
                    box = _union(box, [r.x0, r.y0, r.x1, r.y1])
    return box, words


def _with_labels(page: Any, b: BBox, draws: Optional[List[Any]] = None) -> Tuple[BBox, List[str]]:
    reach = [b[0] - FAR_REACH, b[1] - LABEL_REACH, b[2] + FAR_REACH, b[3] + LABEL_REACH]
    lines: Lines = {}
    for w in page.get_text("words", clip=tuple(reach)):
        if w[4].strip():
            lines.setdefault((w[5], w[6]), []).append((w[0], w[1], w[2], w[3], w[4]))
    return _grow(b, lines, draws)


def find_digital(path: str, page_num: int, tables: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Figures on a digital page: [{"bbox": [x0, y0, x1, y1] in points, "source": "image" | "vector", "labels": [...], "digest"?}]."""
    from app.docproc.services.reading import pdf_pages
    with pdf_pages.FITZ_LOCK, pdf_pages.open_pdf(path) as pdf:
        page = pdf[page_num - 1]
        w, h = page.rect.width, page.rect.height
        found: List[Dict[str, Any]] = []
        try:
            for info in page.get_image_info(xrefs=True):
                b = [float(v) for v in info["bbox"]]
                digest = None
                if info.get("xref"):
                    try:
                        digest = hashlib.sha1(pdf.extract_image(info["xref"])["image"]).hexdigest()[:12]
                    except Exception:
                        digest = None
                found.append({"bbox": b, "source": "image", "digest": digest})
        except Exception as e:
            logger.warning("Images on page %s could not be listed: %s", page_num, e)
        try:
            clusters = page.cluster_drawings(x_tolerance=CLUSTER_GAP, y_tolerance=CLUSTER_GAP)
        except Exception as e:
            logger.warning("Drawings on page %s could not be clustered: %s", page_num, e)
            clusters = []
        draws = page.get_drawings()
        table_boxes = [t["bbox"] for t in tables or [] if t.get("bbox") and t.get("method") == "grid"]       # ruled tables draw lines; word-found ones do not
        for r in clusters:
            b = [float(r.x0), float(r.y0), float(r.x1), float(r.y1)]
            if any(overlap(b, t) >= TABLE_OVERLAP and _area(b) <= _area(t) * 1.3 for t in table_boxes):
                continue                                              # a table's own rules
            if any(overlap(b, f["bbox"]) >= 0.6 for f in found):
                continue                                              # drawn over a picture already found
            found.append({"bbox": b, "source": "vector"})
        out = []
        merged = _merge(found, CLUSTER_GAP)
        kept: List[Dict[str, Any]] = []
        small: List[Dict[str, Any]] = []
        for f in merged:
            b = f["bbox"]
            if not _keep(b, w, h):
                if f["source"] == "vector" and _area(b) > 0 and not _ruled(draws, b) and not _is_band(b, w, h, _drawn(draws, b)) \
                        and not any(overlap(b, t) >= 0.8 for t in table_boxes):
                    small.append(f)                                       # may be a legend or a key of a figure
                continue
            if f["source"] == "vector" and _is_band(b, w, h, _drawn(draws, b)):
                continue                                              # a shaded band, a page rule, a banner or a sidebar
            if f["source"] == "image" and _area(b) / (w * h) < SMALL_IMAGE and (b[3] < h * EDGE or b[1] > h * (1 - EDGE)):
                continue                                              # a logo in the margin
            if f["source"] == "vector":
                if any(overlap(b, t) >= 0.8 for t in table_boxes):
                    continue
                if _ruled(draws, b):
                    continue                                          # ruling lines only: a table or a form's boxes
                if _drawn(draws, b) < MIN_DRAWN:
                    continue                                          # a boxed note: a border round some text
                if _icon_field(page, b):
                    continue                                          # a grid of small separate icons or bullets, not a drawing
            kept.append(f)
        for f in _attach_satellites(kept, small):
            b = f["bbox"]
            if f["source"] == "vector":
                grown, words = _with_labels(page, b, draws)
                grown = [max(0.0, grown[0]), max(0.0, grown[1]), min(w, grown[2]), min(h, grown[3])]
                # growing to take in the labels must not run into the figure beside it
                b = b if any(g is not f and overlap(grown, g["bbox"]) > 0 for g in kept) else grown
                if b is not grown:
                    words = [x[4] for x in page.get_text("words", clip=tuple(b)) if x[4].strip()]
            else:
                words = [x[4] for x in page.get_text("words", clip=tuple(b)) if x[4].strip()]
            f["labels"] = words[:MAX_LABELS]
            f["bbox"] = [round(v, 2) for v in b]
            out.append(f)
    return sorted(out, key=lambda f: (round(f["bbox"][1] / 30), f["bbox"][0]))          # top to bottom, left to right within a band


# ---- scans -------------------------------------------------------------------------------------------------------------

RESIDUE_WORDS = 15        # a region holding this many words and almost no ink besides them is a block of text...
RESIDUE_BUSY = 0.06       # ...almost none being less than this share of its area
RESIDUE_ANY = 0.012       # whatever the words, less ink than this is nothing
FRAME_MARKS = 0.15        # ink off the lines of a frame, as a share of all its ink
FRAME_PAPER = 215         # the inside of an empty frame is about this light or lighter
TEXT_TRIM = 8             # pixels taken off each side before looking for lines of type: a call-out's frame would join them
TEXT_GAP = 3              # blank rows between one line of type and the next
TEXT_LINE_MAX = 70        # pixels: a line of type is not taller than this on the full-size page
TEXT_STROKES = 12         # separate strokes along a line of type
SMALL_CELLS = 4           # a leftover region this many squares big may still be a legend or part of a diagram
GRID_PX = 5               # the page is looked at in squares this wide, on a copy 600 pixels across
INK_DIFF = 38             # a pixel this much darker than the paper around it is ink
CELL_INK = 0.05           # a square with this share of ink counts as inked
JOIN = 2                  # inked squares this close (in squares) belong to one region
TABLE_WORDS = 10          # a region with this many words in it, covering this much of it, is a table or a form
TABLE_COVER = 0.12
LINE_SHARE = 0.6          # a ruling line runs across at least this share of a region
LINE_PAD = 4              # pixels either side of a ruling line that still belong to it
STRAIGHT = 14             # pixels: a straight stroke at least this long is a line or a stem, not a curve
THICK = 7                 # pixels: ink this wide all round is a filled shape, not a line or a stroke
GRID_MARKS = 0.15          # a grid has little ink that is not on its lines; a chart in a frame has bars and curves
GRID_MAX_INK = 0.30       # ruling lines are a small share of the area they sit in
DARK_PIXEL = 150          # on the small copy, a pixel this dark is ink whatever is round it
SKEW_TRY = tuple(sorted((k * 0.3 for k in range(-10, 11)), key=abs))      # degrees a photographed page may be turned, straightest guess first
GRID_DARK = 128           # on the full-size page a pixel below this is a line (rules are drawn black)


def _bands(flags: Any) -> int:
    """How many separate runs of True there are in a 1-D sequence: ruling lines, counting a thick one as one."""
    n, prev = 0, False
    for f in flags:
        if f and not prev:
            n += 1
        prev = bool(f)
    return n


def _longest_runs(ink: Any) -> Any:
    """For each row of a 2-D boolean array, the length of its longest run of True."""
    import numpy as np
    out = np.zeros(ink.shape[0], dtype=np.int32)
    for r in range(ink.shape[0]):
        row = ink[r]
        if not row.any():
            continue
        edges = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
        starts, ends = np.where(edges == 1)[0], np.where(edges == -1)[0]
        out[r] = int((ends - starts).max())
    return out


def _text_like(ink: Any) -> bool:
    """Is this ink lines of lettering the OCR did not read (large bold type on a shaded call-out)? Two or more thin bands of ink, one per
    line of type, with many strokes along each, and nothing taller. A chart's bars, a diagram's boxes and a picture are all taller."""
    import numpy as np
    rows = ink.any(axis=1)
    bands, start, gap = [], None, 0
    for r, on in enumerate(rows):
        if on:
            start = r if start is None else start
            gap = 0
        elif start is not None:
            gap += 1
            if gap > TEXT_GAP:
                bands.append((start, r - gap))
                start, gap = None, 0
    if start is not None:
        bands.append((start, len(rows) - 1 - gap))
    bands = [b for b in bands if b[1] - b[0] >= 3]
    if len(bands) < 2 or any(b[1] - b[0] + 1 > TEXT_LINE_MAX for b in bands):
        return False
    strokes = []
    for a, b in bands:
        seg = ink[a:b + 1].any(axis=0).astype(np.int8)
        strokes.append(int(np.abs(np.diff(seg)).sum()) // 2)
    return float(np.mean(strokes)) >= TEXT_STROKES


def _runs_mask(ink: Any, min_len: int) -> Any:
    """The pixels of `ink` that lie in a straight horizontal run of at least `min_len` pixels."""
    import numpy as np
    out = np.zeros(ink.shape, dtype=bool)
    for r in range(ink.shape[0]):
        row = ink[r]
        if not row.any():
            continue
        edges = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
        for a, b in zip(np.where(edges == 1)[0], np.where(edges == -1)[0]):
            if b - a >= min_len:
                out[r, a:b] = True
    return out


def _off_the_grid(view: Any, across: Any, down: Any) -> float:
    """The share of the ink that is neither on a ruling line nor a short straight stroke (a box's side, a letter's stem), as a share of
    all the ink, with filled areas counted as off the grid: what is left is bars, curves, points and pictures, which no table has."""
    import numpy as np
    from PIL import Image, ImageFilter
    total = max(1, int(view.sum()))
    pad = np.ones(2 * LINE_PAD + 1)
    lines = (np.convolve(across, pad, mode="same") > 0)[:, None] | (np.convolve(down, pad, mode="same") > 0)[None, :]
    straight = _runs_mask(view, STRAIGHT) | _runs_mask(np.ascontiguousarray(view.T), STRAIGHT).T
    thick = np.asarray(Image.fromarray(view.astype(np.uint8) * 255).filter(ImageFilter.MinFilter(THICK))) > 127           # ink with ink all round it
    slanted = view & ~lines & ~straight
    return float((slanted | (thick & view)).sum()) / total


def _is_frame(ink: Any, masked: Optional[Any] = None) -> bool:
    """Is this region one empty rectangle (a field to write in, a call-out's frame): two long lines across, two down, and no ink off them?"""
    import numpy as np
    from PIL import Image
    h, w = ink.shape
    masked = ink if masked is None else masked
    if not ink.any():
        return False
    img = Image.fromarray(ink.astype(np.uint8) * 255)
    mimg = Image.fromarray(masked.astype(np.uint8) * 255)
    for angle in SKEW_TRY:
        view = ink if angle == 0 else np.asarray(img.rotate(angle, resample=Image.NEAREST, fillcolor=0)) > 127
        mview = masked if angle == 0 else np.asarray(mimg.rotate(angle, resample=Image.NEAREST, fillcolor=0)) > 127
        across = _longest_runs(view) >= LINE_SHARE * w
        down = _longest_runs(np.ascontiguousarray(view.T)) >= LINE_SHARE * h
        if _bands(across) >= 2 and _bands(down) >= 2 and _off_the_grid(mview, across, down) <= FRAME_MARKS:
            return True
    return False


def _grid_like(ink: Any, n_words: int, masked: Optional[Any] = None) -> bool:
    """Is this region only ruling lines (a table, a form's boxes)? Long horizontal and vertical lines, several of each, and nothing
    else inside (the words are painted out before this): a chart with a frame and gridlines also has long lines, but it has bars or a
    curve between them. A page photographed a little crooked has slanting rules, so the region is looked at turned a few degrees each
    way and the straightest view counts."""
    import numpy as np
    from PIL import Image
    h, w = ink.shape
    masked = ink if masked is None else masked
    if ink.mean() > GRID_MAX_INK or not ink.any():
        return False                                                  # mostly dark: a picture, not lines on white
    img = Image.fromarray(ink.astype(np.uint8) * 255)
    mimg = Image.fromarray(masked.astype(np.uint8) * 255)
    for angle in SKEW_TRY:
        # the lines are found with the words in (noise the OCR took for words may sit on a rule); what else is there, with the words out
        view = ink if angle == 0 else np.asarray(img.rotate(angle, resample=Image.NEAREST, fillcolor=0)) > 127
        mview = masked if angle == 0 else np.asarray(mimg.rotate(angle, resample=Image.NEAREST, fillcolor=0)) > 127
        across = _longest_runs(view) >= LINE_SHARE * w
        down = _longest_runs(np.ascontiguousarray(view.T)) >= LINE_SHARE * h
        horizontal, vertical = _bands(across), _bands(down)
        shaped = (horizontal >= 4 and vertical >= 3) or (horizontal >= 3 and vertical >= 4) or horizontal >= 6 \
            or ((horizontal >= 5 or vertical >= 5) and n_words >= 20)
        if shaped and _off_the_grid(mview, across, down) <= GRID_MARKS:
            return True
    return False


def find_scanned(jpeg: bytes, words: List[Dict[str, Any]], size: Dict[str, float]) -> List[Dict[str, Any]]:
    """Figures on a scanned or photographed page: ink that is not text. `words` are the OCR's words with their boxes in points (and,
    when known, their "block" and "line", which let the labels round a figure be taken in with it)."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    from app.docproc.services.reading import tess
    W, H = float(size["width"]), float(size["height"])
    if not W or not H:
        return []
    try:
        full = Image.open(io.BytesIO(tess.evened(jpeg))).convert("L")
    except Exception:
        return []
    sw = 600
    sh = max(1, int(sw * H / W))
    small = full.resize((sw, sh), Image.BOX)                              # averaging, so a thin rule stays a faint line instead of vanishing
    arr = np.asarray(small, dtype=np.int16)
    paper = np.asarray(small.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(12)), dtype=np.int16)
    ink = ((paper - arr) > INK_DIFF) | (arr < DARK_PIXEL)                # darker than the paper round it, or plainly dark (the inside of a picture is neither lighter nor darker than itself)
    sx, sy = sw / W, sh / H
    for wd in words:                                                      # text is not a figure
        x0, y0, x1, y1 = wd["bbox"]
        ink[max(0, int(y0 * sy) - 3):int(y1 * sy) + 4, max(0, int(x0 * sx) - 3):int(x1 * sx) + 4] = False
    cols, rows = sw // GRID_PX, sh // GRID_PX
    cell = ink[:rows * GRID_PX, :cols * GRID_PX].reshape(rows, GRID_PX, cols, GRID_PX).mean(axis=(1, 3)) >= CELL_INK
    seen = np.zeros((rows, cols), dtype=bool)
    regions: List[Tuple[int, int, int, int, int]] = []             # c0, r0, c1, r1, cells
    for r0, c0 in zip(*np.where(cell)):
        if seen[r0, c0]:
            continue
        stack, box, n = [(int(r0), int(c0))], [int(c0), int(r0), int(c0), int(r0)], 0
        seen[r0, c0] = True
        while stack:
            r, c = stack.pop()
            n += 1
            box = [min(box[0], c), min(box[1], r), max(box[2], c), max(box[3], r)]
            for dr in range(-JOIN, JOIN + 1):
                for dc in range(-JOIN, JOIN + 1):
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < rows and 0 <= cc < cols and cell[rr, cc] and not seen[rr, cc]:
                        seen[rr, cc] = True
                        stack.append((rr, cc))
        regions.append((box[0], box[1], box[2], box[3], n))
    scale = full.width / W
    gray_full = np.asarray(full)
    lines: Lines = {}
    for k, wd in enumerate(words):
        lines.setdefault((wd.get("block", -1), wd.get("line", k)), []).append((wd["bbox"][0], wd["bbox"][1], wd["bbox"][2], wd["bbox"][3], wd["text"]))
    def _ruled_region(b: BBox) -> bool:
        y0, y1, x0, x1 = int(b[1] * scale), int(b[3] * scale), int(b[0] * scale), int(b[2] * scale)
        raw = gray_full[y0:y1, x0:x1] < GRID_DARK
        inside = [wd for wd in words if b[0] <= (wd["bbox"][0] + wd["bbox"][2]) / 2 <= b[2] and b[1] <= (wd["bbox"][1] + wd["bbox"][3]) / 2 <= b[3]]
        crop = raw.copy()
        for wd in inside:                                             # the words are not lines
            wx0, wy0, wx1, wy1 = wd["bbox"]
            crop[max(0, int(wy0 * scale) - y0 - 2):int(wy1 * scale) - y0 + 3, max(0, int(wx0 * scale) - x0 - 2):int(wx1 * scale) - x0 + 3] = False
        if not crop.size:
            return False
        density = float(crop.mean())
        if (len(inside) >= RESIDUE_WORDS and density < RESIDUE_BUSY) or density < RESIDUE_ANY:
            return True                                               # what is left once the words are painted out is a few specks round text: not a drawing
        if _grid_like(raw, len(inside), crop) or _text_like(crop[TEXT_TRIM:-TEXT_TRIM, TEXT_TRIM:-TEXT_TRIM]):         # without the frame, if it has one
            return True
        # one empty frame: its sides are lines and its inside is paper. A picture's inside is not paper, so a picture with edges never counts.
        inner = gray_full[y0 + TEXT_TRIM:y1 - TEXT_TRIM, x0 + TEXT_TRIM:x1 - TEXT_TRIM]
        return bool(inner.size) and float(inner.mean()) > FRAME_PAPER and _is_frame(raw, crop)

    out: List[Dict[str, Any]] = []
    small: List[Dict[str, Any]] = []
    for c0, r0, c1, r1, n in regions:
        b = [c0 * GRID_PX / sx, r0 * GRID_PX / sy, (c1 + 1) * GRID_PX / sx, (r1 + 1) * GRID_PX / sy]
        if not _keep(b, W, H) or n < 12:
            if n >= SMALL_CELLS and not _is_band(b, W, H, 0):
                small.append({"bbox": b, "source": "region"})             # may be a legend or one more block of a diagram, drawn apart
            continue
        inside = [wd for wd in words if b[0] <= (wd["bbox"][0] + wd["bbox"][2]) / 2 <= b[2] and b[1] <= (wd["bbox"][1] + wd["bbox"][3]) / 2 <= b[3]]
        covered = sum(_area(wd["bbox"]) for wd in inside) / max(1e-9, _area(b))
        if len(inside) >= TABLE_WORDS and covered >= TABLE_COVER:
            continue                                                  # a table, a form or a block of text with some ink round it
        if _ruled_region(b):
            continue                                                  # ruling lines: a table or a form's boxes
        out.append({"bbox": b, "source": "region"})
    merged = [f for f in _merge(out, CLUSTER_GAP * 2) if not _ruled_region(f["bbox"])]      # boxes that were each alone, together, are a form
    merged = _attach_satellites(merged, small)                         # a block of the diagram, or a legend, drawn apart from the rest
    result = []
    for f in merged:
        grown, labels = _grow(f["bbox"], lines)
        f["bbox"] = [round(max(0.0, grown[0]), 2), round(max(0.0, grown[1]), 2), round(min(W, grown[2]), 2), round(min(H, grown[3]), 2)]
        f["labels"] = labels[:MAX_LABELS]
        result.append(f)
    return sorted(result, key=lambda f: (round(f["bbox"][1] / 30), f["bbox"][0]))


def drop_logos(pages: List[Dict[str, Any]]) -> int:
    """Take out a picture that sits on several pages of one document (a letterhead, a logo): it says nothing about any page.
    Works on the page dicts' structure blocks. Returns how many were removed."""
    count: Dict[str, int] = {}
    for p in pages:
        for d in {b.get("digest") for b in (p.get("structure") or {}).get("blocks", []) if b.get("type") == "figure" and b.get("digest")}:
            count[d] = count.get(d, 0) + 1
    gone = 0
    for p in pages:
        st = p.get("structure") or {}
        keep = [b for b in st.get("blocks", []) if not (b.get("type") == "figure" and b.get("digest") and count.get(b["digest"], 0) >= LOGO_PAGES)]
        gone += len(st.get("blocks", [])) - len(keep)
        if len(keep) != len(st.get("blocks", [])):
            st["blocks"] = keep
    number(pages)
    return gone


def number(pages: List[Dict[str, Any]]) -> None:
    """Each figure's index on its page, in reading order: the handle for reading it later."""
    for p in pages:
        k = 0
        for b in (p.get("structure") or {}).get("blocks", []):
            if b.get("type") == "figure":
                b["index"] = k
                k += 1


# ---- reading -----------------------------------------------------------------------------------------------------------

PROMPT_VERSION = "1"
MAX_SIDE = 1000           # the crop is sent at most this many pixels across: enough to read axes, no more tokens than that needs
MAX_POINTS = 12
CROP_PAD = 4.0            # points of page round the figure in the crop

PROMPT = """This image is one figure cropped from a document page. Answer with JSON only, in this shape:
{"kind": "chart|diagram|drawing|photo|table|logo|other", "title": string or null, "summary": "one or two sentences on what it shows",
 "chart": null or {"type": "line|bar|pie|scatter|area|other", "x_axis": {"label": string or null, "unit": string or null},
   "y_axis": {"label": string or null, "unit": string or null},
   "series": [{"name": string or null, "points": [{"x": string, "y": string, "printed": true or false}]}]},
 "components": null or [{"name": string, "role": string or null}],
 "connections": null or [{"from": string, "to": string, "label": string or null}],
 "text": [every label you can read on the figure],
 "limits": string or null}
Rules: report only what is visible, and never invent. "printed" is true only when that exact value is written on the figure; a value
you work out from the geometry (the height of a bar, the position of a point against the axis) must have "printed": false. At most
%d points per series (the clearest ones). For a diagram list its components and how they connect. For a logo or decoration use
"kind": "logo" and leave the rest empty. In "limits" say what you could not read.""" % MAX_POINTS

_CACHE: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_CACHE_MAX = 64


def crop_jpeg(path: str, page_num: int, box: BBox) -> bytes:
    """The figure as the model will see it: the region of the page, at a size that keeps its text readable and no larger."""
    from PIL import Image
    from app.docproc.services.reading import pdf_pages, tess
    with pdf_pages.FITZ_LOCK, pdf_pages.open_pdf(path) as pdf:
        page = pdf[page_num - 1]
        import pymupdf
        clip = (pymupdf.Rect(*box) + (-CROP_PAD, -CROP_PAD, CROP_PAD, CROP_PAD)) & page.rect          # a little air, so edge labels are not cut
        scale = min(3.0, max(1.0, MAX_SIDE / max(clip.width, clip.height, 1.0)))
        png = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip).tobytes("png")
    im = Image.open(io.BytesIO(tess.evened(png))).convert("RGB")
    if max(im.size) > MAX_SIDE:
        im.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return buf.getvalue()


_NUM = re.compile(r"[-+−–]?\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> List[str]:
    return [m.replace(",", "").replace("−", "-").replace("–", "-") for m in _NUM.findall(str(text))]


def check_printed(reading: Dict[str, Any], labels: List[str]) -> Dict[str, Any]:
    """Settle which chart values were written on the figure and which were estimated. The model claims "printed"; the labels found by
    code decide. A value claimed as printed that is not among the labels is estimated after all; a value that is among them is
    printed. With no labels to check against, the model's claim stands but is not counted as confirmed."""
    chart = reading.get("chart") or {}
    have = {n for t in labels for n in _numbers(t)}
    total = confirmed = 0
    for s in chart.get("series") or []:
        for p in s.get("points") or []:
            ys = _numbers(p.get("y"))
            found = bool(ys) and all(n in have for n in ys)
            printed = found if labels else bool(p.get("printed"))
            p["estimated"] = not printed
            p["value"] = float(ys[0]) if ys else None                        # "about 82" -> 82.0: the number, for whoever uses the JSON
            p.pop("printed", None)
            total += 1
            confirmed += 1 if (printed and labels) else 0
    reading["values_total"], reading["values_confirmed"] = total, confirmed
    return reading


def _clean(reply: Any) -> Dict[str, Any]:
    if isinstance(reply, str):
        try:
            reply = json.loads(reply)
        except ValueError:
            reply = {}
    r = reply if isinstance(reply, dict) else {}
    kind = str(r.get("kind") or "other").lower()
    kind = kind if kind in ("chart", "diagram", "drawing", "photo", "table", "logo", "other") else "other"

    def s(v: Any) -> Optional[str]:
        t = str(v).strip() if v is not None else ""
        return t or None

    chart = r.get("chart") if isinstance(r.get("chart"), dict) else None
    if chart:
        series = []
        for ser in (chart.get("series") or [])[:8]:
            pts = [{"x": s(p.get("x")), "y": s(p.get("y")), "printed": bool(p.get("printed"))} for p in (ser.get("points") or [])[:MAX_POINTS] if isinstance(p, dict)]
            series.append({"name": s(ser.get("name")), "points": pts})
        axis = lambda a: {"label": s((a or {}).get("label")), "unit": s((a or {}).get("unit"))}      # noqa: E731
        chart = {"type": s(chart.get("type")) or "other", "x_axis": axis(chart.get("x_axis")), "y_axis": axis(chart.get("y_axis")), "series": series}
    comps = [{"name": s(c.get("name")), "role": s(c.get("role"))} for c in (r.get("components") or [])[:60] if isinstance(c, dict) and s(c.get("name"))]
    links = [{"from": s(c.get("from")), "to": s(c.get("to")), "label": s(c.get("label"))} for c in (r.get("connections") or [])[:80]
             if isinstance(c, dict) and s(c.get("from")) and s(c.get("to"))]
    return {"kind": kind, "title": s(r.get("title")), "summary": s(r.get("summary")), "chart": chart, "components": comps or None,
            "connections": links or None, "text": [t for t in (s(x) for x in (r.get("text") or [])[:120]) if t], "limits": s(r.get("limits"))}


async def read(path: str, page_num: int, box: BBox, labels: List[str], model: str, ask=None, fresh: bool = False) -> Dict[str, Any]:
    """Read one figure: one crop, one model, one reply. `ask(model, jpeg, prompt)` is injectable for tests. The same crop read by the
    same model is answered from memory, unless `fresh`: a person asked to read it again because the first reading was wrong."""
    jpeg = await asyncio.to_thread(crop_jpeg, path, page_num, box)
    prompt = PROMPT + ("\n\nText that code found printed on this figure (use it for labels and for any value you report as printed):\n"
                       + " | ".join(labels[:MAX_LABELS]) if labels else "")
    key = hashlib.sha256(jpeg + model.encode() + PROMPT_VERSION.encode() + prompt.encode()).hexdigest()
    if key in _CACHE and not fresh:
        _CACHE.move_to_end(key)
        return json.loads(json.dumps(_CACHE[key]))
    if ask is None:
        from app.docproc.engines.providers import llm

        async def ask(m: str, img: bytes, p: str) -> str:                # noqa: E306
            return await llm.complete(m, p, img, stage="figures")
    raw = await ask(model, jpeg, prompt)
    reading = check_printed(_clean(raw), labels)
    reading["model"] = model
    _CACHE[key] = reading
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return json.loads(json.dumps(reading))


# ---- correcting a reading by hand --------------------------------------------------------------------------------------

EDIT_FIELD = re.compile(
    r"^(?:summary|title|limits"
    r"|(?P<ax>[xy])axis\.(?P<af>label|unit)"
    r"|c(?P<c>\d+)\.(?P<cf>name|role)"
    r"|l(?P<l>\d+)\.(?P<lf>from|to|label)"
    r"|s(?P<s>\d+)\.name"
    r"|s(?P<s2>\d+)\.p(?P<p>\d+)\.(?P<pa>[xy]))$")
REMOVE_FIELD = re.compile(r"^(?:c(?P<c>\d+)|l(?P<l>\d+)|s(?P<s>\d+)\.p(?P<p>\d+))$")


def edit_reading(reading: Dict[str, Any], field: str, text: str) -> bool:
    """Put a person's text into one part of a reading, where that part is stored, and mark it manually_corrected. A chart value a person
    set is no longer an estimate. False when the figure has no such part (the caller says so)."""
    m = EDIT_FIELD.match(field)
    if not m:
        return False
    chart = reading.get("chart") or {}
    if field in ("summary", "title", "limits"):
        reading[field] = text
        reading["manually_corrected"] = True
        return True
    if m.group("ax"):
        axis = chart.get(m.group("ax") + "_axis")
        if axis is None:
            return False
        axis[m.group("af")] = text
        axis["manually_corrected"] = True
        return True
    if m.group("c") is not None or m.group("l") is not None:
        items = reading.get("components" if m.group("c") is not None else "connections") or []
        i = int(m.group("c") if m.group("c") is not None else m.group("l"))
        if i >= len(items):
            return False
        items[i][m.group("cf") or m.group("lf")] = text
        items[i]["manually_corrected"] = True
        return True
    series = chart.get("series") or []
    si = int(m.group("s") if m.group("s") is not None else m.group("s2"))
    if si >= len(series):
        return False
    if m.group("s") is not None:
        series[si]["name"] = text
        series[si]["manually_corrected"] = True
        return True
    points = series[si].get("points") or []
    pi = int(m.group("p"))
    if pi >= len(points):
        return False
    point = points[pi]
    point[m.group("pa")] = text
    if m.group("pa") == "y":
        nums = _numbers(text)
        point["value"] = float(nums[0]) if nums else None
        point["estimated"] = False                       # a person set it
    point["manually_corrected"] = True
    return True


def remove_from_reading(reading: Dict[str, Any], field: str) -> bool:
    """Take a wrong part, connection or chart point out of a reading. False when there is no such item."""
    m = REMOVE_FIELD.match(field)
    if not m:
        return False
    if m.group("c") is not None or m.group("l") is not None:
        key = "components" if m.group("c") is not None else "connections"
        items = reading.get(key) or []
        i = int(m.group("c") if m.group("c") is not None else m.group("l"))
        if i >= len(items):
            return False
        del items[i]
        reading[key] = items or None
        return True
    series = (reading.get("chart") or {}).get("series") or []
    si, pi = int(m.group("s")), int(m.group("p"))
    if si >= len(series) or pi >= len(series[si].get("points") or []):
        return False
    del series[si]["points"][pi]
    return True
