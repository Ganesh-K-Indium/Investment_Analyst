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
from app.core.config import get_settings

logger = logging.getLogger("app.services.tess")

DPI = 300
TIMEOUT_SECONDS = 90
LANG = get_settings().tesseract_lang


def binary() -> Optional[str]:
    """TESSERACT_CMD, else PATH, else (Windows) the installer's default folders, so a standard install needs no setting."""
    found = get_settings().tesseract_cmd or shutil.which("tesseract")
    if found or os.name != "nt":
        return found
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        exe = Path(base or "") / "Tesseract-OCR" / "tesseract.exe"
        if base and exe.is_file():
            return str(exe)
    return None


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


SKEW_MAX = 3.0          # degrees: a page more tilted than this is a photo at an angle, not a scanner's skew
SKEW_MIN = 0.12         # degrees: a smaller tilt moves a line by less than a pixel across the page
SKEW_GAIN = 1.06        # the straightened page's line profile must be this much sharper than the original's to be trusted


def _sharpness(im: Any) -> float:
    """How strongly the ink falls into rows: the spread of the row sums. Straight text lines make tall peaks and empty gaps."""
    import numpy as np
    rows = np.asarray(im, dtype=np.float32).sum(axis=1)
    return float(rows.var())


def skew_angle(png: bytes) -> float:
    """The tilt of the page's text lines in degrees (positive = counter-clockwise needed to straighten), 0.0 when it is level or unclear.
    Found by trying small rotations of a reduced, ink-only copy and keeping the one whose rows are most distinct."""
    from io import BytesIO
    from PIL import Image
    try:
        im = Image.open(BytesIO(png)).convert("L")
        scale = 900.0 / max(im.width, 1)
        if scale < 1:
            im = im.resize((900, max(1, int(im.height * scale))))
        cut = _otsu(im)
        ink = im.point(lambda v: 255 if v <= cut else 0)         # ink is white on black, so rows of text add up
        if sum(ink.histogram()[200:]) < 0.004 * ink.width * ink.height:
            return 0.0                                           # almost no ink: nothing to align
        base = _sharpness(ink)
        best, best_v = 0.0, base
        for step, span, centre in ((0.5, SKEW_MAX, 0.0), (0.1, 0.5, None), (0.03, 0.1, None)):
            c = best if centre is None else centre
            a = c - span
            while a <= c + span + 1e-9:
                v = _sharpness(ink.rotate(a, resample=Image.BILINEAR, fillcolor=0))
                if v > best_v:
                    best, best_v = a, v
                a += step
        if abs(best) < SKEW_MIN or abs(best) > SKEW_MAX - 0.4 or best_v < base * SKEW_GAIN:      # level, or at the edge of the search (not a real answer)
            return 0.0
        return round(best, 2)
    except Exception:
        return 0.0


ANGLES: "dict[tuple, float]" = {}       # (source path, page) -> the tilt taken out of its OCR copy, so every later render of that page is straightened the same way


def rotated(png: bytes, angle: float) -> bytes:
    """The page picture turned by `angle` degrees to take a tilt out (white fills the corners)."""
    from io import BytesIO
    from PIL import Image
    if not angle:
        return png
    im = Image.open(BytesIO(png))
    # the corners the turn uncovers take the colour of the paper (not pure white): a dim or unevenly lit photo would otherwise get bright corners
    # that fool every later estimate of the light on the page
    paper = int(sorted(im.convert("L").resize((64, 64)).getdata())[int(64 * 64 * 0.9)])
    fill = paper if im.mode == "L" else tuple([paper] * len(im.getbands())) if im.mode in ("RGB", "RGBA") else 255
    out = BytesIO()
    im.rotate(angle, resample=Image.BICUBIC, fillcolor=fill).save(out, "PNG")
    return out.getvalue()


def straightened(png: bytes) -> "tuple[bytes, float]":
    """(the page picture with its tilt taken out, the angle removed). A level page comes back untouched, and so does every page when
    DOCPROC_DESKEW is off. A tilt of even a degree makes OCR break rows apart, so a label and its figures land on different lines."""
    if not get_settings().docproc_deskew:
        return png, 0.0
    angle = skew_angle(png)
    return (rotated(png, angle), angle) if angle else (png, 0.0)


def render_png(page: "fitz.Page") -> bytes:
    return straightened(page.get_pixmap(dpi=DPI).tobytes("png"))[0]


def searchable_page_with_words(page: "fitz.Page", key: "Optional[tuple]" = None) -> "tuple[fitz.Document, str]":
    """A one-page document: `page`'s picture (straightened) with Tesseract's text laid over it, at `page`'s size; plus the word table.
    `key` names the page for later renders, which must take out the same tilt (see ANGLES)."""
    png, angle = straightened(page.get_pixmap(dpi=DPI).tobytes("png"))
    if key is not None:
        ANGLES[key] = angle
    pdf_bytes, tsv = png_to_pdf_and_words(png)
    ocr = fitz.open("pdf", pdf_bytes)
    out = fitz.open()
    new = out.new_page(width=page.rect.width, height=page.rect.height)
    new.show_pdf_page(new.rect, ocr, 0)
    ocr.close()
    return out, tsv


def searchable_page(page: "fitz.Page") -> "fitz.Document":
    return searchable_page_with_words(page)[0]
