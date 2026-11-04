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
from typing import List

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


def chunk_page_documents(documents: List[Document], text_splitter) -> List[Document]:
    chunks: List[Document] = []
    for doc in documents:
        text, _, tables = doc.page_content.partition(TABLES_MARKER)
        idx = 0
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
