"""
Table-aware chunking for ingested pages.

The old pipeline joined each page's text and its `[TABLES]` markdown into one string and ran a token splitter over
it, which cut tables mid-row (a continuation chunk had numbers with no row labels or column headers) and repeated
300 tokens of overlap inside tables. Here:

  - page text is split exactly as before (1024 tokens, 300 overlap) and stays free of table markdown;
  - tables are packed whole into `[TABLES]` chunks (each with its `Table: <caption>` + header row), never cut
    mid-row; a table too large for one chunk is split by row groups with the header repeated.

Chunks carry `chunk_kind` ("text" | "table") and `chunk_index` (order within the page).
"""
import re
from typing import List, Optional

from langchain_core.documents import Document

TABLES_MARKER = "\n\n[TABLES]\n"
TABLE_PREFIX = "[TABLES]\n"
MAX_TABLE_CHARS = 3500   # ~1000 tokens of numeric markdown; larger tables are split by rows
ROWS_PER_CHUNK = 25


def make_text_splitter():
    """The ingestion text splitter (1024 tokens, 300 overlap) — shared by ingestion and the re-extract script."""
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    return RecursiveCharacterTextSplitter.from_tiktoken_encoder(chunk_size=1024, chunk_overlap=300)


def split_table_block(block: str) -> List[str]:
    """One table (optional `Table:` caption + header + separator + rows) -> chunk(s), header repeated per chunk."""
    if len(block) <= MAX_TABLE_CHARS:
        return [block]
    lines = block.split("\n")
    sep = next((i for i, l in enumerate(lines) if set(l.replace("|", "").replace(" ", "")) <= {"-", ":"} and "-" in l), None)
    if sep is None:
        return [block]
    head, rows = lines[:sep + 1], lines[sep + 1:]
    out, cur = [], []
    for row in rows:
        if cur and (len(cur) >= ROWS_PER_CHUNK or len("\n".join(head + cur + [row])) > MAX_TABLE_CHARS):
            out.append("\n".join(head + cur))
            cur = []
        cur.append(row)
    if cur:
        out.append("\n".join(head + cur))
    return out


_SECTION_RE = re.compile(r"^\s*((?:PART\s+[IVX]+\b|Item\s+\d{1,2}[A-C]?\b)[^\n]{0,90})$", re.I | re.M)


def find_section(text: str) -> Optional[str]:
    """The last Part/Item heading on a page ("Item 7. Management's Discussion and Analysis"), or None."""
    found = _SECTION_RE.findall(text or "")
    return re.sub(r"\s+", " ", found[-1]).strip(" .") if found else None


def context_header(meta: dict, section: Optional[str]) -> str:
    """One line that says what a chunk is part of, for the embedding input only (contextual retrieval): without it a chunk reading
    "Revenue increased 12%" says nothing about which company, filing or period it belongs to."""
    parts = [meta.get("company") or meta.get("ticker"), meta.get("filing_type") or meta.get("doc_type"),
             f"FY{meta['year']}" if meta.get("year") else None,
             f"quarter {meta['fiscal_quarter']}" if meta.get("fiscal_quarter") else None,
             f"period ended {meta['period_end_date']}" if meta.get("period_end_date") else None,
             f"section: {section}" if section else None,
             f"page {meta['page_num']}" if meta.get("page_num") is not None else None]
    return "[" + ", ".join(str(p) for p in parts if p) + "]" if any(parts) else ""


def embed_text(doc: Document) -> str:
    """What is embedded and keyword-indexed for a chunk: its context header (if any) plus its text. The stored text stays clean."""
    h = (doc.metadata or {}).get("context_header")
    return f"{h}\n\n{doc.page_content}" if h else doc.page_content


def chunk_page_documents(documents: List[Document], text_splitter, context_prefix: bool = False) -> List[Document]:
    chunks: List[Document] = []
    section: Optional[str] = None
    for doc in documents:
        text, _, tables = doc.page_content.partition(TABLES_MARKER)
        idx = 0
        if context_prefix:
            section = find_section(text) or section          # a heading carries forward to the pages after it
            header = context_header(doc.metadata, section)
            if header:
                doc = Document(page_content=doc.page_content, metadata={**doc.metadata, "context_header": header})
        if text.strip():
            for c in text_splitter.split_documents([Document(page_content=text, metadata=dict(doc.metadata))]):
                c.metadata = {**c.metadata, "chunk_kind": "text", "chunk_index": idx}
                chunks.append(c)
                idx += 1
        # Pack whole tables together up to MAX_TABLE_CHARS; an oversized table is split by rows (header repeated).
        packed, cur = [], ""
        for block in (b.strip() for b in tables.split("\n\n") if b.strip()):
            for part in split_table_block(block):
                if cur and len(cur) + len(part) + 2 > MAX_TABLE_CHARS:
                    packed.append(cur)
                    cur = ""
                cur = f"{cur}\n\n{part}" if cur else part
        if cur:
            packed.append(cur)
        for part in packed:
            chunks.append(Document(page_content=TABLE_PREFIX + part,
                                   metadata={**doc.metadata, "chunk_kind": "table", "chunk_index": idx}))
            idx += 1
    return chunks
