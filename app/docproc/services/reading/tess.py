"""
Tesseract text layer for scanned pages.

A fax is a picture, so PyMuPDF sees no words and nothing downstream (highlighting, label search, checkbox pixels,
table rebuild) has anything to work with. Tesseract turns each scanned page into a one-page PDF whose invisible text
sits exactly over the picture; we lay that onto the original page geometry and call the result the "work PDF".
Every geometric step then runs on the work PDF; the viewer keeps showing the original, and the coordinates match
because the page size is carried over.

Tesseract is also usable as a reader on its own (route `ocr_local`) when no model is configured, which keeps
patient documents off third-party services if that is required.
"""
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

import pymupdf as fitz

logger = logging.getLogger("app.services.tess")

DPI = 300
TIMEOUT_SECONDS = 90
LANG = os.getenv("TESSERACT_LANG", "eng")


def binary() -> Optional[str]:
    return os.getenv("TESSERACT_CMD") or shutil.which("tesseract")


def available() -> bool:
    return binary() is not None


BLANK_INK = 0.001       # a page with less ink than this is blank (a noisy empty page measures 0.0003; a sparse real one, over 0.03)
SPARSE = 11              # Tesseract's page-segmentation mode for text scattered over a page, with no layout analysis
FEW_WORDS = 40          # a page with fewer recognised words than this, yet with ink on it, is read again on a cleaned image


def word_count(tsv: str) -> int:
    n = 0
    for line in tsv.splitlines()[1:]:
        f = line.split("\t")
        if len(f) > 11 and f[11].strip():
            try:
                if float(f[10]) > 0:
                    n += 1
            except ValueError:
                pass
    return n


def flatten(png: bytes) -> "tuple[bytes, float]":
    """The page with its lighting evened out (a phone photo is darker on one side, or has a shadow), and the share of it
    that is ink. Each pixel is compared with the paper around it, so uneven light stops hiding the text."""
    from io import BytesIO
    from PIL import Image, ImageChops, ImageFilter, ImageOps
    im = Image.open(BytesIO(png)).convert("L")
    small = im.resize((max(1, im.width // 4), max(1, im.height // 4)))
    paper = small.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(max(4, small.width // 40))).resize(im.size)
    dark = ImageChops.subtract(paper, im)                    # how much darker than the paper nearby
    ink = sum(dark.histogram()[40:]) / float(im.width * im.height)
    out = ImageOps.autocontrast(ImageOps.invert(dark), cutoff=2)
    buf = BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue(), ink


UNEVEN = 0.22           # the paper is this much darker on one side than the other: a photo, not a scan


def evened(png: bytes) -> bytes:
    """The page image as it should go to a reader: unchanged when the light is even (almost every scan), lighting-corrected when
    the paper is clearly darker on one side or in a shadow. A vision model reads a shadowed photo worse than a flat page, and
    a clean scan must never be altered."""
    from io import BytesIO
    from PIL import Image, ImageFilter
    try:
        im = Image.open(BytesIO(png)).convert("L")
        small = im.resize((max(1, im.width // 8), max(1, im.height // 8))).filter(ImageFilter.MaxFilter(7))
        hist = small.histogram()
        total, acc, lo, hi = sum(hist), 0, 0, 255
        for v, n in enumerate(hist):
            acc += n
            if acc >= total * 0.05 and lo == 0:
                lo = v
            if acc >= total * 0.95:
                hi = v
                break
        if hi <= 0 or (hi - lo) / hi < UNEVEN:
            return png
        return flatten(png)[0]
    except Exception:
        return png


def skew_angle(png: bytes, max_deg: float = 4.0) -> Optional[float]:
    """The rotation (degrees, counter-clockwise) that straightens a slightly tilted scan, or None when the page is already straight enough
    that it must not be touched. Projection profile: the right angle makes text lines line up, so the row sums become most uneven."""
    from io import BytesIO
    import numpy as np
    from PIL import Image
    try:
        im = Image.open(BytesIO(png)).convert("L")
        scale = 900 / max(im.width, 1)
        small = im.resize((900, max(1, int(im.height * scale))))
        arr = np.asarray(small, dtype=np.uint8)
        ink = (arr < max(60, int(arr.mean()) - 30)).astype(np.uint8)
        if ink.sum() < 400:
            return None
        base = Image.fromarray(ink * 255)

        def score(a: float) -> float:
            rows = np.asarray(base.rotate(a, resample=Image.NEAREST, fillcolor=0), dtype=np.float32).sum(axis=1)
            return float(np.var(rows))

        coarse = max(np.arange(-max_deg, max_deg + 0.01, 0.25), key=score)
        fine = max(np.arange(coarse - 0.25, coarse + 0.26, 0.05), key=score)
        if abs(fine) < 0.3 or score(fine) < score(0.0) * 1.05:
            return None                       # straight enough, or no clear gain: a clean scan is never altered
        return round(float(fine), 2)
    except Exception:
        return None


def deskewed(png: bytes) -> bytes:
    """The page image straightened when it is visibly tilted, otherwise unchanged. A tilt of even a degree makes OCR break rows apart, so a
    label and its figures land on different lines."""
    from io import BytesIO
    from PIL import Image
    angle = skew_angle(png)
    if angle is None:
        return png
    try:
        im = Image.open(BytesIO(png))
        fill = 255 if im.mode == "L" else (255, 255, 255)
        out = BytesIO()
        im.rotate(angle, resample=Image.BICUBIC, fillcolor=fill).save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return png


def _run(png: bytes, psm: Optional[int] = None) -> "tuple[bytes, str]":
    """One page of picture + invisible text, and Tesseract's word table (TSV) from the same run."""
    exe = binary()
    if not exe:
        raise RuntimeError("Tesseract is not installed")
    with tempfile.TemporaryDirectory() as tmp:
        src, base = Path(tmp) / "in.png", Path(tmp) / "out"
        src.write_bytes(png)
        env = {**os.environ, "OMP_THREAD_LIMIT": "1"}  # pages run in parallel; one thread each avoids oversubscription
        proc = subprocess.run([exe, str(src), str(base), "-l", LANG, "--dpi", str(DPI), *(["--psm", str(psm)] if psm else []), "pdf", "tsv"],
                              capture_output=True, timeout=TIMEOUT_SECONDS, env=env)
        out, words = base.with_suffix(".pdf"), base.with_suffix(".tsv")
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError((proc.stderr or b"").decode(errors="replace")[:300] or "Tesseract failed")
        return out.read_bytes(), (words.read_text(errors="replace") if words.exists() else "")


def _otsu(gray: Any) -> int:
    """The grey level that best separates dark strokes from light paper (Otsu's method), from a PIL grayscale image's histogram."""
    hist = gray.histogram()
    total = sum(hist)
    sum_all = sum(i * n for i, n in enumerate(hist))
    best, best_var, w0, sum0 = 128, -1.0, 0, 0.0
    for t in range(256):
        w0 += hist[t]
        if w0 == 0 or w0 == total:
            continue
        sum0 += t * hist[t]
        m0, m1 = sum0 / w0, (sum_all - sum0) / (total - w0)
        var = w0 * (total - w0) * (m0 - m1) ** 2
        if var > best_var:
            best, best_var = t, var
    return best


def _binarized(clean_png: bytes) -> bytes:
    """Black strokes on white paper, nothing between: the grain of a photograph is grey, and Tesseract can read a grainy page as noise."""
    from io import BytesIO
    from PIL import Image
    im = Image.open(BytesIO(clean_png)).convert("L")
    cut = _otsu(im)
    out = BytesIO()
    im.point(lambda v: 255 if v > cut else 0).save(out, "PNG")
    return out.getvalue()


def png_to_pdf_and_words(png: bytes) -> "tuple[bytes, str]":
    """One page of picture + invisible text, and Tesseract's word table (TSV) from the same run, so reading the page
    for its text costs no second pass. A page that comes back nearly empty although it has ink on it (a photo with uneven
    light, a shadow, grain) is read again on a cleaned copy (lighting evened, then pure black on white), and the best reading is
    kept. Raises RuntimeError with Tesseract's own message on failure."""
    best = _run(png)
    if word_count(best[1]) >= FEW_WORDS:
        return best
    try:
        clean, ink = flatten(png)
        if ink < BLANK_INK:                                  # a blank page: nothing to find
            return best
        # Two different things go wrong with a photographed page. Uneven light hides the text (a cleaned copy helps), and Tesseract's own
        # page layout can take a page with a drawing on it for one big picture and skip the text (reading it as sparse text helps).
        for make, psm in ((lambda: png, SPARSE), (lambda: clean, SPARSE), (lambda: clean, None), (lambda: _binarized(clean), None)):
            got = _run(make(), psm)
            if word_count(got[1]) > word_count(best[1]):
                best = got
            if word_count(best[1]) >= FEW_WORDS:
                break
    except Exception as e:                                   # the first reading stands
        logger.warning("Cleaned-image retry failed: %s", e)
    return best


def png_to_pdf(png: bytes) -> bytes:
    return png_to_pdf_and_words(png)[0]


def render_png(page: "fitz.Page") -> bytes:
    return page.get_pixmap(dpi=DPI).tobytes("png")


def searchable_page_with_words(page: "fitz.Page") -> "tuple[fitz.Document, str]":
    """A one-page document: `page`'s picture with Tesseract's text laid over it, at `page`'s size; plus the word table."""
    pdf_bytes, tsv = png_to_pdf_and_words(deskewed(render_png(page)))
    ocr = fitz.open("pdf", pdf_bytes)
    out = fitz.open()
    new = out.new_page(width=page.rect.width, height=page.rect.height)
    new.show_pdf_page(new.rect, ocr, 0)
    ocr.close()
    return out, tsv


def searchable_page(page: "fitz.Page") -> "fitz.Document":
    return searchable_page_with_words(page)[0]
