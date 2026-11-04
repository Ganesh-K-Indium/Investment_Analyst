"""
Per-page JSON for the Document Inspector (docs/DOCUMENT_INSPECTOR_PLAN.md §5.3).

Text, tables and image analyses come from the Qdrant chunks (exactly what the RAG indexed); geometry
(blocks / table / image bboxes) comes from PyMuPDF and is only present when the PDF is on disk.
"""
import re
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from qdrant_client import models

from app.database.models import Document
from app.services.documents.pdf import FITZ_LOCK, pdf_path_for

TABLES_MARKER = "\n\n[TABLES]\n"
_XREF_RE = re.compile(r"-page\d+-(\d+)$")
_SUMMARY_RE = re.compile(r"\*\*SEARCHABLE SUMMARY:\*\*\s*(.+?)(?:\n\n|\Z)", re.S)
_TYPE_RE = re.compile(r"\*\*Image Type:\*\*\s*(.+)")
_CACHE_MAX = 256
_cache: "OrderedDict[Tuple, Dict[str, Any]]" = OrderedDict()


# ---------------------------------------------------------------------------
# Chunk parsing
# ---------------------------------------------------------------------------

def split_page_content(content: str) -> Tuple[str, List[str]]:
    """Indexed page text -> (plain text, [table markdown, ...]); ingestion appends '[TABLES]' + markdown."""
    content = content or ""
    if content.startswith(TABLES_MARKER.lstrip("\n")):  # a table-only chunk (the splitter ate the leading blank line)
        content = "\n\n" + content
    text, _, tables = content.partition(TABLES_MARKER)
    return text, [t for t in tables.split("\n\n") if t.strip()] if tables else []


MIN_OVERLAP = 40


def _overlap(a: str, b: str) -> int:
    """Length of the longest suffix of `a` that is a prefix of `b` (the splitter's chunk overlap)."""
    for k in range(min(len(a), len(b), 2500), MIN_OVERLAP - 1, -1):
        if a.endswith(b[:k]):
            return k
    return 0


def merge_text_chunks(items: List[Tuple[Optional[int], str]]) -> str:
    """Rebuild one page's text from its chunks: put them in reading order and drop the duplicated overlap.

    `items` = [(chunk_index or None, text)]. Chunks written by the current ingestion carry chunk_index; older
    ones are chained by matching each chunk's tail with the next chunk's head.
    """
    items = [(i, t) for i, t in items if t.strip()]
    if len(items) <= 1:
        return items[0][1] if items else ""
    if all(i is not None for i, _ in items):
        ordered = [t for _, t in sorted(items, key=lambda x: x[0])]
    else:
        texts = [t for _, t in items]
        score = {(a, b): _overlap(texts[a], texts[b]) for a in range(len(texts)) for b in range(len(texts)) if a != b}
        incoming = {b: max((score[(a, b)] for a in range(len(texts)) if a != b), default=0) for b in range(len(texts))}
        remaining = set(range(len(texts)))
        cur = min(remaining, key=lambda b: (incoming[b], b))
        order = [cur]
        remaining.discard(cur)
        while remaining:
            nxt = max(remaining, key=lambda b: (score[(cur, b)], -incoming[b], -b))
            if score[(cur, nxt)] < MIN_OVERLAP:
                nxt = min(remaining, key=lambda b: (incoming[b], b))
            order.append(nxt)
            remaining.discard(nxt)
            cur = nxt
        ordered = [texts[i] for i in order]
    merged = ordered[0]
    for t in ordered[1:]:
        k = _overlap(merged, t)
        merged += t[k:] if k else "\n" + t
    return merged


def parse_image_chunk(meta: Dict[str, Any], content: str) -> Dict[str, Any]:
    """Image chunk -> {image_id, type, summary, analysis, xref}; bbox is filled in from the PDF later."""
    summary = _SUMMARY_RE.search(content or "")
    kind = _TYPE_RE.search(content or "")
    xref = _XREF_RE.search(meta.get("image_source_in_file") or "")
    return {
        "image_id": meta.get("image_content_hash"),
        "type": kind.group(1).strip() if kind else None,
        "summary": summary.group(1).strip() if summary else None,
        "analysis": content,
        "xref": int(xref.group(1)) if xref else None,
        "bbox": None,
    }


def _flt(doc_id: str, page: Optional[int] = None) -> models.Filter:
    must = [models.FieldCondition(key="metadata.content_hash", match=models.MatchValue(value=doc_id))]
    if page is not None:
        must.append(models.FieldCondition(key="metadata.page_num", match=models.MatchValue(value=page)))
    return models.Filter(must=must)


def _scroll_all(client, collection: str, flt: models.Filter, payload: List[str]) -> list:
    out, offset = [], None
    while True:
        points, offset = client.scroll(collection, scroll_filter=flt, limit=256, offset=offset,
                                       with_payload=payload, with_vectors=False)
        out.extend(points)
        if offset is None:
            return out


# ---------------------------------------------------------------------------
# Geometry (PyMuPDF)
# ---------------------------------------------------------------------------

def _round(rect) -> List[float]:
    return [round(float(v), 2) for v in rect]


def _live_tables(page, page_num: int) -> List[Dict[str, Any]]:
    """Tables re-extracted from the PDF with the ingestion extractor (word-position rebuild for financial tables).
    Used for display so the Inspector shows full row labels / multi-row headers even for pages indexed with the
    older, lossier extraction."""
    try:
        from ingestion.table_extractor import _numbers, extract_page_tables_with_bbox, table_to_markdown
        out = []
        for t, bb in extract_page_tables_with_bbox(page, page_num):
            # Accuracy check: every number printed inside the table area must appear in the extracted cells.
            x0, y0, x1, y1 = bb
            printed = _numbers(" ".join(w[4] for w in page.get_text("words")
                                        if w[0] >= x0 - 1 and w[2] <= x1 + 1 and w[1] >= y0 - 1 and w[3] <= y1 + 1))
            cells = [h for h in t["headers"] if not re.fullmatch(r"(Col \d+|Line item)", h)] + [c for r in t["rows"] for c in r] + [t.get("title") or ""]
            missing = sum((printed - _numbers(" ".join(cells))).values())
            total = sum(printed.values())
            out.append({"markdown": table_to_markdown(t), "headers": t["headers"], "rows": t["rows"], "bbox": _round(bb),
                        "verified": {"numbers": total, "matched": total - missing}})
        return out
    except Exception:
        return []


@lru_cache(maxsize=128)
def _geometry(path: str, mtime: float, page_num: int, xrefs: Tuple[int, ...]) -> Dict[str, Any]:
    """size, text blocks, table bboxes and image bboxes of one page. `mtime` only busts the cache on file change."""
    import fitz
    with FITZ_LOCK, fitz.open(path) as pdf:
        page = pdf[page_num - 1]
        blocks = [{"id": f"b{b[5]}", "bbox": _round(b[:4]), "text": b[4].strip()}
                  for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
        tables = _live_tables(page, page_num)
        images = {}
        for xref in xrefs:
            try:
                rects = page.get_image_rects(xref)
            except Exception:
                rects = []
            if rects:
                images[xref] = _round(rects[0])
        return {"size": {"width": round(page.rect.width, 2), "height": round(page.rect.height, 2)},
                "blocks": blocks, "tables": tables, "images": images}


# ---------------------------------------------------------------------------
# Public API (sync; call via asyncio.to_thread)
# ---------------------------------------------------------------------------

def build_page(client, doc: Document, page_num: int) -> Dict[str, Any]:
    path = pdf_path_for(doc)
    mtime = path.stat().st_mtime if path else None
    key = (doc.doc_id, page_num, mtime)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]

    points = _scroll_all(client, doc.collection, _flt(doc.doc_id, page_num), ["page_content", "metadata"])
    texts, tables_md, images, chunks = [], [], [], []
    for p in sorted(points, key=lambda p: str(p.id)):
        payload = p.payload or {}
        meta, content = payload.get("metadata") or {}, payload.get("page_content") or ""
        ctype = meta.get("content_type")
        chunks.append({"point_id": str(p.id), "content_type": ctype})
        if ctype == "image":
            images.append(parse_image_chunk(meta, content))
        else:
            text, tables = split_page_content(content)
            texts.append((meta.get("chunk_index"), text))
            tables_md.extend(tables)

    geo = None
    if path:
        geo = _geometry(str(path), mtime, page_num, tuple(i["xref"] for i in images if i["xref"]))
    live_tables = geo["tables"] if geo else []
    for img in images:
        xref = img.pop("xref")
        img["bbox"] = geo["images"].get(xref) if geo and xref else None

    result = {
        "doc_id": doc.doc_id,
        "page": page_num,
        "page_count": doc.page_count,
        "size": geo["size"] if geo else None,
        "text": merge_text_chunks(texts),
        # Prefer tables rebuilt from the PDF; fall back to the indexed markdown when the PDF is unavailable.
        "tables": ([{**t, "source": "pdf"} for t in live_tables] if live_tables else
                   [{"markdown": md, "headers": None, "rows": None, "bbox": None, "verified": None, "source": "index"} for md in tables_md]),
        "images": images,
        "blocks": geo["blocks"] if geo else [],
        "chunks": chunks,
        "meta": {
            "ticker": doc.ticker, "form_type": doc.form_type, "fiscal_year": doc.fiscal_year,
            "ingested_at": doc.ingested_at.isoformat() if doc.ingested_at else None,
            "pdf_available": path is not None,
            "aligned": doc.page_fingerprints.get("aligned") if isinstance(doc.page_fingerprints, dict) else None,
        },
    }
    _cache[key] = result
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return result


def build_page_index(client, doc: Document) -> List[Dict[str, Any]]:
    """Lightweight per-page summary for the thumbnail rail: chars / has_tables / has_images for every page."""
    pages: Dict[int, Dict[str, Any]] = {n: {"page": n, "chars": 0, "has_tables": False, "has_images": False}
                                        for n in range(1, doc.page_count + 1)}
    for p in _scroll_all(client, doc.collection, _flt(doc.doc_id), ["page_content", "metadata"]):
        payload = p.payload or {}
        meta = payload.get("metadata") or {}
        n = meta.get("page_num")
        if n not in pages:
            continue
        if meta.get("content_type") == "image":
            pages[n]["has_images"] = True
        else:
            text, tables = split_page_content(payload.get("page_content") or "")
            pages[n]["chars"] += len(text)
            pages[n]["has_tables"] = pages[n]["has_tables"] or bool(tables)
    return [pages[n] for n in sorted(pages)]


# ---------------------------------------------------------------------------
# Cited-text locate (Phase 8): quote -> rects on the PDF page
# ---------------------------------------------------------------------------

_NORM_RE = re.compile(r"[^a-z0-9]+")
FUZZY_MIN_RATIO = 0.75


def _norm_tokens(text: str) -> List[str]:
    """Lowercase alphanumeric tokens; also repairs line-break hyphenation ('infor- mation')."""
    text = re.sub(r"(\w)-\s+(\w)", r"\1\2", text or "")
    return [t for t in _NORM_RE.split(text.lower()) if t]


def _merge_line_rects(words: list) -> List[List[float]]:
    """Union word boxes per (block, line) -> one rect per visual line."""
    lines: Dict[Tuple[int, int], List[float]] = {}
    for w in words:
        r = lines.setdefault((w[5], w[6]), [w[0], w[1], w[2], w[3]])
        r[0], r[1], r[2], r[3] = min(r[0], w[0]), min(r[1], w[1]), max(r[2], w[2]), max(r[3], w[3])
    return [_round(r) for r in lines.values()]


def _locate_words(words: list, q_tokens: List[str]) -> Optional[Tuple[list, float]]:
    """Best sliding window of page words for the quote (token-set prefilter, then SequenceMatcher)."""
    from difflib import SequenceMatcher
    toks = [(_norm_tokens(w[4]) or [""])[0] for w in words]
    n = len(q_tokens)
    if n == 0 or len(toks) < max(3, n // 2):
        return None
    q_set, q_joined = set(q_tokens), " ".join(q_tokens)
    scored = []
    for i in range(0, max(1, len(toks) - n + 1)):
        window = toks[i:i + n]
        scored.append((len(q_set.intersection(window)), i))
    best, best_ratio = None, 0.0
    for _, i in sorted(scored, reverse=True)[:12]:
        ratio = SequenceMatcher(None, q_joined, " ".join(toks[i:i + n]), autojunk=False).ratio()
        if ratio > best_ratio:
            best, best_ratio = i, ratio
    if best is None or best_ratio < FUZZY_MIN_RATIO:
        return None
    return words[best:best + n], best_ratio


@lru_cache(maxsize=256)
def _locate(path: str, mtime: float, page_num: int, quote: str) -> Dict[str, Any]:
    import fitz
    with FITZ_LOCK, fitz.open(path) as pdf:
        page = pdf[page_num - 1]
        words = page.get_text("words")
        if not words:
            return {"method": "no_text_layer", "rects": [], "score": 0.0}
        q_tokens = _norm_tokens(quote)
        phrase = " ".join(quote.split())
        rects = page.search_for(phrase) if phrase else []
        if rects:
            return {"method": "exact", "rects": [_round(r) for r in rects], "score": 1.0}
        hit = _locate_words(words, q_tokens)
        if hit:
            matched, score = hit
            return {"method": "fuzzy", "rects": _merge_line_rects(matched), "score": round(score, 3)}
        return {"method": "not_found", "rects": [], "score": 0.0}


def locate_quote(doc: Document, page_num: int, quote: str) -> Dict[str, Any]:
    path = pdf_path_for(doc)
    if not path:
        return {"method": "no_pdf", "rects": [], "score": 0.0}
    return _locate(str(path), path.stat().st_mtime, page_num, " ".join((quote or "").split())[:1200])
