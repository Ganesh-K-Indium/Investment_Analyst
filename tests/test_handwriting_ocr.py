"""
Offline tests for handwritten-note transcription: reading-merge logic, input handling (images / typed vs scanned PDFs),
failure degradation, and POST /notes/transcribe. Model calls are stubbed; no network.

Run:  ./venv/bin/python -m pytest -q tests/test_handwriting_ocr.py
"""
import asyncio
import io
from types import SimpleNamespace

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.api import notes as notes_api
from app.auth.deps import get_current_user
from app.services import handwriting_ocr as ocr


def _jpeg(size=(300, 200), color=(250, 250, 250)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def _pdf(pages) -> bytes:
    """pages: list of text (typed page) or None (image-only page)."""
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
        else:
            page.insert_image(page.rect, stream=_jpeg((600, 800)))
    return doc.tobytes()


def _lines(res):
    return [(l.text, l.status, l.reason) for l in res]


# ---- merge_readings ----------------------------------------------------------

def test_identical_readings_agree():
    out = ocr.merge_readings(["PT $215 (was 200)", "watch margin"], ["PT $215 (was 200)", "watch margin"])
    assert all(l.status == "agreed" for l in out) and [l.text for l in out] == ["PT $215 (was 200)", "watch margin"]


def test_number_disagreement_is_flagged_with_the_other_reading():
    (line,) = ocr.merge_readings(["Rev 94.9B"], ["Rev 84.9B"])
    assert line.status == "review" and line.reason == "numbers differ" and line.alternatives == ["Rev 84.9B"]
    assert line.text == "Rev 94.9B"  # the primary reader's text is kept


def test_case_and_spacing_differences_do_not_flag():
    (line,) = ocr.merge_readings(["Watch  Services margin"], ["watch services margin"])
    assert line.status == "agreed"


def test_trivial_wording_difference_ok_real_one_flagged():
    assert ocr.merge_readings(["watch guidance and buyback activity."], ["watch guidance and buyback activity"])[0].status == "agreed"
    assert ocr.merge_readings(["guidance and buyback"], ["guidance & buyback"])[0].reason == "wording differs"  # conservative on purpose
    assert ocr.merge_readings(["raise the target"], ["cut position sharply"])[0].reason == "wording differs"


def test_unreadable_marker_always_flags_even_when_both_agree():
    (line,) = ocr.merge_readings(["PT $2[?]5"], ["PT $2[?]5"])
    assert line.status == "review" and line.reason == "unreadable part"


def test_comma_in_numbers_is_not_a_disagreement():
    assert ocr.merge_readings(["Shares 2,112"], ["Shares 2112"])[0].status == "agreed"


def test_extra_or_missing_lines_are_flagged():
    out = ocr.merge_readings(["a note", "extra line 5"], ["a note"])
    assert out[0].status == "agreed" and out[1].status == "review" and "did not see" in out[1].reason
    out = ocr.merge_readings(["a note"], ["a note", "only here 7"])
    assert out[1].text == "only here 7" and out[1].reason == "only the second reader saw this line"


def test_no_second_reader_flags_every_line_with_a_number_only():
    out = ocr.merge_readings(["plain words", "PT $215"], None)
    assert [l.status for l in out] == ["agreed", "review"] and out[1].reason == "not cross-checked"


def test_blank_lines_are_dropped():
    assert [l.text for l in ocr.merge_readings(["a", "", "  ", "b"], ["a", "b"])] == ["a", "b"]


# ---- reply parsing -----------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ('{"lines": ["a", "b"]}', ["a", "b"]),
    ('```json\n{"lines": ["a", " b "]}\n```', ["a", "b"]),
    ('["x", "y"]', ["x", "y"]),
    ("plain\ntext reply", ["plain", "text reply"]),
    ('{"lines": []}', []),
    ("", []),
])
def test_parse_lines(raw, expected):
    assert ocr.parse_lines(raw) == expected


# ---- input handling ----------------------------------------------------------

def test_image_is_normalised_to_a_bounded_jpeg():
    big = io.BytesIO()
    Image.new("RGBA", (5000, 3000), (255, 255, 255, 255)).save(big, "PNG")
    (page,) = ocr.split_pages(big.getvalue())
    img = Image.open(io.BytesIO(page["image"]))
    assert page["text"] is None and img.format == "JPEG" and max(img.size) == ocr.MAX_SIDE_PX


def test_typed_pdf_pages_use_the_text_layer_and_scans_are_rendered():
    pages = ocr.split_pages(_pdf(["This is a typed page with plenty of real text on it for the analyst.", None]))
    assert pages[0]["text"] and pages[0]["image"] is None
    assert pages[1]["text"] is None and pages[1]["image"].startswith(b"\xff\xd8")


@pytest.mark.parametrize("data,status", [
    (b"GIF89a....", 415),
    (b"\x00\x00\x00\x18ftypheic" + b"\x00" * 8, 415),
    (b"%PDF-not really a pdf", 422),
    (b"\xff\xd8\xff garbage", 422),
])
def test_bad_files_are_rejected_with_a_user_safe_message(data, status):
    with pytest.raises(ocr.OcrError) as e:
        ocr.split_pages(data)
    assert e.value.status_code == status and str(e.value)


def test_pdf_page_limit():
    with pytest.raises(ocr.OcrError) as e:
        ocr.split_pages(_pdf(["text " * 20] * (ocr.MAX_PAGES + 1)))
    assert e.value.status_code == 413


# ---- transcribe_upload / read_page -------------------------------------------

def _reader(replies):
    """replies: {model: lines | Exception}"""
    async def read(model, jpeg):
        r = replies[model]
        if isinstance(r, Exception):
            raise r
        return list(r)
    return read


def _run(data, replies, monkeypatch, models="m1,m2"):
    monkeypatch.setenv("HANDWRITING_OCR_MODELS", models)
    return asyncio.run(ocr.transcribe_upload(data, reader=_reader(replies)))


def test_two_agreeing_readers_give_a_verified_result(monkeypatch):
    res = _run(_jpeg(), {"m1": ["PT $215"], "m2": ["PT $215"]}, monkeypatch)
    assert res.verified and res.needs_review == 0 and res.text == "PT $215" and res.engines == ["m1", "m2"]


def test_disagreement_counts_towards_needs_review(monkeypatch):
    res = _run(_jpeg(), {"m1": ["PT $215", "ok"], "m2": ["PT $275", "ok"]}, monkeypatch)
    assert res.needs_review == 1 and res.verified
    assert res.to_dict()["pages"][0]["lines"][0]["alternatives"] == ["PT $275"]


def test_second_reader_failure_degrades_to_unverified_not_an_error(monkeypatch):
    res = _run(_jpeg(), {"m1": ["PT $215", "words"], "m2": RuntimeError("boom")}, monkeypatch)
    assert not res.verified and [l.status for l in res.pages[0].lines] == ["review", "agreed"]


def test_primary_failure_falls_back_to_the_second_reader(monkeypatch):
    res = _run(_jpeg(), {"m1": RuntimeError("boom"), "m2": ["PT $215"]}, monkeypatch)
    assert res.text == "PT $215" and not res.verified


def test_all_readers_failing_is_a_502_with_a_friendly_message(monkeypatch):
    with pytest.raises(ocr.OcrError) as e:
        _run(_jpeg(), {"m1": RuntimeError("a"), "m2": RuntimeError("b")}, monkeypatch)
    assert e.value.status_code == 502 and "try again" in str(e.value)


def test_typed_pdf_needs_no_model_calls(monkeypatch):
    res = _run(_pdf(["Typed analyst note with enough characters to count as a real text layer."]),
               {"m1": RuntimeError("must not be called"), "m2": RuntimeError("must not be called")}, monkeypatch)
    assert res.pages[0].source == "text-layer" and res.verified and res.needs_review == 0


def test_mixed_pdf_ocr_only_the_scanned_page(monkeypatch):
    res = _run(_pdf(["Typed page with enough text in the layer to be treated as typed content.", None]),
               {"m1": ["scanned 5"], "m2": ["scanned 5"]}, monkeypatch)
    assert [p.source for p in res.pages] == ["text-layer", "ocr"] and [p.page for p in res.pages] == [1, 2]


def test_empty_and_oversized_uploads(monkeypatch):
    with pytest.raises(ocr.OcrError):
        _run(b"", {}, monkeypatch)
    with pytest.raises(ocr.OcrError) as e:
        _run(b"x" * (ocr.MAX_UPLOAD_BYTES + 1), {}, monkeypatch)
    assert e.value.status_code == 413


def test_models_env_is_capped_at_two(monkeypatch):
    monkeypatch.setenv("HANDWRITING_OCR_MODELS", "a, b ,c")
    assert ocr.configured_models() == ["a", "b"]


# ---- POST /notes/transcribe --------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("HANDWRITING_OCR_MODELS", "m1,m2")
    notes_api._transcribe_calls.clear()
    monkeypatch.setattr(ocr, "read_image", _reader({"m1": ["PT $215"], "m2": ["PT $275"]}))
    app = FastAPI()
    app.include_router(notes_api.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, username="ana", full_name="Ana", role="analyst")
    return TestClient(app)


def _post(client, data=None, name="note.jpg"):
    return client.post("/notes/transcribe", files={"file": (name, data if data is not None else _jpeg(), "image/jpeg")})


def test_transcribe_returns_a_draft_with_flags(client):
    r = _post(client)
    body = r.json()
    assert r.status_code == 200 and body["needs_review"] == 1 and body["engines"] == ["m1", "m2"]
    assert body["pages"][0]["lines"][0] == {"text": "PT $215", "status": "review", "alternatives": ["PT $275"], "reason": "numbers differ"}


def test_transcribe_maps_ocr_errors_to_their_status(client):
    assert _post(client, b"GIF89a....").status_code == 415
    assert _post(client, b"").status_code == 400


def test_transcribe_is_disabled_without_a_key_or_by_flag(client, monkeypatch):
    monkeypatch.setenv("HANDWRITING_OCR_ENABLED", "0")
    assert _post(client).status_code == 503


def test_transcribe_is_rate_limited_per_user(client):
    for _ in range(notes_api._TRANSCRIBE_LIMIT):
        assert _post(client).status_code == 200
    assert _post(client).status_code == 429


def test_transcribe_requires_a_file(client):
    assert client.post("/notes/transcribe").status_code == 422


def test_transcribe_oversized_body_is_rejected_without_reading_it_all(client):
    big = b"\xff\xd8\xff" + b"0" * (ocr.MAX_UPLOAD_BYTES + 10)
    assert _post(client, big).status_code == 413


def test_input_method_is_stored_and_defaults_to_typed():
    from app.api.notes import NoteCreate
    base = dict(ticker="AAPL", title="t", content="c")
    assert NoteCreate(**base).input_method == "typed"
    assert NoteCreate(**base, input_method="handwritten").input_method == "handwritten"
    with pytest.raises(ValueError):
        NoteCreate(**base, input_method="scanned-by-someone")
