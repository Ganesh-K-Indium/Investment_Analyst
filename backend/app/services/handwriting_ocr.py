"""
Handwritten / scanned note -> reviewable text, for analyst notes (see app/services/analyst_notes.py).

Why two readers: vision LLMs read handwriting better than classic OCR, but they fail silently (a misread digit comes
back with full confidence, and token log-probs do not separate right from wrong). So every page is transcribed
independently by two different models and the lines are compared:

    both agree (same text, same numbers)  -> status "agreed"
    anything else, or a [?] marker        -> status "review"   (with the other reader's version as an alternative)

Nothing here is indexed. The caller shows the draft to the user, who fixes the flagged lines and then saves it as an
ordinary note, so unverified text never reaches the knowledge base.

PDFs with a real text layer skip OCR entirely; only image-only pages (scans) and image uploads are sent to the models.
"""
import asyncio
import base64
import difflib
import io
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.utils.usage_tracker import record_usage

logger = logging.getLogger("app.services.handwriting_ocr")

# primary reader (its text is kept) , second reader (used to cross-check). Overridable via env.
DEFAULT_MODELS = "gpt-4.1,gpt-4.1-mini"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10
MAX_SIDE_PX = 2048
RENDER_DPI = 200
MIN_TEXT_LAYER_CHARS = 40  # a PDF page with at least this much text is "typed", not a scan
SCAN_IMAGE_COVERAGE = 0.5  # ...unless a picture covers this share of the page: then the text is a scanner's OCR layer
ENGINE_TIMEOUT_SECONDS = 120

PROMPT = """You are transcribing a handwritten or scanned note for a financial analyst's records.

Rules:
- Transcribe exactly what is written, line by line, in reading order. Keep the original line breaks.
- Do NOT correct, complete, summarise, translate or calculate. Keep spelling, abbreviations, symbols and units exactly as written (for example $215, 6%, 94.9B, YoY).
- Digits are critical: copy every digit exactly as written. If you cannot read a character or number with certainty, write [?] in its place. Never guess.
- Include printed text as well as handwriting.
- For a table, put each row on one line with the cells separated by " | ".
- Ignore page borders, ruled lines, stains and shadows.

Return JSON only, in this shape: {"lines": ["first line", "second line"]}"""

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
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


@dataclass
class OcrResult:
    pages: List[OcrPage]
    engines: List[str]
    verified: bool  # False when only one reader was available, so nothing was cross-checked

    @property
    def text(self) -> str:
        return "\n\n".join("\n".join(l.text for l in p.lines) for p in self.pages if p.lines)

    @property
    def needs_review(self) -> int:
        return sum(l.status == "review" for p in self.pages for l in p.lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text, "engines": self.engines, "verified": self.verified, "needs_review": self.needs_review,
            "pages": [{"page": p.page, "source": p.source,
                       "lines": [{"text": l.text, "status": l.status, "alternatives": l.alternatives, "reason": l.reason}
                                 for l in p.lines]} for p in self.pages],
        }


# ---------------------------------------------------------------------------
# Comparing two transcriptions
# ---------------------------------------------------------------------------

def _canon(line: str) -> str:
    return _WS_RE.sub(" ", line).strip().lower()


def _numbers(line: str) -> List[str]:
    return sorted(m.replace(",", "") for m in _NUM_RE.findall(line))


def _compare(a: str, b: str) -> Optional[str]:
    """None when the two readings agree; otherwise the reason they do not."""
    if "[?]" in a or "[?]" in b:
        return "unreadable part"
    if _numbers(a) != _numbers(b):
        return "numbers differ"
    if _canon(a) != _canon(b) and difflib.SequenceMatcher(None, _canon(a), _canon(b)).ratio() < 0.92:
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
    return out


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------

def _sniff(data: bytes) -> str:
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypheif"):
        raise OcrError("HEIC photos are not supported. Export the photo as JPG or PNG and try again.", 415)
    raise OcrError("Unsupported file. Upload a JPG, PNG, WEBP image or a PDF.", 415)


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


def _is_scanned_page(page) -> bool:
    """A picture covering most of the page means a scan, whatever text layer rides on top.

    Scanner apps and archives embed their own OCR layer, which is often gibberish for handwriting;
    trusting it would skip real OCR and index the gibberish.
    """
    area = page.rect.width * page.rect.height
    if area <= 0:
        return False
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


def split_pages(data: bytes) -> List[Dict[str, Any]]:
    """[{"page": n, "text": str | None, "image": jpeg bytes | None}]: typed PDF pages carry text, everything else an image."""
    kind = _sniff(data)
    if kind != "pdf":
        return [{"page": 1, "text": None, "image": normalize_image(data)}]
    import fitz
    try:
        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise OcrError("That PDF could not be read.", 422) from e
    with pdf:
        if pdf.needs_pass:
            raise OcrError("Password-protected PDFs are not supported.", 422)
        if pdf.page_count > MAX_PAGES:
            raise OcrError(f"PDF has {pdf.page_count} pages; the limit is {MAX_PAGES}. Split it and upload in parts.", 413)
        pages = []
        for i, page in enumerate(pdf, 1):
            text = page.get_text().strip()
            if len(text) >= MIN_TEXT_LAYER_CHARS and not _is_scanned_page(page):
                pages.append({"page": i, "text": text, "image": None})
            else:
                png = page.get_pixmap(dpi=RENDER_DPI).tobytes("png")
                pages.append({"page": i, "text": None, "image": normalize_image(png)})
        return pages


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import AsyncOpenAI
        _client = AsyncOpenAI(timeout=ENGINE_TIMEOUT_SECONDS)
    return _client


def configured_models() -> List[str]:
    raw = os.getenv("HANDWRITING_OCR_MODELS", DEFAULT_MODELS)
    return [m.strip() for m in raw.split(",") if m.strip()][:2]


def is_enabled() -> bool:
    return os.getenv("HANDWRITING_OCR_ENABLED", "1").lower() not in ("0", "false", "no") and bool(os.getenv("OPENAI_API_KEY"))


def parse_lines(raw: str) -> List[str]:
    """Lines from a model reply: the JSON `lines` array, tolerating code fences and a plain-text reply."""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
        lines = data.get("lines") if isinstance(data, dict) else data
        if isinstance(lines, list):
            return [str(l).strip() for l in lines if str(l).strip()]
    except (ValueError, AttributeError):
        pass
    return [l.strip() for l in text.splitlines() if l.strip()]


async def read_image(model: str, jpeg: bytes) -> List[str]:
    """One model's transcription of one page image."""
    b64 = base64.b64encode(jpeg).decode()
    kwargs: Dict[str, Any] = {"model": model, "response_format": {"type": "json_object"}, "messages": [{
        "role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}}]}]}
    if model.startswith(("gpt-5", "o")):
        kwargs["reasoning_effort"] = "low"
    else:
        kwargs["temperature"] = 0
    start = time.perf_counter()
    resp = await _get_client().chat.completions.create(**kwargs)
    usage = getattr(resp, "usage", None)
    record_usage(stage="handwriting_ocr", model=model, prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                 completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                 total_tokens=getattr(usage, "total_tokens", 0) or 0, duration_seconds=time.perf_counter() - start)
    return parse_lines(resp.choices[0].message.content)


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
        raise OcrError("The transcription service is unavailable right now. Please try again, or type the note.", 502)
    primary = results[0] if not isinstance(results[0], BaseException) else ok[0][1]
    secondary = next((r for m, r in ok if r is not primary), None) if len(ok) > 1 else None
    return OcrPage(page=0, source="ocr", lines=merge_readings(primary, secondary), cross_checked=secondary is not None)


async def transcribe_upload(data: bytes, reader=None) -> OcrResult:
    if not data:
        raise OcrError("The file is empty.", 400)
    if len(data) > MAX_UPLOAD_BYTES:
        raise OcrError(f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.", 413)
    models = configured_models()
    if not models:
        raise OcrError("Handwriting transcription is not configured.", 503)
    pages = await asyncio.to_thread(split_pages, data)

    async def one(p: Dict[str, Any]) -> OcrPage:
        if p["text"] is not None:  # typed PDF page: exact text, nothing to verify
            return OcrPage(p["page"], "text-layer", [OcrLine(l.strip()) for l in p["text"].splitlines() if l.strip()])
        page = await read_page(p["image"], models, reader)
        page.page = p["page"]
        return page

    out = await asyncio.gather(*[one(p) for p in pages])
    return OcrResult(pages=list(out), engines=models, verified=all(p.cross_checked for p in out))
