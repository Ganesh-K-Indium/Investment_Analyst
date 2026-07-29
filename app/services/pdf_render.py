"""
Renders sanitized report content_html into an fpdf2 PDF, preserving inline
color/highlight/bold/italic/underline/strikethrough and real bordered tables.

fpdf2's own `write_html()` can't do this faithfully: it only applies inline
`style="color:..."` on heading tags, has no concept of inline background
(highlight) at all, doesn't understand `rgb()`/`rgba()` CSS color syntax
(only hex/named colors), and crashes on TipTap's `width="450px"`-style
image attributes (it expects a bare number). This walks the sanitized HTML
directly with BeautifulSoup and lays text out word-by-word via fpdf2's
low-level `cell()`/`table()`/`image()` primitives instead.
"""
import os
import re
import tempfile
import urllib.request
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup, NavigableString, Tag
from fpdf import FPDF
from fpdf.fonts import FontFace

LINE_HEIGHT = 6.0
HEADING_SIZE = {1: 20, 2: 16, 3: 13, 4: 12, 5: 11, 6: 11}
BODY_SIZE = 11
PX_TO_MM = 25.4 / 96

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGB_RE = re.compile(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*[\d.]+\s*)?\)")

_INLINE_BOLD = {"strong", "b"}
_INLINE_ITALIC = {"em", "i"}
_INLINE_UNDERLINE = {"u"}
_INLINE_STRIKE = {"s", "del", "strike"}
_TRANSPARENT_WRAPPERS = {"div", "span", "body", "html", "a"}


def _parse_color(value: str) -> Optional[Tuple[int, int, int]]:
    if not value:
        return None
    value = value.strip()
    m = _RGB_RE.match(value)
    if m:
        return tuple(min(255, max(0, int(float(g)))) for g in m.groups())  # type: ignore[return-value]
    if _HEX_RE.match(value):
        h = value.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    return None


def _style_dict(tag: Tag) -> dict:
    style_attr = tag.get("style") or ""
    out = {}
    for decl in style_attr.split(";"):
        if ":" not in decl:
            continue
        k, v = decl.split(":", 1)
        out[k.strip().lower()] = v.strip()
    return out


class Run:
    __slots__ = ("text", "bold", "italic", "underline", "strike", "color", "highlight")

    def __init__(self, text, bold=False, italic=False, underline=False, strike=False, color=None, highlight=None):
        self.text = text
        self.bold = bold
        self.italic = italic
        self.underline = underline
        self.strike = strike
        self.color = color
        self.highlight = highlight


def _collect_runs(node, bold=False, italic=False, underline=False, strike=False, color=None, highlight=None) -> List[Run]:
    runs: List[Run] = []
    if isinstance(node, NavigableString):
        text = str(node)
        if text:
            runs.append(Run(text, bold, italic, underline, strike, color, highlight))
        return runs
    if not isinstance(node, Tag):
        return runs

    name = node.name.lower()
    if name == "br":
        runs.append(Run("\n", bold, italic, underline, strike, color, highlight))
        return runs

    b, i, u, s, c, h = bold, italic, underline, strike, color, highlight
    if name in _INLINE_BOLD:
        b = True
    if name in _INLINE_ITALIC:
        i = True
    if name in _INLINE_UNDERLINE:
        u = True
    if name in _INLINE_STRIKE:
        s = True

    style = _style_dict(node)
    parsed_color = _parse_color(style.get("color", ""))
    if parsed_color:
        c = parsed_color
    parsed_bg = _parse_color(style.get("background-color", ""))
    if parsed_bg:
        h = parsed_bg
    if style.get("font-weight") in ("bold", "600", "700", "800", "900"):
        b = True

    for child in node.children:
        runs.extend(_collect_runs(child, b, i, u, s, c, h))
    return runs


def _set_font(pdf: FPDF, size: float, run: Run) -> None:
    style = ""
    if run.bold:
        style += "B"
    if run.italic:
        style += "I"
    if run.underline:
        style += "U"
    pdf.set_font("Helvetica", style, size)
    pdf.set_text_color(*(run.color or (26, 26, 26)))


def _flow_runs(pdf: FPDF, runs: List[Run], size: float, left: float, right: float) -> None:
    """Word-wraps styled runs across the available width. Highlight is a real
    filled rectangle behind each word via cell(fill=True) — there is no
    equivalent in fpdf2's write_html."""
    if not runs:
        return
    line_height = max(LINE_HEIGHT, size / 2.2)
    pdf.set_x(left)
    for run in runs:
        for seg_idx, seg in enumerate(run.text.split("\n")):
            if seg_idx > 0:
                pdf.ln(line_height)
                pdf.set_x(left)
            if not seg:
                continue
            _set_font(pdf, size, run)
            for tok in re.findall(r"\S+\s*|\s+", seg):
                w = pdf.get_string_width(tok)
                if w <= 0:
                    continue
                if pdf.get_x() + w > right and tok.strip():
                    pdf.ln(line_height)
                    pdf.set_x(left)
                if run.highlight:
                    pdf.set_fill_color(*run.highlight)
                x_start = pdf.get_x()
                pdf.cell(w, line_height, tok, fill=bool(run.highlight))
                if run.strike and tok.strip():
                    y = pdf.get_y() + line_height / 2
                    pdf.set_draw_color(*(run.color or (26, 26, 26)))
                    pdf.line(x_start, y, pdf.get_x(), y)
    pdf.ln(line_height)
    pdf.set_text_color(0, 0, 0)


def _cell_style(td: Tag) -> Tuple[str, "FontFace"]:
    runs = _collect_runs(td)
    text = "".join(r.text for r in runs).strip()
    bold = td.name == "th" or any(r.bold for r in runs)
    color = next((r.color for r in runs if r.color), None) or (26, 26, 26)
    # fpdf2's table doesn't reset fill state between cells when fill_color is
    # left unset — an explicit white default (instead of None) stops one
    # highlighted cell's color from bleeding into every other cell in the table.
    fill = next((r.highlight for r in runs if r.highlight), None) or (255, 255, 255)
    if td.name == "th" and fill == (255, 255, 255):
        fill = (240, 240, 240)
    style = FontFace(emphasis="B" if bold else "", color=color, fill_color=fill)
    return text, style


def _render_table(pdf: FPDF, table_node: Tag) -> None:
    rows = [tr.find_all(["td", "th"]) for tr in table_node.find_all("tr")]
    rows = [r for r in rows if r]
    if not rows:
        return
    pdf.ln(2)
    pdf.set_font("Helvetica", "", BODY_SIZE - 1)
    with pdf.table(borders_layout="ALL") as pdf_table:
        for cells in rows:
            row = pdf_table.row()
            for td in cells:
                text, style = _cell_style(td)
                row.cell(text, style=style)
    pdf.ln(2)


def _render_image(pdf: FPDF, img: Tag, left: float, right: float) -> None:
    src = img.get("src")
    if not src:
        return
    max_width = right - left
    align = (img.get("align") or "center").lower()
    tmp_path = None
    try:
        suffix = ".png" if ".png" in src.lower() else ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name
        urllib.request.urlretrieve(src, tmp_path)

        from PIL import Image as PILImage
        with PILImage.open(tmp_path) as im:
            nat_w_px, nat_h_px = im.size

        width_attr = img.get("width") or ""
        digits = re.sub(r"[^0-9.]", "", width_attr)
        req_w_mm = float(digits) * PX_TO_MM if digits else nat_w_px * PX_TO_MM
        w = min(req_w_mm, max_width) if req_w_mm > 0 else max_width
        h = w * (nat_h_px / nat_w_px) if nat_w_px else None

        if align == "left":
            x = left
        elif align == "right":
            x = right - w
        else:
            x = left + (max_width - w) / 2

        pdf.ln(2)
        pdf.image(tmp_path, x=x, w=w, h=h)
        pdf.set_y(pdf.get_y() + (h or 0) + 3)
    except Exception:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(130, 130, 130)
        pdf.multi_cell(max_width, 5, "[Image unavailable]")
        pdf.set_text_color(0, 0, 0)
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _render_children(pdf: FPDF, parent, left: float, right: float) -> None:
    for child in parent.children:
        _render_block(pdf, child, left, right)


def _render_block(pdf: FPDF, node, left: float, right: float) -> None:
    if isinstance(node, NavigableString):
        if str(node).strip():
            _flow_runs(pdf, _collect_runs(node), BODY_SIZE, left, right)
        return
    if not isinstance(node, Tag):
        return

    name = node.name.lower()

    if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        size = HEADING_SIZE.get(int(name[1]), 12)
        pdf.ln(2)
        _flow_runs(pdf, _collect_runs(node, bold=True), size, left, right)
        pdf.ln(1)
        return

    if name == "p":
        runs = _collect_runs(node)
        if runs:
            _flow_runs(pdf, runs, BODY_SIZE, left, right)
        else:
            pdf.ln(LINE_HEIGHT / 2)
        return

    if name in ("ul", "ol"):
        for idx, li in enumerate((c for c in node.children if isinstance(c, Tag) and c.name == "li"), start=1):
            prefix = f"{idx}. " if name == "ol" else "* "
            runs = [Run(prefix)] + _collect_runs(li)
            _flow_runs(pdf, runs, BODY_SIZE, left + 5, right)
        return

    if name == "blockquote":
        _flow_runs(pdf, _collect_runs(node, italic=True), BODY_SIZE, left + 6, right)
        return

    if name in ("pre", "code"):
        pdf.set_font("Courier", "", 9)
        pdf.set_fill_color(245, 245, 245)
        pdf.multi_cell(right - left, 5, node.get_text(), fill=True)
        pdf.set_fill_color(255, 255, 255)
        pdf.set_font("Helvetica", "", BODY_SIZE)
        return

    if name == "hr":
        pdf.set_draw_color(180, 180, 180)
        pdf.line(left, pdf.get_y(), right, pdf.get_y())
        pdf.ln(4)
        return

    if name == "table":
        _render_table(pdf, node)
        return

    if name == "img":
        _render_image(pdf, node, left, right)
        return

    # Transparent wrappers (div/span/a/...) and any unrecognized tag: recurse
    _render_children(pdf, node, left, right)


def render_content_html(pdf: FPDF, html: str) -> None:
    """Entry point — walks sanitized report HTML and draws it onto `pdf`."""
    if not html:
        return
    soup = BeautifulSoup(html, "html.parser")
    left = pdf.l_margin
    right = left + pdf.epw
    pdf.set_font("Helvetica", "", BODY_SIZE)
    _render_children(pdf, soup, left, right)
