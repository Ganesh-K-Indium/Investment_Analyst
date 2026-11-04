from types import SimpleNamespace as N

from app.api.rag import _response_document, _saved_document
from app.services.documents.quotes import pick_quote

PAGE = ("Item 7. Overview. Total revenue increased 12% to $4.1 billion in fiscal 2022, driven by enterprise customer "
        "growth. Operating margin was 21%. Some unrelated boilerplate text about forward looking statements and risks."
        "\n\n[TABLES]\n| Line item | 2022 | 2021 |\n| --- | --- | --- |\n| Net income | 672,000 | 1,382,000 |")
ANSWER = "Zoom's revenue grew 12% to $4.1 billion in fiscal 2022 on enterprise growth."


def test_picks_the_supporting_sentence():
    q = pick_quote(PAGE, ANSWER)
    assert q and q.startswith("Total revenue increased 12%") and len(q) <= 400


def test_falls_back_to_table_row_and_handles_table_only_chunk():
    chunk = "[TABLES]\n| Line item | 2022 | 2021 |\n| --- | --- | --- |\n| Net income | 672,000 | 1,382,000 |"
    assert pick_quote(chunk, "Net income was 672,000 in 2022 versus 1,382,000 in 2021") == "Net income 672,000 1,382,000"


def test_no_quote_when_nothing_matches():
    assert pick_quote(PAGE, "completely unrelated question about satellites") is None
    assert pick_quote("", ANSWER) is None


def test_saved_entry_keeps_quote_not_chunk_text_and_skips_images():
    doc = N(metadata={"content_type": "text", "page_num": 3}, page_content=PAGE)
    saved = _saved_document(doc, ANSWER)
    assert set(saved) == {"metadata", "quote"} and "page_content" not in saved
    img = N(metadata={"content_type": "image"}, page_content=PAGE)
    assert set(_saved_document(img, ANSWER)) == {"metadata"}
    assert _response_document(doc, ANSWER)["quote"] == saved["quote"]
