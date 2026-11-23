"""
Page transcription by two independent vision models, compared line by line.

Vision models read handwriting and poor scans better than classic OCR, but they fail silently: a misread digit comes
back with full confidence, and token log-probs do not separate right from wrong. So every page is transcribed
independently by two different models and the lines are compared by content:

    both agree (same text, same numbers)  -> status "agreed"
    anything else, or a [?] marker        -> status "review"   (with the other reader's version as an alternative)

PDF pages with a real text layer skip this entirely; only scans and image-only pages are sent to the models.
"""
import asyncio
import difflib
import io
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional


logger = logging.getLogger("app.services.ocr")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10
MAX_SIDE_PX = 2048
RENDER_DPI = 200
MIN_TEXT_LAYER_CHARS = 40  # a PDF page with at least this much text is "typed", not a scan
SCAN_IMAGE_COVERAGE = 0.5  # ...unless a picture covers this share of the page: then the text is a scanner's OCR layer
ENGINE_TIMEOUT_SECONDS = 120

PROMPT = """You are transcribing one scanned or handwritten page.

Rules:
- Transcribe exactly what is written, line by line, in reading order. Keep the original line breaks.
- Do NOT correct, complete, summarise, translate or calculate. Keep spelling, abbreviations, symbols and units exactly as written (for example $215, 6%, 94.9B, 10/12/2026).
- Digits are critical: copy every digit exactly as written. If you cannot read a character or number with certainty, write [?] in its place. Never guess.
- Keep a leading decimal point (.2% is not 2%), percent and currency signs, ranges (7-20) and fractions (1 1/2) exactly as written.
- Names and rare words: copy letter by letter. Do not replace an unusual spelling with a common word.
- Include printed text as well as handwriting.
- For a table, put each row on one line with the cells separated by " | ".
- Ignore page borders, ruled lines, stains and shadows. Faint or mirrored writing showing through from the other side of the paper is not part of this page: leave it out.
- Set "handwriting" to true if any meaningful part of the page is handwritten, false if everything is printed or typed.

Return JSON only, in this shape: {"handwriting": true, "lines": ["first line", "second line"]}"""

# a number with the marks that change its meaning: .2 (not 2), 20%, -5 (see normalize._MARKS)
_NUM_RE = re.compile(r"(?:(?<![\w])[-(])?\.?\d[\d,]*(?:\.\d+)?%?")
_WS_RE = re.compile(r"\s+")


class OcrError(Exception):
    """Raised with a message that is safe to show to the user."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class OcrLine:
    text: str
    status: str = "agreed"  # agreed | review
    alternatives: List[str] = field(default_factory=list)
    reason: Optional[str] = None  # why it needs review


@dataclass
class OcrPage:
    page: int
    source: str  # ocr | text-layer
    lines: List[OcrLine]
    cross_checked: bool = True  # False when only one reader produced this page
    handwritten: bool = False   # a reader saw handwriting: Tesseract is a poor judge of such a page, so it is not used as a tie-break


_PUNCT_RE = re.compile(r"(?<!\d)[^\w\s]|[^\w\s](?!\d)")  # keeps 9.5 and 10/12/2026, drops stars, colons, specks


def _canon(line: str) -> str:
    """Comparison form: case, punctuation (stars, colons, stray scanner marks) and spacing do not make a disagreement."""
    return _WS_RE.sub(" ", _PUNCT_RE.sub("", line)).strip().lower()


def _numbers(line: str) -> List[str]:
    return sorted(m.replace(",", "") for m in _NUM_RE.findall(line))


def _compare(a: str, b: str) -> Optional[str]:
    """None when the two readings agree; otherwise the reason they do not."""
    if "[?]" in a or "[?]" in b:
        return "unreadable part"
    if _numbers(a) != _numbers(b):
        return "numbers differ"
    if _canon(a) != _canon(b) and difflib.SequenceMatcher(None, _canon(a), _canon(b)).ratio() < 0.88:
        return "wording differs"
    return None


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _canon(a), _canon(b)).ratio()


def _pair_lines(a_lines: List[str], b_lines: List[str]) -> List[tuple]:
    """Pair up the lines of a disagreeing block as (a_group, b_group).

    Usually one to one, but the two readers often break a line differently (one reads two lines as one),
    so a line is also matched against two consecutive lines of the other reading, when that fits clearly better.
    """
    pairs, i, j = [], 0, 0
    while i < len(a_lines) and j < len(b_lines):
        one = _similar(a_lines[i], b_lines[j])
        split_b = _similar(a_lines[i], " ".join(b_lines[j:j + 2])) if j + 1 < len(b_lines) else 0.0
        split_a = _similar(" ".join(a_lines[i:i + 2]), b_lines[j]) if i + 1 < len(a_lines) else 0.0
        if max(split_a, split_b) >= 0.8 and max(split_a, split_b) > one + 0.1:
            if split_b >= split_a:
                pairs.append(([a_lines[i]], b_lines[j:j + 2]))
                i, j = i + 1, j + 2
            else:
                pairs.append((a_lines[i:i + 2], [b_lines[j]]))
                i, j = i + 2, j + 1
        else:
            pairs.append(([a_lines[i]], [b_lines[j]]))
            i, j = i + 1, j + 1
    pairs.extend(([l], []) for l in a_lines[i:])
    pairs.extend(([], [l]) for l in b_lines[j:])
    return pairs


def merge_readings(primary: List[str], secondary: Optional[List[str]]) -> List[OcrLine]:
    """Line-align the two readings. The primary's text is kept; disagreements are flagged with the other version."""
    primary = [l.strip() for l in primary if l and l.strip()]
    if secondary is None:  # no second reader: cannot verify, so flag every line that carries a number
        return [OcrLine(l, "review", [], "not cross-checked") if (_NUM_RE.search(l) or "[?]" in l) else OcrLine(l)
                for l in primary]
    secondary = [l.strip() for l in secondary if l and l.strip()]
    out: List[OcrLine] = []
    sm = difflib.SequenceMatcher(None, [_canon(l) for l in primary], [_canon(l) for l in secondary], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.extend(OcrLine(l) if "[?]" not in l else OcrLine(l, "review", [], "unreadable part") for l in primary[i1:i2])
            continue
        for a_group, b_group in _pair_lines(primary[i1:i2], secondary[j1:j2]):
            a, b = " ".join(a_group) or None, " ".join(b_group) or None
            if a is not None and b is not None:
                reason = _compare(a, b)
                flag = reason is not None
                alts = [b] if flag and _canon(a) != _canon(b) else []
                for n, line in enumerate(a_group):
                    out.append(OcrLine(line, "review", alts if n == 0 else [], reason) if flag else OcrLine(line))
            elif a is not None:
                out.extend(OcrLine(l, "review", [], "second reader did not see this line") for l in a_group)
            else:
                out.extend(OcrLine(l, "review", [], "only the second reader saw this line") for l in b_group)
    return _settle_layout(out, primary, secondary)


def _settle_layout(out: List[OcrLine], primary: List[str], secondary: List[str]) -> List[OcrLine]:
    """Line pairing fails when the readers break the page differently (a two-column header read as one line by one
    model and two by the other). Compare content instead: a flagged line whose text appears intact anywhere in the
    other reader's page is not a disagreement, and a line only the second reader produced that the primary already
    contains is dropped. A genuinely different digit still fails this test and stays flagged."""
    full_a = _canon(" ".join(primary))
    full_b = _canon(" ".join(secondary))
    primary_set = {_canon(l) for l in primary}
    settled: List[OcrLine] = []
    for line in out:
        if line.status == "review" and line.reason in ("numbers differ", "wording differs", "second reader did not see this line"):
            if _canon(line.text) and _canon(line.text) in full_b and "[?]" not in line.text:
                settled.append(OcrLine(line.text))
                continue
        if line.status == "review" and line.reason == "only the second reader saw this line":
            if _canon(line.text) in full_a:
                continue  # the primary reading already holds this text
        settled.append(line)
    return settled


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------

def normalize_image(data: bytes) -> bytes:
    """Upright (EXIF), RGB, longest side <= MAX_SIDE_PX, re-encoded as JPEG."""
    from PIL import Image, ImageOps
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            im = ImageOps.exif_transpose(im).convert("RGB")
    except (OSError, Image.DecompressionBombError, ValueError) as e:
        raise OcrError("That image could not be read; it may be corrupt or too large.", 422) from e
    if max(im.size) > MAX_SIDE_PX:
        im.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


_COMMON_PUNCT = set(".,;:()-$%/&'\"!?[]{}#@*+=<>_|\\\u2014\u2013\u2022\u00b0\u00a9\u00ae\u2122\u2018\u2019\u201c\u201d\u00a0~^`")


def text_is_garbled(text: str) -> bool:
    """A text layer that is not real text: fonts without a Unicode map extract as "(cid:12)", replacement characters,
    private-use glyphs or symbol soup. Trusting it would index nonsense, so such a page is read like a scan."""
    s = text or ""
    if len(s.strip()) < MIN_TEXT_LAYER_CHARS:
        return False
    chars = [c for c in s if not c.isspace()]
    if not chars:
        return False
    bad = s.count("(cid:") * 6 + sum(1 for c in chars if c == "\ufffd" or "\ue000" <= c <= "\uf8ff" or (ord(c) < 32))
    odd = sum(1 for c in chars if not c.isalnum() and c not in _COMMON_PUNCT)
    return (bad + odd) / len(chars) > 0.3


def _is_scanned_page(page) -> bool:
    """A picture covering most of the page means a scan, whatever text layer rides on top.

    Scanner apps and archives embed their own OCR layer, which is often gibberish for handwriting;
    trusting it would skip real OCR and index the gibberish.
    """
    area = page.rect.width * page.rect.height
    if area <= 0:
        return False
    try:
        if text_is_garbled(page.get_text()):
            return True
    except Exception:
        pass
    try:
        covered = sum(fitz_rect_area(info["bbox"], page.rect) for info in page.get_image_info())
    except Exception:
        return False
    return covered / area >= SCAN_IMAGE_COVERAGE


def fitz_rect_area(bbox, page_rect) -> float:
    x0, y0, x1, y1 = bbox
    w = max(0.0, min(x1, page_rect.x1) - max(x0, page_rect.x0))
    h = max(0.0, min(y1, page_rect.y1) - max(y0, page_rect.y0))
    return w * h


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def configured_models() -> List[str]:
    raw = os.getenv("HANDWRITING_OCR_MODELS", "").strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()][:2]
    from app.docproc.engines.providers import llm
    return llm.default_models()


class Lines(list):
    """The lines of one reading. `handwriting` is the reader's own report that the page holds handwriting; it costs no
    extra call, and a plain list (tests, other readers) simply means "not reported"."""
    handwriting: bool = False


def parse_lines(raw: str) -> Lines:
    """Lines from a model reply: the JSON `lines` array, tolerating code fences and a plain-text reply."""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
        lines = data.get("lines") if isinstance(data, dict) else data
        if isinstance(lines, list):
            out = Lines(str(l).strip() for l in lines if str(l).strip())
            out.handwriting = bool(data.get("handwriting")) if isinstance(data, dict) else False
            return out
    except (ValueError, AttributeError):
        pass
    return Lines(l.strip() for l in text.splitlines() if l.strip())


async def read_image(model: str, jpeg: bytes) -> List[str]:
    """One model's transcription of one page image."""
    from app.docproc.engines.providers import llm
    return parse_lines(await llm.complete(model, PROMPT, jpeg, stage="handwriting_ocr"))


async def read_page(jpeg: bytes, models: List[str], reader=None) -> OcrPage:
    """Both models read the page concurrently; a failed second reader degrades to 'not cross-checked'."""
    reader = reader or read_image
    results = await asyncio.gather(*[asyncio.wait_for(reader(m, jpeg), ENGINE_TIMEOUT_SECONDS + 5) for m in models],
                                   return_exceptions=True)
    ok = [(m, r) for m, r in zip(models, results) if not isinstance(r, BaseException)]
    for m, r in zip(models, results):
        if isinstance(r, BaseException):
            logger.warning("Handwriting reader %s failed: %s", m, r)
    if not ok:
        raise OcrError("The transcription service is unavailable right now. Try again in a minute.", 502)
    primary = results[0] if not isinstance(results[0], BaseException) else ok[0][1]
    secondary = next((r for m, r in ok if r is not primary), None) if len(ok) > 1 else None
    handwritten = any(getattr(r, "handwriting", False) for _, r in ok)
    return OcrPage(page=0, source="ocr", lines=merge_readings(primary, secondary), cross_checked=secondary is not None,
                   handwritten=handwritten)


