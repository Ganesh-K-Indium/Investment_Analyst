"""
Local OCR engines: read a page picture on this machine, for free, and say how sure they are.

Every engine turns a page image into words with boxes and a confidence, and `build_reading` turns those into lines (a wide
gap inside a line becomes " | ", so table rows survive) plus a page-level quality summary. The pipeline uses that summary to
decide whether the local reading is good enough or the page should be read by a vision model (see ladder.py).

Engines
    tesseract  classic OCR; needs the `tesseract` program (the Docker image has it)
    rapidocr   PaddleOCR models on ONNX: light, fast on CPU, good on printed scans.
    paddleocr  PaddleOCR itself.
    doctr      docTR (PyTorch): strong on printed pages and tables.

Install one with `python scripts/dev.py ocr add <engine>` (pinned versions from backend/requirements-ocr/), or in Docker
with the OCR_ENGINES build argument.

Only installed engines are offered. Nothing here sends anything off the machine.
"""
import asyncio
import contextlib
import importlib.util
import io
import logging
import os
import re
import statistics
import subprocess
import tempfile
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.docproc import config
from app.docproc.services.reading import ocr, tess
from app.core.config import get_settings

logger = logging.getLogger("app.docproc.engines.ocr.local")

IN_WORKER = False      # True inside an engine's own process (worker.py), where the engine runs directly
LOW_WORD = 0.60        # a word read with less confidence than this is doubtful
LOW_DIGIT = 0.70       # numbers are held to a stricter standard: one wrong digit is a wrong value
GAP_FACTOR = 1.5       # a gap wider than this many text heights starts a new cell in the row
MAX_LOW_WORDS = 0.25   # more than this share of doubtful words: the page is not read well enough
MAX_LOW_DIGITS = 0.20
BLANK_INK = 0.003      # a page with less dark ink than this is blank
MIN_AGREEMENT = 0.75   # two local engines must share this much of the page's words (clean pages score 0.80-0.90, handwriting under 0.35)


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    conf: Optional[float] = None    # 0..1, None when the engine does not say


@dataclass
class Reading:
    engine: str
    lines: List[str]
    line_conf: List[Optional[float]]
    words: int = 0
    confidence: Optional[float] = None
    low_share: float = 0.0
    digit_low_share: float = 0.0
    seconds: float = 0.0
    agreement: Optional[float] = None     # share of words another local engine also read (see cross_check)
    # where each line sits on the page as fractions (x0, y0, x1, y1) of the image, and whether it holds a doubtful number:
    # what region-level re-reads need (see doubtful_lines and plan_regions)
    line_boxes: Optional[List[Tuple[float, float, float, float]]] = None
    line_digit_low: Optional[List[bool]] = None
    # each line's cells (the pieces joined by " | "), as (text, x0, y0, x1, y1) in page fractions: the layout the structure builder reads
    line_cells: Optional[List[List[Tuple[str, float, float, float, float]]]] = None

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


_ALNUM = re.compile(r"[A-Za-z0-9]")
_DIGIT = re.compile(r"\d")


def build_reading(words: List[Word], engine: str, seconds: float = 0.0, size: Optional[Tuple[float, float]] = None) -> Reading:
    """Group words into rows (by vertical position), join each row left to right, and summarise confidence.
    `size` is the (width, height) of the image the word boxes refer to; with it each line also gets a page-relative box."""
    words = [w for w in words if w.text and w.text.strip()]
    if not words:
        return Reading(engine, [], [], 0, None, 0.0, 0.0, seconds)
    heights = [max(w.y1 - w.y0, 1.0) for w in words]
    typical = statistics.median(heights)
    ordered = sorted(words, key=lambda w: ((w.y0 + w.y1) / 2, w.x0))
    rows: List[List[Word]] = []
    for w in ordered:
        mid = (w.y0 + w.y1) / 2
        if rows and abs(mid - statistics.fmean((x.y0 + x.y1) / 2 for x in rows[-1])) <= typical * 0.6:
            rows[-1].append(w)
        else:
            rows.append([w])
    lines: List[str] = []
    line_conf: List[Optional[float]] = []
    boxes: List[Tuple[float, float, float, float]] = []
    digit_low: List[bool] = []
    cells: List[List[Tuple[str, float, float, float, float]]] = []
    for row in rows:
        row.sort(key=lambda w: w.x0)
        groups: List[List[Word]] = [[row[0]]]
        for prev, w in zip(row, row[1:]):
            gap = w.x0 - prev.x1
            h = max(prev.y1 - prev.y0, typical, 1.0)
            if gap > h * GAP_FACTOR:
                groups.append([w])
            else:
                groups[-1].append(w)
        text = " | ".join(" ".join(x.text.strip() for x in g) for g in groups)
        lines.append(text)
        if size and size[0] > 0 and size[1] > 0:
            cells.append([(" ".join(x.text.strip() for x in g), min(x.x0 for x in g) / size[0], min(x.y0 for x in g) / size[1],
                           max(x.x1 for x in g) / size[0], max(x.y1 for x in g) / size[1]) for g in groups])
        confs = [w.conf for w in row if w.conf is not None and _ALNUM.search(w.text)]
        line_conf.append(statistics.fmean(confs) if confs else None)
        digit_low.append(any(w.conf is not None and w.conf < LOW_DIGIT and _DIGIT.search(w.text) for w in row))
        if size and size[0] > 0 and size[1] > 0:
            boxes.append((min(w.x0 for w in row) / size[0], min(w.y0 for w in row) / size[1],
                          max(w.x1 for w in row) / size[0], max(w.y1 for w in row) / size[1]))
    real = [w for w in words if _ALNUM.search(w.text) and w.conf is not None]
    digits = [w for w in real if _DIGIT.search(w.text)]
    return Reading(
        engine=engine, lines=lines, line_conf=line_conf, words=len([w for w in words if _ALNUM.search(w.text)]),
        confidence=statistics.fmean(w.conf for w in real) if real else None,
        low_share=(sum(1 for w in real if w.conf < LOW_WORD) / len(real)) if real else 0.0,
        digit_low_share=(sum(1 for w in digits if w.conf < LOW_DIGIT) / len(digits)) if digits else 0.0,
        seconds=seconds, line_boxes=boxes if len(boxes) == len(lines) else None, line_digit_low=digit_low,
        line_cells=cells if len(cells) == len(lines) else None)


# ---------------------------------------------------------------------------------------------------------------------
# Is the local reading good enough?
# ---------------------------------------------------------------------------------------------------------------------

def ink_ratio(jpeg: bytes) -> float:
    """Share of dark pixels on the page: a blank page has almost none."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(jpeg)).convert("L")
        img.thumbnail((400, 400))
        hist = img.histogram()
        return sum(hist[:140]) / max(sum(hist), 1)
    except Exception:
        return 1.0     # cannot tell: treat the page as having content, so it is not silently skipped


_TOKEN = re.compile(r"[a-z0-9]+(?:[.,/\-][a-z0-9]+)*")


def tokens(text: str) -> List[str]:
    """The words and numbers of a text, for comparing two readings of the same page."""
    return [t for t in _TOKEN.findall((text or "").lower()) if len(t) > 1 or t.isdigit()]


def agreement(a: str, b: str) -> float:
    """How much of two readings is the same (Dice overlap of their words, order ignored)."""
    ca, cb = Counter(tokens(a)), Counter(tokens(b))
    na, nb = sum(ca.values()), sum(cb.values())
    return 2 * sum((ca & cb).values()) / (na + nb) if na and nb else 0.0


MIN_GLUED = 7          # a token shorter than this is not worth splitting
MAX_UNMATCHED = 0.12   # share of a glued token's characters that no reference word explains, at most


def _split_glued(token: str, vocab: set, longest: int) -> Optional[List[str]]:
    """Cut one run-together token ("25TestwayAveSte201") into the words another engine read ("25", "Testway", "Ave", ...).
    Best cut = fewest characters no reference word explains, then fewest pieces. None when no cut explains most of it."""
    n = len(token)
    low = token.lower()
    INF = (float("inf"), 0)
    best: List[Tuple[float, int]] = [INF] * (n + 1)    # (unmatched chars, pieces) for the first i characters
    best[0] = (0, 0)
    back: List[Optional[Tuple[int, bool]]] = [None] * (n + 1)
    for i in range(1, n + 1):
        cand = best[i - 1]
        if cand != INF:                                 # leave one character unexplained
            best[i], back[i] = (cand[0] + 1, cand[1]), (i - 1, False)
        for j in range(max(0, i - longest), i):
            if best[j] != INF and low[j:i] in vocab and (i - j >= 2 or low[j:i].isdigit()):
                c = (best[j][0], best[j][1] + 1)
                if c < best[i]:
                    best[i], back[i] = c, (j, True)
    if best[n][0] > n * MAX_UNMATCHED or best[n][1] < 2:
        return None
    pieces: List[str] = []
    i, loose = n, ""
    while i > 0:
        j, matched = back[i]                            # type: ignore[misc]
        if matched:
            if loose:
                pieces.append(loose)
                loose = ""
            pieces.append(token[j:i])
        else:
            loose = token[j:i] + loose
        i = j
    if loose:
        pieces.append(loose)
    pieces.reverse()
    if sum(1 for p in pieces if p.lower() in vocab) < 2:
        return None
    # an unexplained fragment stays attached to the word beside it instead of standing alone
    out: List[str] = []
    for p in pieces:
        if p.lower() in vocab or not out:
            out.append(p)
        else:
            out[-1] += p
    return out


def respace(primary: Reading, reference: Reading) -> Reading:
    """Put back the spaces an engine dropped. Some read a line as one run-together word ("WedNov1109:21:382026MSTPage4of9");
    Tesseract's words for the same page say where the breaks belong. Only long tokens are touched, only when a cut
    explains nearly all of the token, and only a space is ever added: no character is changed."""
    vocab = {t.lower() for line in reference.lines for t in line.replace(" | ", " ").split() if len(t) >= 1}
    if not vocab:
        return primary
    longest = max(len(t) for t in vocab)
    def fix(text: str) -> str:
        out: List[str] = []
        for tok in text.split(" "):
            cut = _split_glued(tok, vocab, longest) if len(tok) >= MIN_GLUED and tok.lower() not in vocab and tok != "|" else None
            out.extend(cut if cut else [tok])
        return " ".join(out)

    if primary.line_cells is not None:                  # fix each cell, so the layout keeps the same text as the lines
        primary.line_cells = [[(fix(c[0]),) + tuple(c[1:]) for c in row] for row in primary.line_cells]   # type: ignore[misc]
        lines = [" | ".join(c[0] for c in row) for row in primary.line_cells]
    else:
        lines = [" | ".join(fix(part) for part in line.split(" | ")) for line in primary.lines]
    primary.lines = lines
    return primary


def cross_check(primary: Reading, other: Reading) -> Reading:
    """Judge `primary` by how well a second, independent engine agrees with it. Some engines (RapidOCR, PaddleOCR, docTR) report
    confidences that look the same on clean print and on handwriting, so their own number cannot decide anything; two
    different engines agreeing on a page is evidence, and disagreeing on a line marks exactly that line as doubtful."""
    theirs = Counter(tokens(other.text))
    conf: List[Optional[float]] = []
    for line in primary.lines:
        ts = tokens(line)
        conf.append(sum(1 for t in ts if theirs[t] > 0) / len(ts) if ts else None)
    primary.line_conf = conf
    primary.line_digit_low = [False] * len(conf)       # this engine's own digit confidences mean nothing; agreement is the evidence
    primary.agreement = agreement(primary.text, other.text)
    return primary


@dataclass
class Verdict:
    ok: bool
    reasons: List[str] = field(default_factory=list)


def assess(reading: Reading, threshold: float, jpeg: Optional[bytes] = None) -> Verdict:
    """Decide whether this reading can stand without a vision model. Every reason is plain language for the review screen."""
    if reading.words == 0:
        if jpeg is not None and ink_ratio(jpeg) < BLANK_INK:
            return Verdict(True)                                   # a blank page has nothing to read
        return Verdict(False, ["the page has marks but no text was recognised (handwriting, a photo or a poor scan)"])
    reasons: List[str] = []
    if reading.confidence is not None and reading.confidence < threshold:
        reasons.append(f"OCR confidence {reading.confidence:.0%} is below the {threshold:.0%} needed")
    if reading.low_share > MAX_LOW_WORDS:
        reasons.append(f"{reading.low_share:.0%} of the words were hard to read")
    if reading.digit_low_share > MAX_LOW_DIGITS:
        reasons.append(f"{reading.digit_low_share:.0%} of the numbers were hard to read")
    if ocr.text_is_garbled(reading.text):
        reasons.append("the recognised text is garbled")
    return Verdict(not reasons, reasons)


def doubtful_lines(r: Reading, calibrated: bool) -> List[int]:
    """Indexes of the lines that should not stand on the local reading's word: a line the engine was unsure of (below
    LOW_WORD), a number it was unsure of, or, for an engine whose confidence cannot be trusted, a line the second engine
    does not agree with (`cross_check` puts that agreement in line_conf). Judged line by line, because a page's average
    hides one terrible line."""
    cut = LOW_WORD if calibrated else MIN_AGREEMENT
    digits = r.line_digit_low or [False] * len(r.lines)
    return [i for i, c in enumerate(r.line_conf) if (c is not None and c < cut) or digits[i]]


MAX_REGIONS = 3         # more separate regions than this and the crops cost about as much as the whole page
MAX_REGION_AREA = 0.5   # so does re-reading more than half of the page
MAX_DOUBTFUL = 0.4      # if this share of the lines is doubtful the page as a whole is poor: read all of it


@dataclass
class Region:
    first: int                                   # the lines first..last (inclusive) this region replaces
    last: int
    box: Tuple[float, float, float, float]       # page-relative (x0, y0, x1, y1), padded


def plan_regions(r: Reading, doubtful: List[int]) -> Optional[List[Region]]:
    """Group doubtful lines into a few bands to re-read on their own, or None when the whole page should be read instead
    (nothing located, too many bands, too much of the page, or most lines are doubtful)."""
    if not doubtful or not r.line_boxes or len(r.line_boxes) != len(r.lines) or len(doubtful) / len(r.lines) > MAX_DOUBTFUL:
        return None
    bands: List[List[int]] = []
    for i in sorted(doubtful):
        if bands and i - bands[-1][-1] <= 2:             # a good line between two doubtful ones is re-read too, for context
            bands[-1].extend(range(bands[-1][-1] + 1, i + 1))
        else:
            bands.append([i])
    out: List[Region] = []
    for b in bands:
        boxes = [r.line_boxes[i] for i in b]
        x0, y0 = min(x[0] for x in boxes), min(x[1] for x in boxes)
        x1, y1 = max(x[2] for x in boxes), max(x[3] for x in boxes)
        px, py = 0.02, max(0.006, (y1 - y0) / (len(b) * 4))
        out.append(Region(b[0], b[-1], (max(0.0, x0 - px), max(0.0, y0 - py), min(1.0, x1 + px), min(1.0, y1 + py))))
    area = sum((g.box[2] - g.box[0]) * (g.box[3] - g.box[1]) for g in out)
    return out if len(out) <= MAX_REGIONS and area <= MAX_REGION_AREA else None


def crop(jpeg: bytes, box: Tuple[float, float, float, float], min_height: int = 96) -> bytes:
    """The part of the page image inside a page-relative box, tall enough for a model to read."""
    from PIL import Image
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    w, h = img.size
    x0, y0, x1, y1 = int(box[0] * w), int(box[1] * h), max(int(box[2] * w), int(box[0] * w) + 1), max(int(box[3] * h), int(box[1] * h) + 1)
    if y1 - y0 < min_height:                                   # a one-line strip is too thin to read reliably: add context
        grow = (min_height - (y1 - y0)) // 2 + 1
        y0, y1 = max(0, y0 - grow), min(h, y1 + grow)
    out = io.BytesIO()
    img.crop((x0, y0, x1, y1)).save(out, "JPEG", quality=92)
    return out.getvalue()


def assess_cross(reading: Reading, jpeg: Optional[bytes] = None) -> Verdict:
    """The verdict for a reading that was cross-checked against a second engine (see cross_check)."""
    if reading.words == 0:
        return assess(reading, 0.0, jpeg)
    reasons: List[str] = []
    if reading.agreement is not None and reading.agreement < MIN_AGREEMENT:
        reasons.append(f"two OCR engines agree on only {reading.agreement:.0%} of the page")
    if ocr.text_is_garbled(reading.text):
        reasons.append("the recognised text is garbled")
    return Verdict(not reasons, reasons)


# ---------------------------------------------------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------------------------------------------------

# ---------------------------------------------------------------------------------------------------------------------
# Where the neural engines keep their models
# ---------------------------------------------------------------------------------------------------------------------
# Inside the app's own data folder by default, so everything the backend needs travels with it: copy DATA_DIR (or MODELS_DIR) to
# another server and the models go too. An explicit DOCTR_CACHE_DIR / PADDLE_PDX_CACHE_HOME still wins.

APPROX_MB = {"paddleocr": 130, "doctr": 125}


def models_dir() -> Path:
    return Path(get_settings().docproc_models_dir or config.data_dir() / "models")


def model_folders() -> Dict[str, Path]:
    return {"paddleocr": Path(os.getenv("PADDLE_PDX_CACHE_HOME") or models_dir() / "paddlex") / "official_models",
            "doctr": Path(os.getenv("DOCTR_CACHE_DIR") or models_dir() / "doctr") / "models"}


def configure_caches() -> None:
    """Point the libraries at the app's models folder. Must run before they are first imported (they read these at import)."""
    os.environ.setdefault("DOCTR_CACHE_DIR", str(models_dir() / "doctr"))
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(models_dir() / "paddlex"))
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")      # no connectivity probe before loading


@contextlib.contextmanager
def private_home():
    """Run a block with the user's home folder pointed inside the app's models folder. PaddlePaddle creates ~/.cache/paddle the moment
    it is imported and offers no setting to move it, so on a machine where that folder cannot be created (a `~/.cache` owned by root
    after a `sudo pip`, a read-only home in a container) it cannot even load. The paths it computes while this is active land in
    `<models>/home` instead, and the real home is put back afterwards."""
    home = models_dir() / "home"
    home.mkdir(parents=True, exist_ok=True)
    names = ("HOME", "USERPROFILE")                       # POSIX and Windows
    saved = {n: os.environ.get(n) for n in names}
    for n in names:
        os.environ[n] = str(home)
    try:
        yield home
    finally:
        for n, v in saved.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v


def folder_size(path: Path) -> Tuple[int, int]:
    """(files, bytes) under a folder; (0, 0) when it does not exist."""
    files = [f for f in path.rglob("*") if f.is_file()] if path.is_dir() else []
    return len(files), sum(f.stat().st_size for f in files)


def _rapidocr_models() -> Optional[Path]:
    """RapidOCR 3+ downloads its models (~30 MB) on first use into the package's own `models` folder. The older
    rapidocr-onnxruntime package ships them inside, so it has no folder to report."""
    spec = importlib.util.find_spec("rapidocr")
    return Path(list(spec.submodule_search_locations)[0]) / "models" if spec and spec.submodule_search_locations else None


def model_status(engine_id: str) -> Dict[str, Any]:
    """Whether an engine's models are on disk. `state` is "bundled" (inside the package), "downloaded", "missing" or "none"."""
    if engine_id == "rapidocr":
        folder = _rapidocr_models()
        if folder is None:
            return {"state": "bundled"}
        models = list(folder.glob("*.onnx")) if folder.is_dir() else []
        return {"state": "downloaded" if models else "missing", "files": len(models),
                "bytes": sum(f.stat().st_size for f in models), "approx_mb": 30, "path": str(folder)}
    if engine_id not in APPROX_MB:
        return {"state": "none"}
    path = model_folders()[engine_id]
    files, size = folder_size(path)
    return {"state": "downloaded" if files else "missing", "files": files, "bytes": size, "approx_mb": APPROX_MB[engine_id], "path": str(path)}


class Engine:
    isolated = False      # True: runs in a child process of its own (isolated.py), because its library cannot share a process with the others
    serial = False        # True: one page at a time. The neural engines are not guaranteed safe to run from several threads at once
    calibrated = True     # False: its confidence cannot tell clean print from handwriting, so a second engine must agree
    id = ""
    label = ""
    summary = ""
    install = ""

    def available(self) -> bool:
        raise NotImplementedError

    def _words(self, jpeg: bytes) -> List[Word]:
        raise NotImplementedError

    @property
    def _turn(self) -> threading.Lock:
        if not hasattr(self, "_turn_lock"):
            self._turn_lock = threading.Lock()
        return self._turn_lock

    async def read(self, jpeg: bytes) -> Reading:
        start = time.perf_counter()
        words = await asyncio.to_thread(self._run, jpeg)
        return build_reading(words, self.id, time.perf_counter() - start, image_size(jpeg))

    def _run(self, jpeg: bytes) -> List[Word]:
        if self.isolated and not IN_WORKER:
            from app.docproc.engines.ocr import isolated
            with self._turn:
                return [Word(r[0], r[1], r[2], r[3], r[4], r[5]) for r in isolated.call(self.id, self.label, jpeg)]
        if not self.serial:
            return self._words(jpeg)
        with self._turn:
            return self._words(jpeg)

    def info(self) -> Dict[str, Any]:
        ok = self.available()
        return {"id": self.id, "label": self.label, "summary": self.summary, "ready": ok, "cross_checked": not self.calibrated,
                "models": model_status(self.id) if ok else {"state": "none"},
                "status": "ready" if ok else "not_installed", "hint": "" if ok else self.install}


def words_from_tsv(tsv: str) -> List[Word]:
    """Tesseract's TSV output: one row per word with its box and a 0..100 confidence."""
    out: List[Word] = []
    for row in tsv.splitlines()[1:]:
        c = row.split("\t", 11)
        if len(c) < 12 or c[0] != "5" or not c[11].strip():
            continue
        try:
            x, y, w, h, conf = float(c[6]), float(c[7]), float(c[8]), float(c[9]), float(c[10])
        except ValueError:
            continue
        out.append(Word(x, y, x + w, y + h, c[11].strip(), conf / 100.0 if conf >= 0 else None))
    return out


def tsv_size(tsv: str) -> Optional[Tuple[float, float]]:
    """The page image size Tesseract's word table is measured in (its level-1 row)."""
    for row in tsv.splitlines()[1:]:
        c = row.split("\t", 11)
        if len(c) >= 10 and c[0] == "1":
            try:
                return float(c[8]), float(c[9])
            except ValueError:
                return None
    return None


def image_size(jpeg: bytes) -> Optional[Tuple[float, float]]:
    try:
        from PIL import Image
        return Image.open(io.BytesIO(jpeg)).size
    except Exception:
        return None


class Tesseract(Engine):
    id = "tesseract"
    label = "Tesseract"
    summary = "Classic OCR. Fast and free; good on clean printed pages, weak on handwriting and poor photos."
    install = "Install the tesseract program: python scripts/dev.py ocr add tesseract (or apt install tesseract-ocr, brew install tesseract, winget install UB-Mannheim.TesseractOCR)."

    def available(self) -> bool:
        return tess.available()

    def _words(self, jpeg: bytes) -> List[Word]:
        exe = tess.binary()
        if not exe:
            raise RuntimeError("Tesseract is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "page.jpg"
            src.write_bytes(jpeg)
            proc = subprocess.run([exe, str(src), "stdout", "-l", tess.LANG, "--dpi", "200", "tsv"], capture_output=True,
                                  timeout=tess.TIMEOUT_SECONDS, env={**os.environ, "OMP_THREAD_LIMIT": "1"})
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or b"").decode(errors="replace")[:300] or "Tesseract failed")
        return words_from_tsv(proc.stdout.decode(errors="replace"))


def _array(jpeg: bytes):
    import numpy as np
    from PIL import Image
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    return np.array(img), img.size


def _box_words(items: List[Tuple[Any, str, Optional[float]]]) -> List[Word]:
    """(polygon or rectangle, text, confidence) -> Words. A polygon is a list of (x, y) corners."""
    out: List[Word] = []
    for box, text, conf in items:
        pts = [(float(p[0]), float(p[1])) for p in box] if len(box) and hasattr(box[0], "__len__") else None
        if pts:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        else:
            x0, y0, x1, y1 = (float(v) for v in box)
        if text and text.strip():
            out.append(Word(x0, y0, x1, y1, str(text).strip(), None if conf is None else max(0.0, min(1.0, float(conf)))))
    return out


class _Lazy:
    """Model loading is slow and heavy: do it once, on first use, from one thread at a time."""

    def __init__(self, make: Callable[[], Any]):
        self._make, self._obj, self._lock = make, None, threading.Lock()

    def get(self):
        with self._lock:
            if self._obj is None:
                self._obj = self._make()
            return self._obj


class RapidOCR(Engine):
    calibrated = False
    serial = True
    id = "rapidocr"
    label = "RapidOCR"
    summary = "PaddleOCR models on ONNX. Light and quick on a CPU; a good local step up from Tesseract for printed scans."
    install = "python scripts/dev.py ocr add rapidocr"

    def __init__(self):
        self._model = _Lazy(self._load)

    def available(self) -> bool:
        return importlib.util.find_spec("rapidocr_onnxruntime") is not None or importlib.util.find_spec("rapidocr") is not None

    @staticmethod
    def _load():
        try:
            from rapidocr_onnxruntime import RapidOCR as R
        except ImportError:
            from rapidocr import RapidOCR as R
        return R()

    def _words(self, jpeg: bytes) -> List[Word]:
        arr, _ = _array(jpeg)
        res = self._model.get()(arr)
        items: List[Tuple[Any, str, Optional[float]]] = []
        if isinstance(res, tuple):                                   # rapidocr_onnxruntime: (result, timings)
            for r in (res[0] or []):
                items.append((r[0], r[1], float(r[2])))
        else:                                                        # rapidocr 2+/3: an object with parallel lists
            boxes, txts, scores = getattr(res, "boxes", None), getattr(res, "txts", None), getattr(res, "scores", None)
            if boxes is not None and txts is not None:
                items = [(b, t, float(s) if scores is not None else None) for b, t, s in zip(boxes, txts, scores if scores is not None else [None] * len(txts))]
        return _box_words(items)


class PaddleOCR(Engine):
    calibrated = False
    serial = True
    isolated = True       # Paddle and PyTorch cannot share a process (see worker.py)
    id = "paddleocr"
    label = "PaddleOCR"
    summary = "Strong open-source OCR with good layout handling. Heavier to install than RapidOCR."
    install = "python scripts/dev.py ocr add paddleocr"

    def __init__(self):
        self._model = _Lazy(self._load)

    def available(self) -> bool:
        return importlib.util.find_spec("paddleocr") is not None

    @staticmethod
    def _load():
        configure_caches()
        with private_home():                                         # see private_home: importing paddle writes under the home folder
            import paddle  # noqa: F401
            from paddleocr import PaddleOCR as P
            try:                                                     # 3.x
                return P(lang="en", use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False)
            except (TypeError, ValueError):                          # 2.x
                return P(lang="en", use_angle_cls=False, show_log=False)

    def _words(self, jpeg: bytes) -> List[Word]:
        arr, _ = _array(jpeg)
        model = self._model.get()
        items: List[Tuple[Any, str, Optional[float]]] = []
        if hasattr(model, "predict"):                                # 3.x: dict-like results
            for page in model.predict(arr) or []:
                texts, scores = page.get("rec_texts") or [], page.get("rec_scores") or []
                polys = page.get("rec_polys") if page.get("rec_polys") is not None else page.get("rec_boxes")
                for i, t in enumerate(texts):
                    items.append((polys[i].tolist() if hasattr(polys[i], "tolist") else polys[i], t, float(scores[i]) if i < len(scores) else None))
        else:                                                        # 2.x: [[ [box, (text, conf)], ... ]]
            for page in model.ocr(arr, cls=False) or []:
                for box, (t, c) in (page or []):
                    items.append((box, t, float(c)))
        return _box_words(items)


class DocTR(Engine):
    calibrated = False
    serial = True
    isolated = True       # Paddle and PyTorch cannot share a process (see worker.py)
    id = "doctr"
    label = "docTR"
    summary = "Deep-learning OCR (PyTorch) with strong results on printed pages and tables. Needs more memory."
    install = "python scripts/dev.py ocr add doctr"

    def __init__(self):
        self._model = _Lazy(self._load)

    def available(self) -> bool:
        return importlib.util.find_spec("doctr") is not None

    @staticmethod
    def _load():
        configure_caches()
        from doctr.models import ocr_predictor
        return ocr_predictor(pretrained=True)

    def _words(self, jpeg: bytes) -> List[Word]:
        from doctr.io import DocumentFile
        _, (w, h) = _array(jpeg)
        result = self._model.get()(DocumentFile.from_images([jpeg]))
        out: List[Word] = []
        for page in result.pages:
            for block in page.blocks:
                for line in block.lines:
                    for word in line.words:
                        (x0, y0), (x1, y1) = word.geometry
                        out.append(Word(x0 * w, y0 * h, x1 * w, y1 * h, word.value, float(word.confidence)))
        return out


_ENGINES: Dict[str, Engine] = {e.id: e for e in (Tesseract(), RapidOCR(), PaddleOCR(), DocTR())}


def engines() -> List[Engine]:
    return list(_ENGINES.values())


def get(engine_id: Optional[str]) -> Optional[Engine]:
    return _ENGINES.get(engine_id or "")


def ready() -> List[Engine]:
    return [e for e in _ENGINES.values() if e.available()]


def listing() -> List[Dict[str, Any]]:
    return [e.info() for e in _ENGINES.values()]
