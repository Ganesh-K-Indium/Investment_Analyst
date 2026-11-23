"""
Turn any file a user brings into the one shape the rest of the system reads.

    PDF, scans, photos   -> a PDF for the folded-in extraction engine (app/docproc): digital pages are read by code, scans by local
                            OCR with a vision model only where that is doubtful. Images are wrapped into a PDF first.
    XLSX, CSV, TSV       -> read natively, no OCR: each sheet is a page, each block of rows a table with its header and cell range
    DOCX, PPTX, HTML     -> read natively: headings, paragraphs, lists and tables in order (a slide is a page)
    TXT, MD              -> paragraphs, with Markdown headings

Native readers return the same result shape the engine does ({"pages": [...], "documents": [...], ...}) so chunking, review and the
inspector treat every file alike. Native content is exact, so it never needs review.
"""
import csv
import io
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PDF_LIKE = {"pdf", "png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp", "gif"}
NATIVE = {"xlsx", "xlsm", "csv", "tsv", "docx", "pptx", "html", "htm", "txt", "md", "markdown"}
SUPPORTED = PDF_LIKE | NATIVE

PAGE_CHARS = 3500          # a long native document is cut into pages of about this many characters, so citations point somewhere
MAX_TABLE_ROWS = 400       # rows per table block; longer sheets continue in the next block
MAX_CELL_CHARS = 500


class UnsupportedFile(ValueError):
    pass


def extension(filename: str) -> str:
    return Path(filename or "").suffix.lower().lstrip(".")


def sniff(filename: str, head: bytes) -> str:
    """The extension to treat the file as. The name decides, except that PDFs are recognised by their header whatever they are called,
    and a file claiming to be one format but starting like a PDF is read as a PDF."""
    ext = extension(filename)
    if head.startswith(b"%PDF"):
        return "pdf"
    if ext not in SUPPORTED:
        raise UnsupportedFile(f"{ext or 'This'} files are not supported. Upload PDF, an image, Excel/CSV, Word, PowerPoint, HTML or text.")
    if ext in ("xlsx", "xlsm", "docx", "pptx") and not head.startswith(b"PK"):
        raise UnsupportedFile(f"This does not look like a real .{ext} file (it may be an older format: save it as .{ext} and upload again).")
    if ext == "pdf":
        raise UnsupportedFile("This file is named .pdf but is not a PDF.")
    return ext


# --------------------------------------------------------------------------------------------------------------------
# images -> PDF
# --------------------------------------------------------------------------------------------------------------------

def image_to_pdf(src: str, dst: str) -> int:
    """Wrap an image (or a multi-page TIFF) into a PDF, one page per frame. Returns the page count."""
    import fitz
    with fitz.open(src) as img:
        pdf_bytes = img.convert_to_pdf()
    with fitz.open("pdf", pdf_bytes) as pdf:
        n = pdf.page_count
        pdf.save(dst)
    return n


# --------------------------------------------------------------------------------------------------------------------
# result scaffolding
# --------------------------------------------------------------------------------------------------------------------

def _clean(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).replace("\r", " ").replace("\n", " ").strip()
    return s[:MAX_CELL_CHARS]


def _block_text(b: Dict[str, Any]) -> str:
    if b["type"] == "table":
        head = " | ".join(b["columns"])
        return "\n".join([head] + [" | ".join(r) for r in b["rows"]])
    if b["type"] == "list":
        return "\n".join(f"- {i}" for i in b["items"])
    return b.get("text", "")


def _page(n: int, blocks: List[Dict[str, Any]], label: Optional[str] = None) -> Dict[str, Any]:
    text = "\n\n".join(t for t in (_block_text(b) for b in blocks) if t)
    lines = [{"text": l, "status": "agreed", "alternatives": [], "reason": None} for l in text.splitlines() if l.strip()]
    return {"page": n, "source": "native", "verified": True, "scanned": False, "native": True, "handwritten": False, "has_text_layer": True,
            "size": None, "page_meta": {}, "text": text, "lines": lines, "tables": [], "blocks": [], "form_widgets": [], "route": {"reader": "native", "reasons": ["the file's own content is read directly"]},
            "reading": {"engine": "native"}, "classification": {"type": "unknown", "label": label or "Document", "confidence": 1.0, "by": "native"},
            "structure": {"blocks": blocks, "entities": [], "layout": False}}


def _result(pages: List[Dict[str, Any]], label: str) -> Dict[str, Any]:
    pages = [p for p in pages if p["text"].strip()] or []
    for i, p in enumerate(pages, 1):
        p["page"] = i
    docs = [{"doc_index": 0, "type": "unknown", "label": label, "pages": [p["page"] for p in pages],
             "route": {"extractor": "native", "reasons": ["read directly from the file, no OCR"]}}] if pages else []
    return {"pages": pages, "documents": docs, "forms": [], "other_documents": [], "validation": [], "rest_checked": [],
            "stats": {"pages": len(pages), "forms": 0, "figures": 0, "needs_review": 0, "errors": 0, "failed_pages": 0, "digital_pages": 0, "scanned_pages": 0,
                      "local_ocr_pages": 0, "vision_pages": 0, "escalated_pages": 0, "region_pages": 0, "handwritten_pages": 0, "unverified_tables": 0, "native_pages": len(pages)},
            "reading": {"strategy": "native", "mode": "native", "uses_models": False, "local_engine": None, "models": [], "notes": [], "fingerprint": "native"}}


def _page_up(blocks: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Group blocks into pages of about PAGE_CHARS characters; a heading never ends a page."""
    pages, cur, size = [], [], 0
    for b in blocks:
        t = len(_block_text(b))
        if cur and size + t > PAGE_CHARS and cur[-1]["type"] != "heading":
            pages.append(cur)
            cur, size = [], 0
        cur.append(b)
        size += t
    if cur:
        pages.append(cur)
    return pages


# --------------------------------------------------------------------------------------------------------------------
# spreadsheets
# --------------------------------------------------------------------------------------------------------------------

def _col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _tables_from_grid(grid: List[List[str]], sheet: str, first_row: int = 1) -> List[Dict[str, Any]]:
    """Split a sheet's rows into tables at blank rows; the first row of each is its header. Each table keeps its cell range."""
    tables: List[Dict[str, Any]] = []
    cur: List[Tuple[int, List[str]]] = []

    def flush():
        nonlocal cur
        if not cur:
            return
        width = max(len(r) for _, r in cur)
        rows = [r + [""] * (width - len(r)) for _, r in cur]
        # drop columns that are empty all the way down
        keep = [j for j in range(width) if any(r[j] for r in rows)]
        rows = [[r[j] for j in keep] for r in rows]
        if len(rows) == 1 and len([c for c in rows[0] if c]) <= 1:      # a lone title cell is a heading, not a table
            tables.append({"type": "heading", "text": next(c for c in rows[0] if c)})
            cur = []
            return
        header, body = rows[0], rows[1:] or [[""] * len(rows[0])]
        for k in range(0, len(body), MAX_TABLE_ROWS):
            chunk = body[k:k + MAX_TABLE_ROWS]
            top = cur[0][0] + 2 + k                      # the header is row cur[0][0] + 1 (spreadsheet rows count from 1)
            bottom = top + len(chunk) - 1
            cols = [_col_letter(j) for j in keep]
            tables.append({"type": "table", "title": sheet, "header": True, "method": "native", "sheet": sheet,
                           "columns": [h or f"Column {i + 1}" for i, h in enumerate(header)], "rows": chunk,
                           "range": f"{cols[0]}{top}:{cols[-1]}{bottom}"})
        cur = []

    for idx, row in enumerate(grid):
        if any(row):
            cur.append((first_row - 1 + idx, row))
        else:
            flush()
    flush()
    return tables


def read_xlsx(path: str) -> Dict[str, Any]:
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True, read_only=True)
    pages = []
    for ws in wb.worksheets:
        grid = [[_clean(c) for c in row] for row in ws.iter_rows(values_only=True)]
        while grid and not any(grid[-1]):
            grid.pop()
        if not grid:
            continue
        blocks: List[Dict[str, Any]] = [{"type": "heading", "text": f"Sheet: {ws.title}"}] + _tables_from_grid(grid, ws.title)
        pages.append(_page(0, blocks, f"Sheet {ws.title}"))
    wb.close()
    return _result(pages, "Spreadsheet")


def read_csv(path: str, delimiter: Optional[str] = None) -> Dict[str, Any]:
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8-sig", errors="replace")
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    grid = [[_clean(c) for c in row] for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
    name = Path(path).stem
    blocks = [{"type": "heading", "text": name}] + _tables_from_grid(grid, name)
    return _result([_page(0, blocks, name)], "Table")


# --------------------------------------------------------------------------------------------------------------------
# Word, PowerPoint, HTML, text
# --------------------------------------------------------------------------------------------------------------------

def _table_block(rows: List[List[str]], title: Optional[str] = None) -> Optional[Dict[str, Any]]:
    rows = [[_clean(c) for c in r] for r in rows if any(_clean(c) for c in r)]
    if not rows:
        return None
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header, body = rows[0], rows[1:] or [[""] * width]
    return {"type": "table", "title": title, "header": True, "method": "native", "columns": [h or f"Column {i + 1}" for i, h in enumerate(header)], "rows": body[:MAX_TABLE_ROWS]}


def read_docx(path: str) -> Dict[str, Any]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    d = docx.Document(path)
    blocks: List[Dict[str, Any]] = []
    items: List[str] = []

    def flush_list():
        nonlocal items
        if items:
            blocks.append({"type": "list", "items": items})
            items = []

    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(el, d)
            t = p.text.strip()
            if not t:
                continue
            style = (p.style.name or "").lower() if p.style is not None else ""
            if style.startswith("list"):
                items.append(t)
                continue
            flush_list()
            blocks.append({"type": "heading", "text": t} if (style.startswith("heading") or style == "title") else {"type": "paragraph", "text": t})
        elif tag == "tbl":
            flush_list()
            tb = _table_block([[c.text for c in r.cells] for r in Table(el, d).rows])
            if tb:
                blocks.append(tb)
    flush_list()
    title = next((b["text"] for b in blocks if b["type"] == "heading"), "Document")
    return _result([_page(0, g) for g in _page_up(blocks)], title)


def read_pptx(path: str) -> Dict[str, Any]:
    from pptx import Presentation
    prs = Presentation(path)
    pages = []
    for i, slide in enumerate(prs.slides, 1):
        blocks: List[Dict[str, Any]] = []
        title = slide.shapes.title.text.strip() if slide.shapes.title is not None and slide.shapes.title.has_text_frame else ""
        blocks.append({"type": "heading", "text": title or f"Slide {i}"})
        for sh in slide.shapes:
            if sh.has_text_frame and sh != slide.shapes.title:
                paras = [p.text.strip() for p in sh.text_frame.paragraphs if p.text.strip()]
                if len(paras) > 1:
                    blocks.append({"type": "list", "items": paras})
                elif paras:
                    blocks.append({"type": "paragraph", "text": paras[0]})
            if getattr(sh, "has_table", False) and sh.has_table:
                tb = _table_block([[c.text for c in r.cells] for r in sh.table.rows])
                if tb:
                    blocks.append(tb)
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            note = slide.notes_slide.notes_text_frame.text.strip()
            if note:
                blocks.append({"type": "paragraph", "text": f"Speaker notes: {note}"})
        pages.append(_page(i, blocks, title or f"Slide {i}"))
    return _result(pages, "Presentation")


def read_html(path: str) -> Dict[str, Any]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(Path(path).read_bytes(), "lxml")
    for t in soup(["script", "style", "noscript", "nav", "footer"]):
        t.decompose()
    root = soup.body or soup
    blocks: List[Dict[str, Any]] = []
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "ul", "ol", "table", "pre"]):
        if el.find_parent("table") is not None and el.name != "table":
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            t = el.get_text(" ", strip=True)
            if t:
                blocks.append({"type": "heading", "text": t})
        elif el.name in ("ul", "ol"):
            its = [li.get_text(" ", strip=True) for li in el.find_all("li", recursive=False) if li.get_text(strip=True)]
            if its:
                blocks.append({"type": "list", "items": its})
        elif el.name == "table":
            tb = _table_block([[c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])] for tr in el.find_all("tr")])
            if tb:
                blocks.append(tb)
        else:
            t = el.get_text(" ", strip=True)
            if t:
                blocks.append({"type": "paragraph", "text": t})
    title = (soup.title.get_text(strip=True) if soup.title else None) or next((b["text"] for b in blocks if b["type"] == "heading"), "Web page")
    return _result([_page(0, g) for g in _page_up(blocks)], title)


def read_text(path: str, markdown: bool = False) -> Dict[str, Any]:
    text = Path(path).read_bytes().decode("utf-8-sig", errors="replace")
    blocks: List[Dict[str, Any]] = []
    for para in re.split(r"\n\s*\n", text.strip()):
        para = para.strip()
        if not para:
            continue
        m = re.match(r"^#{1,4}\s+(.+)$", para) if markdown else None
        blocks.append({"type": "heading", "text": m.group(1).strip()} if m and "\n" not in para else {"type": "paragraph", "text": para})
    title = next((b["text"] for b in blocks if b["type"] == "heading"), Path(path).stem)
    return _result([_page(0, g) for g in _page_up(blocks)], title)


def read_native(path: str, ext: str) -> Dict[str, Any]:
    if ext in ("xlsx", "xlsm"):
        return read_xlsx(path)
    if ext in ("csv", "tsv"):
        return read_csv(path, "\t" if ext == "tsv" else None)
    if ext == "docx":
        return read_docx(path)
    if ext == "pptx":
        return read_pptx(path)
    if ext in ("html", "htm"):
        return read_html(path)
    if ext in ("md", "markdown"):
        return read_text(path, markdown=True)
    return read_text(path)
