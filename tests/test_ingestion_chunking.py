import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingestion"))

from langchain_core.documents import Document  # noqa: E402

from app.services.documents.pages import merge_text_chunks, split_page_content  # noqa: E402
from chunking import MAX_TABLE_CHARS, chunk_page_documents, split_table_block  # noqa: E402


class FakeSplitter:
    """Splits text into 100-char chunks with 40-char overlap (stands in for the tiktoken splitter)."""
    def split_documents(self, docs):
        out = []
        for d in docs:
            t, i = d.page_content, 0
            while i < len(t):
                out.append(Document(page_content=t[i:i + 100], metadata=dict(d.metadata)))
                if i + 100 >= len(t):
                    break
                i += 60
        return out


def _table(rows, title="Table: BALANCE SHEET"):
    body = "\n".join(f"| Item {i} | {i * 10:,} | ({i}) |" for i in range(rows))
    return f"{title}\n| Line item | 2025 | 2024 |\n| --- | --- | --- |\n{body}"


def test_tables_are_kept_whole_and_separate_from_text():
    page = Document(page_content="A" * 250 + "\n\n[TABLES]\n" + _table(3) + "\n\n" + _table(2, "Table: OTHER"),
                    metadata={"page_num": 5})
    chunks = chunk_page_documents([page], FakeSplitter())
    kinds = [c.metadata["chunk_kind"] for c in chunks]
    assert kinds[:-1] == ["text"] * (len(chunks) - 1) and kinds[-1] == "table"
    assert [c.metadata["chunk_index"] for c in chunks] == list(range(len(chunks)))
    assert all("[TABLES]" not in c.page_content for c in chunks if c.metadata["chunk_kind"] == "text")
    table_chunk = chunks[-1].page_content
    assert table_chunk.startswith("[TABLES]\nTable: BALANCE SHEET") and "Table: OTHER" in table_chunk


def test_oversized_table_is_split_by_rows_with_header_repeated():
    parts = split_table_block(_table(400))
    assert len(parts) > 1 and all(len(p) <= MAX_TABLE_CHARS for p in parts)
    for p in parts:
        assert p.startswith("Table: BALANCE SHEET\n| Line item | 2025 | 2024 |\n| --- | --- | --- |")
    assert sum(p.count("| Item ") for p in parts) == 400  # no row lost or duplicated


def test_table_only_chunk_is_parsed_as_tables_not_text():
    text, tables = split_page_content("[TABLES]\n" + _table(2))
    assert text == "" and len(tables) == 1 and tables[0].startswith("Table: BALANCE SHEET")


def test_merge_text_chunks_orders_and_drops_overlap():
    full = "".join(f"Sentence number {i} about revenue. " for i in range(12))
    chunks = [c.page_content for c in FakeSplitter().split_documents([Document(page_content=full)])]
    shuffled = [(None, chunks[2]), (None, chunks[0]), (None, chunks[1])] + [(None, c) for c in chunks[3:]]
    assert merge_text_chunks(shuffled) == full
    indexed = [(i, c) for i, c in reversed(list(enumerate(chunks)))]
    assert merge_text_chunks(indexed) == full


def test_prose_blocks_are_not_data_tables_but_real_tables_are():
    from table_extractor import _is_data_table
    prose = {"headers": ["Line item", "Col 1", "Col 2"], "rows": [
        ["As of", "", "December 31, 2025, there were approximately 5,861 and 1,611 stockholders of record of our Class A stock"],
        ["December", "31,", "2025, there were approximately 57 stockholders of record of our Class B stock."]]}
    footnote = {"headers": ["Line item", "Col 1"], "rows": [["", "(1) In April 2024, the company's Board of Directors authorized a $70.0 billion share repurchase program."]]}
    caption = {"headers": ["Revenue:"], "rows": []}
    repurchase = {"headers": ["Line item", "Col 1", "Col 2"], "rows": [["October 1 - 31", "841", "$71,231"], ["Total", "1,050", ""]]}
    text_only = {"headers": ["Line item", "Col 1"], "rows": [["Jen-Hsun Huang", "60 President and Chief Executive Officer"]]}
    dated = {"headers": ["Line item", "Jan 28, 2024", "Jan 29, 2023"], "rows": [["Year Ended", "", ""]]}
    one_row = {"headers": ["Thereafter", "2,753"], "rows": []}
    assert not _is_data_table(prose) and not _is_data_table(footnote) and not _is_data_table(caption)
    assert _is_data_table(repurchase) and _is_data_table(text_only) and _is_data_table(dated) and _is_data_table(one_row)
