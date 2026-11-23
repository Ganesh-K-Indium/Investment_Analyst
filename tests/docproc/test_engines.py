"""Reading engines: model providers, local OCR, the reading plan, the escalation ladder and the API. All offline."""
import asyncio
import io
import time

import pytest
from fastapi.testclient import TestClient

from app.docproc import config
from app.docproc.engines.strategy import ladder, plan
from app.docproc.engines.providers import llm
from app.docproc.engines.ocr import local
from app.docproc.services.reading import ocr, pdf_pages
from app.docproc import pipeline
from app.docproc.services.reading.tess import available as tess_available
from app.utils.usage_tracker import track_usage
from tests.docproc.api_stub import H, mk, reload_app, wait


def run(coro):
    return asyncio.run(coro)


KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OLLAMA_BASE_URL", "LLM_CUSTOM_BASE_URL", "LLM_CUSTOM_MODEL", "LOCAL_ONLY",
        "EXTRACTION_MODELS", "HANDWRITING_OCR_MODELS", "READING_STRATEGY", "LOCAL_OCR_ENGINE", "ESCALATE_BELOW", "TOKEN_CAP")


@pytest.fixture()
def clean_env(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_ENABLED", "1")
    return monkeypatch


# ---- model specs and providers ---------------------------------------------------------------------------------------

def test_spec_parsing():
    assert llm.split("gpt-4.1") == ("openai", "gpt-4.1")
    assert llm.split("anthropic:claude-sonnet-5-5") == ("anthropic", "claude-sonnet-5-5")
    assert llm.split("ollama:qwen2.5vl:7b") == ("ollama", "qwen2.5vl:7b")      # only the first colon separates
    assert llm.split("qwen2.5vl:7b") == ("openai", "qwen2.5vl:7b")            # an unknown prefix is part of an OpenAI-style name
    assert llm.valid_spec("gemini:gemini-2.5-flash")
    assert not llm.valid_spec("anthropic:") and not llm.valid_spec("a b") and not llm.valid_spec("") and not llm.valid_spec(None)


def test_defaults_follow_the_configured_provider(clean_env):
    assert llm.default_models() == ["gpt-4.1", "gpt-4.1-mini"] and not config.llm_enabled()
    clean_env.setenv("ANTHROPIC_API_KEY", "k")
    assert llm.default_models()[0].startswith("anthropic:") and config.llm_enabled()
    clean_env.setenv("OPENAI_API_KEY", "k")
    assert llm.default_models() == ["gpt-4.1", "gpt-4.1-mini"]                   # OpenAI stays the default when it is there
    clean_env.delenv("OPENAI_API_KEY"), clean_env.delenv("ANTHROPIC_API_KEY")
    clean_env.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    assert llm.default_models() == ["ollama:qwen2.5vl:7b"] and config.llm_enabled()
    clean_env.setenv("EXTRACTION_MODELS", "anthropic:claude-haiku-5-5")
    assert config.extraction_models() == ["anthropic:claude-haiku-5-5"]          # an explicit server setting wins


def test_local_only_switches_cloud_providers_off(clean_env):
    clean_env.setenv("OPENAI_API_KEY", "k")
    assert config.llm_enabled()
    clean_env.setenv("LOCAL_ONLY", "1")
    assert not llm.provider_ready("openai") and not config.llm_enabled()
    with pytest.raises(llm.LLMError) as e:
        run(llm.complete("gpt-4.1", "hi", stage="t"))
    assert e.value.status == 503 and "LOCAL_ONLY" in str(e.value)
    clean_env.setenv("OLLAMA_BASE_URL", "http://localhost:11434")                # a model on your own hardware still works
    assert config.llm_enabled()
    with pytest.raises(ValueError, match="switched off"):
        plan.clean({"models": ["openai:gpt-4.1"]})
    assert next(p for p in llm.providers_status() if p["id"] == "openai")["status"] == "blocked"


def test_clean_json():
    assert llm.clean_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert llm.clean_json('Here you go: {"a": 1} Hope it helps') == '{"a": 1}'
    assert llm.clean_json("not json") == "not json"


def test_anthropic_request_and_usage(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "ak")
    seen = {}

    async def fake_post(url, headers, body):
        seen.update(url=url, headers=headers, body=body)
        return {"content": [{"type": "text", "text": '```json\n{"lines": ["a"]}\n```'}], "usage": {"input_tokens": 1500, "output_tokens": 20}}
    clean_env.setattr(llm, "_post", fake_post)
    with track_usage("t") as u:
        out = run(llm.complete("anthropic:claude-sonnet-5-5", "read this", b"\xff\xd8jpeg", stage="handwriting_ocr"))
    assert out == '{"lines": ["a"]}'
    assert seen["url"].endswith("/v1/messages") and seen["headers"]["x-api-key"] == "ak"
    content = seen["body"]["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg" and "JSON" in content[1]["text"]
    assert seen["body"]["model"] == "claude-sonnet-5-5"
    assert u.entries[0].total_tokens == 1520 and u.entries[0].model == "anthropic:claude-sonnet-5-5"


def test_anthropic_retries_without_temperature_when_the_model_refuses_it(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "ak")
    calls = []

    async def fake_post(url, headers, body):
        calls.append("temperature" in body)
        if "temperature" in body:
            raise llm.LLMError("temperature is not supported for this model", 400)
        return {"content": [{"type": "text", "text": "{}"}], "usage": {}}
    clean_env.setattr(llm, "_post", fake_post)
    assert run(llm.complete("anthropic:m", "p", stage="t")) == "{}" and calls == [True, False]


def test_gemini_request_and_usage(clean_env):
    clean_env.setenv("GOOGLE_API_KEY", "gk")
    seen = {}

    async def fake_post(url, headers, body):
        seen.update(url=url, headers=headers, body=body)
        return {"candidates": [{"content": {"parts": [{"text": '{"x": 1}'}]}}], "usageMetadata": {"promptTokenCount": 300, "candidatesTokenCount": 10, "totalTokenCount": 410}}
    clean_env.setattr(llm, "_post", fake_post)
    with track_usage("t") as u:
        assert run(llm.complete("gemini:gemini-2.5-flash", "p", b"jpg", stage="discover")) == '{"x": 1}'
    assert "models/gemini-2.5-flash:generateContent" in seen["url"] and seen["headers"]["x-goog-api-key"] == "gk"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert seen["body"]["contents"][0]["parts"][0]["inline_data"]["mime_type"] == "image/jpeg"
    assert u.entries[0].total_tokens == 410                                       # includes the model's thinking tokens


class FakeCompletions:
    def __init__(self, fail_first=False):
        self.calls, self.fail_first = [], fail_first

    async def create(self, **kw):
        self.calls.append(kw)
        if self.fail_first and "response_format" in kw:
            err = Exception("bad request")
            err.status_code = 400
            raise err
        msg = type("M", (), {"content": '{"ok": true}'})
        usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})
        return type("R", (), {"choices": [type("C", (), {"message": msg})], "usage": usage})


def fake_client(comp):
    return type("Client", (), {"chat": type("Chat", (), {"completions": comp})})


def test_openai_style_providers(clean_env):
    clean_env.setenv("OPENAI_API_KEY", "k")
    clean_env.setenv("OLLAMA_BASE_URL", "http://box:11434/")
    comp, bases = FakeCompletions(), []
    clean_env.setattr(llm, "_openai_client", lambda base, key: (bases.append(base), fake_client(comp))[1])
    run(llm.complete("gpt-4.1", "p", b"j", stage="t"))
    run(llm.complete("gpt-5", "p", stage="t"))
    run(llm.complete("ollama:qwen2.5vl:7b", "p", b"j", stage="t"))
    gpt41, gpt5, ollama = comp.calls
    assert gpt41["temperature"] == 0 and gpt41["response_format"] == {"type": "json_object"} and gpt41["messages"][0]["content"][1]["image_url"]["detail"] == "high"
    assert gpt5["reasoning_effort"] == "low" and "temperature" not in gpt5 and len(gpt5["messages"][0]["content"]) == 1   # text-only: no image part
    assert ollama["model"] == "qwen2.5vl:7b" and bases == [None, None, "http://box:11434/v1"]


def test_self_hosted_server_without_json_mode_is_retried(clean_env):
    clean_env.setenv("LLM_CUSTOM_BASE_URL", "http://vllm:8000/v1")
    comp = FakeCompletions(fail_first=True)
    clean_env.setattr(llm, "_openai_client", lambda base, key: fake_client(comp))
    assert run(llm.complete("custom:my-model", "p", b"j", stage="t")) == '{"ok": true}'
    assert ["response_format" in c for c in comp.calls] == [True, False]


# ---- local OCR ---------------------------------------------------------------------------------------------------------

def W(x0, y0, x1, y1, t, c=0.95):
    return local.Word(x0, y0, x1, y1, t, c)


def test_words_become_rows_with_cells():
    words = [W(10, 100, 60, 120, "Revenue"), W(300, 101, 360, 121, "70,066"), W(500, 99, 560, 119, "61,858"),
             W(10, 140, 40, 160, "Net"), W(45, 141, 90, 161, "income")]
    r = local.build_reading(words, "x")
    assert r.lines == ["Revenue | 70,066 | 61,858", "Net income"]                # wide gaps are cells, a word space is not
    assert r.words == 5 and r.confidence == pytest.approx(0.95) and r.low_share == 0


def test_confidence_summary_counts_doubtful_words_and_numbers():
    words = [W(0, 0, 40, 20, "Total", 0.95), W(50, 0, 90, 20, "1,250.75", 0.5), W(100, 0, 140, 20, "due", 0.4), W(150, 0, 190, 20, "$", None)]
    r = local.build_reading(words, "x")
    assert r.low_share == pytest.approx(2 / 3) and r.digit_low_share == 1.0 and r.words == 3
    assert r.line_conf[0] == pytest.approx((0.95 + 0.5 + 0.4) / 3)                  # punctuation-only tokens do not count


def test_tesseract_tsv_is_parsed():
    tsv = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n" \
          "4\t1\t1\t1\t1\t0\t5\t5\t100\t20\t-1\t\n5\t1\t1\t1\t1\t1\t10\t10\t50\t20\t93.5\tHello\n5\t1\t1\t1\t1\t2\t70\t10\t40\t20\t-1\t \n"
    words = local.words_from_tsv(tsv)
    assert [(w.text, w.conf) for w in words] == [("Hello", 0.935)] and words[0].x1 == 60


def test_box_words_accepts_polygons_and_rectangles():
    ws = local._box_words([([[0, 0], [50, 0], [50, 20], [0, 20]], "Hi", 0.9), ([5, 5, 25, 15], "yo", None), ([0, 0, 1, 1], "  ", 1.0)])
    assert [(w.text, w.x1, w.y1, w.conf) for w in ws] == [("Hi", 50, 20, 0.9), ("yo", 25, 15, None)]


def jpeg_of(ink: bool) -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("L", (600, 800), 255)
    if ink:
        d = ImageDraw.Draw(img)
        for y in range(50, 750, 20):
            d.rectangle((40, y, 560, y + 8), fill=0)
    out = io.BytesIO()
    img.save(out, "JPEG")
    return out.getvalue()


def test_assess_decides_whether_a_local_reading_can_stand():
    good = local.build_reading([W(0, 0, 40, 20, "Total", 0.97), W(50, 0, 90, 20, "125", 0.96)], "x")
    assert local.assess(good, 0.85).ok
    weak = local.build_reading([W(0, 0, 40, 20, "Total", 0.7), W(50, 0, 90, 20, "125", 0.7)], "x")
    v = local.assess(weak, 0.85)
    assert not v.ok and "70%" in v.reasons[0]
    assert local.assess(weak, 0.6).ok                                               # the threshold is the user's call
    digits = local.build_reading([W(i * 50, 0, i * 50 + 40, 20, f"{n}{n}", 0.99 if i > 1 else 0.5) for i, n in enumerate("12345")], "x")
    assert any("numbers" in r for r in local.assess(digits, 0.5).reasons)
    garbled = local.build_reading([W(0, 0, 40, 20, "(cid:12)(cid:7)" * 6, 0.99)], "x")
    assert not local.assess(garbled, 0.5).ok
    empty = local.Reading("x", [], [], 0)
    assert local.assess(empty, 0.85, jpeg_of(ink=False)).ok                         # a blank page has nothing to read
    inked = local.assess(empty, 0.85, jpeg_of(ink=True))
    assert not inked.ok and "no text was recognised" in inked.reasons[0]            # marks but no words: handwriting, a photo


def test_agreement_separates_clean_pages_from_unreadable_ones():
    clean_a = "Revenue | 70,066 | 61,858\nNet income 25,824 21,939\nTotal assets $512,163"
    clean_b = "Revenue 70,066 61,858\nNet income 25,824 21,939\nTotal assets 512,163"
    assert local.agreement(clean_a, clean_b) > 0.9
    assert local.agreement(clean_a, "7o,o66 b1,858\nNet inc0me 2S,824") < 0.5
    assert local.agreement(clean_a, "") == 0.0
    mine = local.build_reading([W(0, 0, 90, 20, "Revenue 70,066", 0.8), W(0, 40, 90, 60, "Net inc0me 2S,824", 0.8)], "rapidocr")
    other = local.build_reading([W(0, 0, 90, 20, "Revenue 70,066", 0.9), W(0, 40, 90, 60, "Net income 25,824", 0.9)], "tesseract")
    out = local.cross_check(mine, other)
    assert out.line_conf[0] == 1.0 and out.line_conf[1] < 0.5 and out.agreement == pytest.approx(0.6)                 # the line they disagree on is the doubtful one
    assert not local.assess_cross(out).ok and "60%" in local.assess_cross(out).reasons[0]
    same = local.cross_check(local.build_reading([W(0, 0, 90, 20, "Revenue 70,066", 0.6), W(0, 40, 90, 60, "Net income 25,824", 0.6)], "rapidocr"), other)
    assert same.agreement == 1.0 and local.assess_cross(same).ok                                # its own low confidence no longer matters


def test_neural_engines_run_one_page_at_a_time_and_tesseract_in_parallel():
    import threading
    import time as _t

    class Probe(local.Engine):
        id, label = "probe", "Probe"
        def __init__(self, serial):
            self.serial, self.now, self.peak, self.guard = serial, 0, 0, threading.Lock()
        def available(self):
            return True
        def _words(self, jpeg):
            with self.guard:
                self.now += 1
                self.peak = max(self.peak, self.now)
            _t.sleep(0.05)
            with self.guard:
                self.now -= 1
            return [W(0, 0, 40, 20, "x")]

    async def burst(eng):
        await asyncio.gather(*[eng.read(jpeg_of(False)) for _ in range(6)])
        return eng.peak
    assert run(burst(Probe(serial=True))) == 1
    assert run(burst(Probe(serial=False))) > 1
    assert [e.serial for e in local.engines()] == [False, True, True, True]


def test_warmup_reports_each_engine_and_signals_failure(capsys, monkeypatch):
    from app.docproc.engines import warmup

    class Good(FakeLocal):
        id, label = "rapidocr", "RapidOCR"

        async def read(self, jpeg):
            r = local.build_reading([W(0, 0, 90, 20, "Invoice No. 48213 Date: 03/14/2026 Total due $1,250.75 Ref AB-9071", 0.9)], "rapidocr")
            return r

    class Bad(FakeLocal):
        id, label = "doctr", "docTR"

    class Poor(FakeLocal):
        id, label = "paddleocr", "PaddleOCR"

        async def read(self, jpeg):
            return local.build_reading([W(0, 0, 90, 20, "nothing useful", 0.9)], "paddleocr")

    class Missing(FakeLocal):
        id, label, install = "tesseract", "Tesseract", "install it"
        available = lambda self: False
    monkeypatch.setattr(local, "_ENGINES", {"tesseract": Missing(), "rapidocr": Good(), "paddleocr": Poor(), "doctr": Bad(fail=True)})
    assert warmup.main(["rapidocr"]) == 0
    out = capsys.readouterr().out
    assert "OK" in out and "RapidOCR" in out and "found 100%" in out
    assert warmup.main(["paddleocr"]) == 1                                                   # reads, but not the sample's values
    assert warmup.main(["doctr"]) == 1                                                       # raises
    assert warmup.main(["tesseract"]) == 1                                                   # asked for, not installed
    assert warmup.main(["nope"]) == 2
    assert warmup.main([]) == 1                                                              # the report as a whole: one engine failed
    monkeypatch.setattr(local, "_ENGINES", {"tesseract": Missing(), "rapidocr": Good()})
    assert warmup.main([]) == 0                                                              # listing everything: a missing optional engine is only reported


def test_status_says_whether_models_are_on_disk(capsys, monkeypatch, tmp_path):
    from app.docproc.engines import warmup

    class Ready(FakeLocal):
        pass
    paddle, doctr = Ready(), Ready()
    paddle.id, paddle.label, doctr.id, doctr.label = "paddleocr", "PaddleOCR", "doctr", "docTR"
    monkeypatch.setattr(local, "_ENGINES", {"paddleocr": paddle, "doctr": doctr})
    monkeypatch.setenv("PADDLE_PDX_CACHE_HOME", str(tmp_path / "px"))
    monkeypatch.setenv("DOCTR_CACHE_DIR", str(tmp_path / "dt"))
    assert warmup.main(["--status"]) == 0
    out = capsys.readouterr().out
    assert out.count("NOT downloaded yet") == 2 and "warmup paddleocr" in out                    # pip alone does not fetch them
    (tmp_path / "dt" / "models").mkdir(parents=True)
    (tmp_path / "dt" / "models" / "m.pt").write_bytes(b"x" * 2_000_000)
    warmup.main(["--status"])
    out = capsys.readouterr().out
    assert "docTR" in out and "models downloaded (1 files, 2 MB)" in out and out.count("NOT downloaded yet") == 1


def test_models_live_in_the_apps_data_folder_unless_told_otherwise(monkeypatch, tmp_path):
    for k in ("DOCPROC_MODELS_DIR", "DOCTR_CACHE_DIR", "PADDLE_PDX_CACHE_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DOCPROC_DATA_DIR", str(tmp_path))
    assert local.models_dir() == tmp_path.resolve() / "models"
    assert local.model_folders()["doctr"] == tmp_path.resolve() / "models" / "doctr" / "models"
    assert local.model_folders()["paddleocr"] == tmp_path.resolve() / "models" / "paddlex" / "official_models"
    local.configure_caches()
    import os
    assert os.environ["DOCTR_CACHE_DIR"] == str(tmp_path.resolve() / "models" / "doctr")           # the libraries are pointed at it
    monkeypatch.setenv("DOCPROC_MODELS_DIR", str(tmp_path / "elsewhere"))
    assert local.models_dir() == tmp_path / "elsewhere"
    monkeypatch.setenv("DOCTR_CACHE_DIR", str(tmp_path / "mine"))
    local.configure_caches()
    assert os.environ["DOCTR_CACHE_DIR"] == str(tmp_path / "mine")                                  # an explicit setting wins
    assert local.model_status("rapidocr")["state"] == "bundled" and local.model_status("tesseract")["state"] == "none"
    assert local.model_status("doctr")["state"] == "missing" and local.model_status("doctr")["approx_mb"] == 125
    (tmp_path / "mine" / "models").mkdir(parents=True)
    (tmp_path / "mine" / "models" / "a.pt").write_bytes(b"x" * 1000)
    assert local.model_status("doctr")["state"] == "downloaded" and local.model_status("doctr")["bytes"] == 1000


def test_paddle_loads_with_a_home_folder_inside_the_app(monkeypatch, tmp_path):
    """Paddle creates ~/.cache/paddle on import. With a home it cannot write to (a root-owned ~/.cache) it would never load, so it is
    imported while the home folder points inside the app, and the real one is restored afterwards."""
    import os
    import sys
    import types
    monkeypatch.setenv("DOCPROC_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("HOME", "/the/real/home")
    monkeypatch.setenv("USERPROFILE", "C:/the/real/home")
    seen = {}
    fake_paddle = types.ModuleType("paddle")
    fake_ocr = types.ModuleType("paddleocr")

    class FakePaddleOCR:
        def __init__(self, **kw):
            seen["home_while_loading"] = os.path.expanduser("~")
            seen["kw"] = kw
    fake_ocr.PaddleOCR = FakePaddleOCR
    monkeypatch.setitem(sys.modules, "paddle", fake_paddle)
    monkeypatch.setitem(sys.modules, "paddleocr", fake_ocr)
    engine = local.PaddleOCR._load()
    assert isinstance(engine, FakePaddleOCR)
    assert seen["home_while_loading"] == str(tmp_path / "models" / "home")                            # the app's folder, not the user's
    assert (tmp_path / "models" / "home").is_dir()
    assert os.environ["HOME"] == "/the/real/home" and os.environ["USERPROFILE"] == "C:/the/real/home"   # put back afterwards
    with pytest.raises(RuntimeError):
        with local.private_home():
            raise RuntimeError("boom")
    assert os.environ["HOME"] == "/the/real/home"                                                       # even when loading fails


def test_engine_listing_names_every_engine():
    ids = [e["id"] for e in local.listing()]
    assert ids == ["tesseract", "rapidocr", "paddleocr", "doctr"]
    missing = [e for e in local.listing() if not e["ready"]]
    assert all(e["status"] == "not_installed" and e["hint"] for e in missing)
    assert all(e["status"] == "ready" for e in local.listing() if e["ready"])


@pytest.mark.skipif(not tess_available(), reason="Tesseract is not installed")
def test_tesseract_reads_a_clean_page_confidently():
    from app.api.engines import SAMPLE_TOKENS, _found, sample_page
    jpeg = sample_page()
    r = run(local.get("tesseract").read(jpeg))
    assert _found(r.text) >= 0.75, r.text
    assert r.confidence > 0.85 and local.assess(r, 0.85, jpeg).ok


# ---- the reading plan -------------------------------------------------------------------------------------------------

class FakeLocal(local.Engine):
    """A local engine with a scripted reading, so the ladder is tested without OCR."""
    id, label = "tesseract", "Fake"

    def __init__(self, reading=None, fail=False):
        self.reading, self.fail, self.calls = reading, fail, 0

    def available(self):
        return True

    async def read(self, jpeg):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return self.reading


def reading(conf=0.95, lines=("Invoice No. 4821", "Total $120.00"), digit_low=0.0, low=0.0):
    r = local.build_reading([W(0, i * 30, 90, i * 30 + 20, t, conf) for i, t in enumerate(lines)], "tesseract")
    r.digit_low_share, r.low_share = digit_low, low
    return r


@pytest.fixture()
def fake_engine(monkeypatch):
    def install(**kw):
        eng = FakeLocal(**kw)
        monkeypatch.setitem(local._ENGINES, "tesseract", eng)
        return eng
    return install


def test_strategies_map_to_modes(clean_env, fake_engine):
    fake_engine(reading=reading())
    clean_env.setenv("OPENAI_API_KEY", "k")
    assert plan.resolve({"strategy": "economy"}).mode == "local" and not plan.resolve({"strategy": "economy"}).llm
    b = plan.resolve({"strategy": "balanced"})
    assert (b.mode, b.llm, b.local_engine) == ("escalate", True, "tesseract")
    assert plan.resolve({"strategy": "accuracy"}).mode == "vision"
    assert plan.resolve({"strategy": "custom", "mode": "local"}).mode == "local"
    assert plan.resolve({"strategy": "balanced", "mode": "local"}).mode == "escalate"           # mode only counts for custom
    assert plan.resolve({}).strategy == "balanced"                                             # the default
    assert plan.resolve({"strategy": "economy"}, {"strategy": "accuracy"}).strategy == "accuracy"   # an upload can override
    assert plan.resolve({"strategy": "nonsense"}).strategy == "balanced"


def test_plan_degrades_with_a_note_instead_of_failing(clean_env, fake_engine, monkeypatch):
    fake_engine(reading=reading())
    p = plan.resolve({"strategy": "balanced"})                                                  # no model key at all
    assert p.mode == "escalate" and not p.llm and any("no model is configured" in n for n in p.notes)
    monkeypatch.setattr(local, "ready", lambda: [])
    clean_env.setenv("OPENAI_API_KEY", "k")
    p = plan.resolve({"strategy": "balanced", "local_engine": "rapidocr"})
    assert p.local_engine is None and p.mode == "vision" and any("straight to the vision models" in n for n in p.notes)


def test_plan_settings_and_fingerprint(clean_env, fake_engine):
    fake_engine(reading=reading())
    clean_env.setenv("OPENAI_API_KEY", "k")
    base = plan.resolve({"strategy": "balanced"})
    assert base.fingerprint() == plan.resolve({"strategy": "balanced"}).fingerprint()
    assert base.fingerprint() != plan.resolve({"strategy": "accuracy"}).fingerprint()
    assert base.fingerprint() != plan.resolve({"strategy": "balanced", "escalate_below": 0.7}).fingerprint()
    assert base.fingerprint() != plan.resolve({"strategy": "balanced", "models": ["openai:gpt-5"]}).fingerprint()
    assert plan.resolve({"models": ["openai:gpt-5", "bad model"]}).models == ("openai:gpt-5",)
    assert plan.resolve({"escalate_below": 5}).escalate_below == 0.99 and plan.resolve({"token_cap": -4}).token_cap == 0
    clean_env.setenv("READING_STRATEGY", "accuracy")
    assert plan.resolve({}).strategy == "accuracy"                                              # the server's default
    assert plan.resolve({"strategy": "economy"}).strategy == "economy"                          # a workspace beats it


def test_settings_are_validated(clean_env, fake_engine, monkeypatch):
    fake_engine(reading=reading())
    absent = FakeLocal()
    absent.id, absent.label, absent.install = "doctr", "docTR", "pip install doctr"
    absent.available = lambda: False                                  # whatever is installed on this machine
    monkeypatch.setitem(local._ENGINES, "doctr", absent)
    clean_env.setenv("OPENAI_API_KEY", "k")
    ok = plan.clean({"strategy": "custom", "mode": "escalate", "local_engine": "tesseract", "models": ["openai:gpt-4.1", "openai:gpt-4.1"],
                     "escalate_below": "0.8", "token_cap": 50000})
    assert ok == {"strategy": "custom", "mode": "escalate", "local_engine": "tesseract", "models": ["openai:gpt-4.1"], "escalate_below": 0.8, "token_cap": 50000}
    for bad, msg in [({"strategy": "x"}, "Choose"), ({"mode": "x"}, "mode"), ({"local_engine": "nope"}, "does not exist"),
                     ({"local_engine": "doctr"}, "not installed"), ({"models": ["a", "b", "c"]}, "up to two"), ({"models": ["anthropic:claude-haiku-5-5"]}, "not available"),
                     ({"escalate_below": 0.1}, "between"), ({"token_cap": -1}, "token cap")]:
        with pytest.raises(ValueError, match=msg):
            plan.clean(bad)


# ---- the escalation ladder ----------------------------------------------------------------------------------------------

async def vision_ok(model, jpeg):
    return ["Invoice No. 4821", "Total $120.00"]


def scan(**kw):
    return run(ladder.read_scanned(jpeg=jpeg_of(True), models=["m-a", "m-b"], **kw))


def test_a_confident_local_reading_never_reaches_a_model(fake_engine):
    eng = fake_engine(reading=reading(0.96))
    calls = []

    async def reader(model, jpeg):
        calls.append(model)
        return ["x"]
    s = scan(mode="escalate", engine_id="tesseract", reader=reader)
    assert calls == [] and eng.calls == 1
    assert s.source == "ocr-local" and not s.verified and [l.text for l in s.lines] == ["Invoice No. 4821", "Total $120.00"]
    assert s.reading["engine"] == "tesseract" and s.reading["escalated"] is False and s.reading["confidence"] == pytest.approx(0.96)


def test_an_uncalibrated_engine_needs_tesseract_to_agree(fake_engine, monkeypatch):
    fake_engine(reading=reading(0.97, ("Invoice No. 4821", "Total $120.00")))                 # Tesseract (the reference)
    rapid = FakeLocal(reading=reading(0.7, ("Invoice No. 4821", "Total $120.00")))               # low own confidence, but they agree
    rapid.id, rapid.calibrated = "rapidocr", False
    monkeypatch.setitem(local._ENGINES, "rapidocr", rapid)
    s = scan(mode="escalate", engine_id="rapidocr", reader=vision_ok)
    assert s.source == "ocr-local" and s.reading["engine"] == "rapidocr" and s.reading["agreement"] == 1.0 and rapid.calls == 1
    rapid.reading = reading(0.99, ("Invc1ce N0. 48Z1", "T0tal $l2O.OO"))                         # confident, but Tesseract disagrees
    s = scan(mode="escalate", engine_id="rapidocr", reader=vision_ok)
    assert s.source == "ocr" and s.reading["escalated"] and "agree on only" in s.reading["reasons"][0]
    lone = scan(mode="local", engine_id="rapidocr")                                              # economy keeps the local reading, flagged
    assert lone.source == "ocr-local" and lone.reading["reasons"] and any(l.status == "review" for l in lone.lines)
    monkeypatch.setitem(local._ENGINES, "tesseract", type("Gone", (FakeLocal,), {"available": lambda self: False})())
    rapid.reading = reading(0.82, ("Invoice No. 4821",))
    s = scan(mode="escalate", engine_id="rapidocr", reader=vision_ok)                            # no reference: its own number is not enough
    assert s.source == "ocr" and "cross-check" in s.reading["reasons"][-1]


def test_a_doubtful_page_is_escalated_and_says_why(fake_engine):
    fake_engine(reading=reading(0.5))
    s = scan(mode="escalate", engine_id="tesseract", reader=vision_ok)
    assert s.source == "ocr" and s.verified and s.reading["escalated"] is True and s.reading["engine"] == "vision"
    assert s.reading["models"] == ["m-a", "m-b"] and "OCR confidence 50%" in s.reading["reasons"][0] and s.reading["local_confidence"] == pytest.approx(0.5)


def test_local_mode_only_flags_and_never_escalates(fake_engine):
    fake_engine(reading=reading(0.5))
    calls = []

    async def reader(model, jpeg):
        calls.append(1)
        return []
    s = scan(mode="local", engine_id="tesseract", reader=reader)
    assert calls == [] and s.source == "ocr-local" and s.reading["reasons"] and all(l.status == "review" for l in s.lines)


def test_unsure_lines_are_flagged_individually(fake_engine):
    r = local.build_reading([W(0, 0, 90, 20, "Invoice", 0.97), W(0, 40, 90, 60, "T0tal", 0.4)], "tesseract")
    fake_engine(reading=r)
    s = scan(mode="local", engine_id="tesseract")
    assert [l.status for l in s.lines] == ["agreed", "review"] and s.lines[1].reason


def test_vision_mode_skips_the_local_engine(fake_engine):
    eng = fake_engine(reading=reading())
    s = scan(mode="vision", engine_id="tesseract", reader=vision_ok)
    assert eng.calls == 0 and s.source == "ocr" and s.reading["escalated"] is False


def test_force_vision_overrides_a_good_local_reading(fake_engine):
    eng = fake_engine(reading=reading(0.99))
    s = scan(mode="escalate", engine_id="tesseract", reader=vision_ok, force_vision=True)
    assert eng.calls == 0 and s.source == "ocr" and s.reading["escalated"] and "re-read" in s.reading["reasons"][0]


def test_the_token_cap_stops_escalation(fake_engine):
    fake_engine(reading=reading(0.5))
    with track_usage("t") as u:
        u.record("discover", "m", total_tokens=900)
        capped = scan(mode="escalate", engine_id="tesseract", reader=vision_ok, token_cap=500)
        free = scan(mode="escalate", engine_id="tesseract", reader=vision_ok, token_cap=5000)
    assert capped.source == "ocr-local" and capped.reading["cost_capped"] and "token cap" in capped.reading["reasons"][-1]
    assert free.source == "ocr"


def test_a_failing_local_engine_falls_back_to_vision_and_vision_failure_keeps_local(fake_engine):
    fake_engine(fail=True)
    s = scan(mode="escalate", engine_id="tesseract", reader=vision_ok)
    assert s.source == "ocr" and s.reading["escalated"] and "could not read" in s.reading["reasons"][0]
    fake_engine(reading=reading(0.5))

    async def down(model, jpeg):
        raise RuntimeError("503")
    s = scan(mode="escalate", engine_id="tesseract", reader=down)
    assert s.source == "ocr-local" and any("unavailable" in r for r in s.reading["reasons"])     # the page is not lost
    fake_engine(fail=True)
    with pytest.raises(ocr.OcrError):
        scan(mode="escalate", engine_id="tesseract", reader=down)                               # nothing could read it: now it fails


def test_tesseract_words_from_the_work_pass_are_reused(fake_engine):
    eng = fake_engine(reading=reading())
    tsv = "h\n5\t1\t1\t1\t1\t1\t10\t10\t50\t20\t96\tHello\n"
    s = scan(mode="escalate", engine_id="tesseract", tsv=tsv)
    assert eng.calls == 0 and [l.text for l in s.lines] == ["Hello"]                            # no second OCR pass


# ---- region-level re-reads ----------------------------------------------------------------------------------------------

def page_reading(confs, engine="tesseract", size=(1000.0, 1400.0), digit_low=()):
    """A page with one line per confidence, stacked down the page, so each line has a box."""
    words = [local.Word(100, 100 + i * 100, 900, 140 + i * 100, f"Line number {i + 1} text", c) for i, c in enumerate(confs)]
    r = local.build_reading(words, engine, size=size)
    for i in digit_low:
        r.line_digit_low[i] = True
    return r


def test_lines_know_where_they_sit_on_the_page():
    r = page_reading([0.9, 0.9])
    assert r.line_boxes == [(0.1, pytest.approx(100 / 1400), 0.9, pytest.approx(140 / 1400)), (0.1, pytest.approx(200 / 1400), 0.9, pytest.approx(240 / 1400))]
    assert local.build_reading([W(0, 0, 10, 10, "x")], "t").line_boxes is None                   # no image size, no boxes
    assert local.tsv_size("h\n1\t1\t0\t0\t0\t0\t0\t0\t2480\t3508\t-1\t\n") == (2480.0, 3508.0)


def test_doubtful_lines_and_their_regions():
    r = page_reading([0.97, 0.97, 0.3, 0.97, 0.97, 0.97, 0.97, 0.97, 0.97, 0.97])
    assert local.doubtful_lines(r, True) == [2]
    regions = local.plan_regions(r, [2])
    assert len(regions) == 1 and (regions[0].first, regions[0].last) == (2, 2)
    x0, y0, x1, y1 = regions[0].box
    assert x0 < 0.1 and x1 > 0.9 and y0 < 300 / 1400 and y1 > 340 / 1400                         # padded around the line
    near = local.plan_regions(r, [2, 4])
    assert len(near) == 1 and (near[0].first, near[0].last) == (2, 4)                            # one good line between: one region
    far = local.plan_regions(r, [1, 5, 9])
    assert len(far) == 3
    assert local.plan_regions(r, [0, 3, 6, 9]) is None                                            # four bands: cheaper to read the page
    assert local.plan_regions(r, list(range(5))) is None                                          # half the lines: the page itself is poor
    assert local.plan_regions(local.build_reading([W(0, 0, 10, 10, "x")], "t"), [0]) is None     # nothing located
    digits = page_reading([0.97] * 6, digit_low=[3])
    assert local.doubtful_lines(digits, True) == [3]                                       # a doubtful number is enough
    assert local.doubtful_lines(page_reading([0.97, 0.7]), True) == []                           # mid confidence is a page-level matter, not a bad line
    assert local.doubtful_lines(page_reading([0.97, 0.5]), True) == [1]
    agree = page_reading([1.0, 0.5])                                                             # uncalibrated engines: line_conf is agreement
    assert local.doubtful_lines(agree, False) == [1]
    crop = local.crop(jpeg_of(True), (0.1, 0.5, 0.9, 0.51))
    from PIL import Image
    assert Image.open(io.BytesIO(crop)).size[1] >= 96                                             # a thin strip is made tall enough to read


def region_scan(reading_obj, reader, **kw):
    return run(ladder.read_scanned(jpeg=jpeg_of(True), models=["m-a", "m-b"], mode="escalate", engine_id="tesseract", reader=reader, **kw))


def test_a_page_with_one_weak_line_sends_only_that_region(fake_engine):
    r = page_reading([0.97, 0.97, 0.3, 0.97, 0.97, 0.97, 0.97, 0.97, 0.97, 0.97])
    fake_engine(reading=r)
    seen = []

    async def reader(model, jpeg):
        from PIL import Image
        seen.append((model, Image.open(io.BytesIO(jpeg)).size))
        return ["Line number 3 text (as the models read it)"]
    s = region_scan(r, reader)
    assert len(seen) == 2 and {m for m, _ in seen} == {"m-a", "m-b"}                             # one region, read by both models
    assert all(size[0] <= 600 and size[1] < 800 * 0.25 for _, size in seen)                       # a strip, not the page
    texts = [l.text for l in s.lines]
    assert len(texts) == 10 and texts[2] == "Line number 3 text (as the models read it)" and texts[0] == "Line number 1 text"
    assert all(l.status == "agreed" for l in s.lines)                                            # both models agreed on the region
    assert s.source == "ocr-local" and not s.verified
    assert s.reading["escalated"] is True and s.reading["scope"] == "regions" and s.reading["regions"] == 1 and s.reading["region_lines"] == 1
    assert "1 of 10 lines" in s.reading["reasons"][-1]


def test_models_that_disagree_on_a_region_are_flagged_there(fake_engine):
    r = page_reading([0.97, 0.3, 0.97, 0.97, 0.97, 0.97, 0.97])
    fake_engine(reading=r)

    async def reader(model, jpeg):
        return ["Total due 120.00"] if model == "m-a" else ["Total due 128.00"]
    s = region_scan(r, reader)
    assert [l.status for l in s.lines].count("review") == 1 and s.lines[1].status == "review" and s.lines[1].alternatives


def test_scattered_or_widespread_doubt_reads_the_whole_page(fake_engine):
    calls = []

    async def reader(model, jpeg):
        from PIL import Image
        calls.append(Image.open(io.BytesIO(jpeg)).size)
        return ["whole page line"]
    fake_engine(reading=page_reading([0.3, 0.97, 0.97, 0.3, 0.97, 0.97, 0.3, 0.97, 0.97, 0.3]))
    s = region_scan(None, reader)                                                                # four separate bands
    assert s.source == "ocr" and s.reading["scope"] == "page" and all(c == (600, 800) for c in calls)
    calls.clear()
    fake_engine(reading=page_reading([0.3, 0.3, 0.3, 0.97, 0.97, 0.97]))
    assert region_scan(None, reader).source == "ocr"                                             # half the lines are doubtful


def test_a_failing_region_read_keeps_a_good_page_and_reads_a_poor_one_whole(fake_engine):
    sizes = []

    async def reader(model, jpeg):
        from PIL import Image
        size = Image.open(io.BytesIO(jpeg)).size
        sizes.append(size)
        if size != (600, 800):
            raise RuntimeError("crop refused")
        return ["whole page"]
    good = page_reading([0.97, 0.3, 0.97, 0.97, 0.97, 0.97, 0.97])
    fake_engine(reading=good)
    s = region_scan(None, reader)                                                                # the page passed overall: keep it, flagged
    assert s.source == "ocr-local" and any("could not re-read" in r for r in s.reading["reasons"]) and s.lines[1].status == "review"
    assert (600, 800) not in sizes
    poor = page_reading([0.97, 0.3, 0.97, 0.97, 0.97, 0.97, 0.97])
    poor.digit_low_share = 0.5                                                                   # the page fails as a whole too
    fake_engine(reading=poor)
    s = region_scan(None, reader)
    assert s.source == "ocr" and s.reading["scope"] == "page" and (600, 800) in sizes


def test_regions_respect_the_token_cap_and_force(fake_engine):
    fake_engine(reading=page_reading([0.97, 0.3, 0.97, 0.97, 0.97, 0.97, 0.97]))

    async def reader(model, jpeg):
        return ["x"]
    with track_usage("t") as u:
        u.record("discover", "m", total_tokens=900)
        s = region_scan(None, reader, token_cap=500)
    assert s.source == "ocr-local" and s.reading.get("cost_capped") and s.reading.get("scope") != "regions" and "doubtful lines" in s.reading["reasons"][-1]
    s = region_scan(None, reader, force_vision=True)                                             # an explicit request reads the page
    assert s.source == "ocr" and s.reading["scope"] == "page"


def test_a_handwriting_region_marks_the_page(fake_engine):
    fake_engine(reading=page_reading([0.97, 0.3, 0.97, 0.97, 0.97, 0.97, 0.97]))

    async def reader(model, jpeg):
        out = ocr.Lines(["scribbled"])
        out.handwriting = True
        return out
    assert region_scan(None, reader).handwritten is True


# ---- through the whole pipeline ---------------------------------------------------------------------------------------

def scanned_pdf(path, lines_per_page):
    """Image-only pages (so each is a scan) with printed text drawn in."""
    import pymupdf as fitz
    from PIL import Image, ImageDraw, ImageFont
    doc = fitz.open()
    font = ImageFont.load_default(size=34)
    for lines in lines_per_page:
        img = Image.new("RGB", (1224, 1584), "white")
        d = ImageDraw.Draw(img)
        for i, t in enumerate(lines):
            d.text((90, 120 + i * 80), t, fill="black", font=font)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        page = doc.new_page(width=612, height=792)
        page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(path)
    doc.close()
    return str(path)


PAGE_A = ["Invoice No. 48213", "Date: 03/14/2026", "Total due $1,250.75", "Customer Northwind Traders"]


@pytest.mark.skipif(not tess_available(), reason="Tesseract is not installed")
def test_balanced_plan_reads_a_clean_scan_without_any_vision_call(tmp_path, clean_env):
    clean_env.setenv("OPENAI_API_KEY", "k")
    pdf = scanned_pdf(tmp_path / "scan.pdf", [PAGE_A])
    vision, structuring = [], []

    async def reader(model, jpeg):
        vision.append(model)
        return ["x"]

    async def form(model, jpeg, prompt):
        structuring.append(jpeg is None)               # text-only reads have no image
        return {"title": "Invoice", "checkboxes": [], "tables": [], "fields": [{"label": "Invoice No", "value": "48213"}, {"label": "Total due", "value": "1,250.75"}]}
    from app.docproc.services.extraction import discover
    clean_env.setattr(discover, "_read", form)
    res = run(pipeline.process(pdf, plan=plan.resolve({"strategy": "balanced"}), ocr_reader=reader, form_reader=form))
    page = res["pages"][0]
    assert vision == [] and page["source"] == "ocr-local" and page["reading"]["engine"] == "tesseract" and not page["reading"]["escalated"]
    assert "48213" in page["text"] and "1,250.75" in page["text"]
    assert structuring and all(structuring)            # the page was structured from its text, never from the picture
    assert res["stats"]["local_ocr_pages"] == 1 and res["stats"]["vision_pages"] == 0 and res["stats"]["escalated_pages"] == 0
    assert res["reading"]["strategy"] == "balanced" and res["reading"]["fingerprint"]
    values = {c["label"]: c["value"] for c in res["forms"][0]["fields"].values()}
    assert values["Invoice No"] == "48213"


def test_pipeline_escalates_only_the_doubtful_page(tmp_path, clean_env, fake_engine):
    clean_env.setenv("OPENAI_API_KEY", "k")
    pdf = scanned_pdf(tmp_path / "two.pdf", [PAGE_A, PAGE_A])
    good, bad = reading(0.97), reading(0.45)
    state = {"n": 0}

    class PerPage(FakeLocal):
        async def read(self, jpeg):                                  # first call -> a good page, second -> a poor one
            state["n"] += 1
            return good if state["n"] == 1 else bad
    clean_env.setitem(local._ENGINES, "tesseract", PerPage())
    seen = []

    async def reader(model, jpeg):
        seen.append(model)
        return ["Invoice No. 4821", "Total $120.00"]

    async def form(model, jpeg, prompt):
        return {"fields": {}, "table_rows": []}
    # this test is about page reading, so the pipeline's own tesseract word table must not be used
    clean_env.setattr(pdf_pages, "build_work_pdf", lambda src, dst, scanned, tsv=None: (__import__("shutil").copy(src, dst), {})[1])
    res = run(pipeline.process(pdf, plan=plan.resolve({"strategy": "balanced"}), ocr_reader=reader, form_reader=form))
    sources = sorted(p["source"] for p in res["pages"])
    assert sources == ["ocr", "ocr-local"] and len(seen) == 2          # one page, two vision readers
    assert res["stats"]["escalated_pages"] == 1 and res["stats"]["vision_pages"] == 1 and res["stats"]["local_ocr_pages"] == 1


def test_economy_plan_never_calls_a_model_even_with_readers_available(tmp_path, clean_env, fake_engine):
    clean_env.setenv("OPENAI_API_KEY", "k")
    fake_engine(reading=reading(0.4))
    pdf = scanned_pdf(tmp_path / "one.pdf", [PAGE_A])
    clean_env.setattr(pdf_pages, "build_work_pdf", lambda src, dst, scanned, tsv=None: (__import__("shutil").copy(src, dst), {})[1])
    called = []

    async def boom(*a):
        called.append(a)
        return []
    res = run(pipeline.process(pdf, plan=plan.resolve({"strategy": "economy"}), ocr_reader=boom, form_reader=boom))
    assert called == [] and res["pages"][0]["source"] == "ocr-local" and res["pages"][0]["reading"]["reasons"]
    assert res["reading"]["uses_models"] is False


def test_force_vision_sends_chosen_pages_to_the_models(tmp_path, clean_env, fake_engine):
    clean_env.setenv("OPENAI_API_KEY", "k")
    eng = fake_engine(reading=reading(0.99))
    pdf = scanned_pdf(tmp_path / "two.pdf", [PAGE_A, PAGE_A])
    clean_env.setattr(pdf_pages, "build_work_pdf", lambda src, dst, scanned, tsv=None: (__import__("shutil").copy(src, dst), {})[1])
    res = run(pipeline.process(pdf, plan=plan.resolve({"strategy": "balanced"}), ocr_reader=vision_ok, force_vision={2},
                               form_reader=lambda *a: asyncio.sleep(0, {"fields": {}, "table_rows": []})))
    by = {p["page"]: p["source"] for p in res["pages"]}
    assert by == {1: "ocr-local", 2: "ocr"} and eng.calls == 1


# ---- the API ------------------------------------------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    with TestClient(reload_app(tmp_path, monkeypatch)) as c:
        yield c


def test_engines_overview(client):
    r = client.get("/api/engines")
    assert r.status_code == 401
    body = client.get("/api/engines", headers=H).json()
    assert [e["id"] for e in body["local_engines"]] == ["tesseract", "rapidocr", "paddleocr", "doctr"]
    assert {p["id"] for p in body["providers"]} == {"openai", "anthropic", "gemini", "ollama", "custom"}
    assert [s["id"] for s in body["strategies"]] == ["economy", "balanced", "accuracy", "custom"]
    assert body["local_only"] is False and body["defaults"]["plan"]["strategy"] in ("balanced", "accuracy")
    assert "OPENAI_API_KEY" in next(p for p in body["providers"] if p["id"] == "openai")["hint"]
    health = client.get("/health").json()
    assert "local_engines" in health and "providers" in health and health["local_only"] is False


def test_workspace_reading_settings_round_trip(client, monkeypatch):
    wid = mk(client)
    url = f"/api/workspaces/{wid}/reading"
    assert client.get(url).status_code == 401
    got = client.get(url, headers=H).json()
    assert got["settings"] == {} and got["plan"]["strategy"] == got["strategy_default"]
    saved = client.put(url, json={"strategy": "economy", "escalate_below": 0.7}, headers=H)
    assert saved.status_code == 200 and saved.json()["plan"]["strategy"] == "economy" and saved.json()["plan"]["escalate_below"] == 0.7
    assert client.get(url, headers=H).json()["settings"] == {"strategy": "economy", "escalate_below": 0.7}
    other = mk(client, "Other")
    assert client.get(f"/api/workspaces/{other}/reading", headers=H).json()["settings"] == {}   # settings belong to one workspace
    bad = client.put(url, json={"strategy": "wild"}, headers=H)
    assert bad.status_code == 422 and "Choose" in bad.json()["detail"]
    assert client.put(url, json={"models": ["anthropic:claude-haiku-5-5"]}, headers=H).status_code == 422     # no Anthropic key here
    assert client.put(f"/api/workspaces/nope/reading", json={}, headers=H).status_code == 404
    assert client.delete(f"/api/workspaces/{wid}", headers=H).status_code == 204


def test_engine_test_endpoint(client, monkeypatch):
    assert client.post("/api/engines/test", json={"kind": "local", "id": "nope"}, headers=H).status_code == 404
    assert client.post("/api/engines/test", json={"kind": "other", "id": "x"}, headers=H).status_code == 422
    assert client.post("/api/engines/test", json={"kind": "model", "id": "no spaces allowed"}, headers=H).status_code == 422
    gone = FakeLocal()                                                                           # whatever is installed on this machine
    gone.id, gone.label, gone.install = "doctr", "docTR", "pip install doctr"
    gone.available = lambda: False
    monkeypatch.setitem(local._ENGINES, "doctr", gone)
    r = client.post("/api/engines/test", json={"kind": "local", "id": "doctr"}, headers=H).json()
    assert r["ok"] is False and "not installed" in r["error"]
    r = client.post("/api/engines/test", json={"kind": "model", "id": "anthropic:claude-haiku-5-5"}, headers=H).json()
    assert r["ok"] is False and "not available" in r["error"]                                    # no key: a clear message, not a 500
    if tess_available():
        r = client.post("/api/engines/test", json={"kind": "local", "id": "tesseract"}, headers=H).json()
        assert r["ok"] and r["score"] >= 0.75 and r["tokens"] == 0
    monkeypatch.setenv("LLM_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    async def fake_post(url, headers, body):
        return {"content": [{"type": "text", "text": '{"lines": ["Invoice No. 48213", "Date: 03/14/2026", "Total due $1,250.75", "Ref AB-9071"]}'}],
                "usage": {"input_tokens": 700, "output_tokens": 40}}
    monkeypatch.setattr(llm, "_post", fake_post)
    r = client.post("/api/engines/test", json={"kind": "model", "id": "anthropic:claude-haiku-5-5"}, headers=H).json()
    assert r["ok"] and r["score"] == 1.0 and r["tokens"] == 740


def test_upload_reading_choice_and_reuse(client, tmp_path, monkeypatch):
    from tests.docproc.make_packet import build
    pdf = tmp_path / "p.pdf"
    build(str(pdf))
    wid = mk(client)
    base = f"/api/workspaces/{wid}/extractions"
    send = lambda reading="": client.post(base, files={"file": ("p.pdf", pdf.read_bytes(), "application/pdf")}, data={"template": "auto", "reading": reading}, headers=H)
    assert send("wild").status_code == 422
    first = send("economy").json()
    done = wait(client, wid, first["id"])
    assert done["status"] == "done" and done["reading"]["strategy"] == "economy" and done["reading"]["uses_models"] is False
    assert send("economy").json().get("reused") is True                       # the same file under the same plan is not read again
    other = send("accuracy").json()
    assert other["id"] != first["id"] and other.get("reused") is not True      # a different plan is a different reading


def test_reread_makes_a_second_extraction_and_leaves_the_first(client, tmp_path, monkeypatch):
    from tests.docproc.make_packet import build
    pdf = tmp_path / "p.pdf"
    build(str(pdf))
    wid = mk(client)
    base = f"/api/workspaces/{wid}/extractions"
    first = client.post(base, files={"file": ("p.pdf", pdf.read_bytes(), "application/pdf")}, data={"template": "auto"}, headers=H).json()["id"]
    wait(client, wid, first)
    assert client.post(f"{base}/{first}/reread", json={}, headers=H).status_code == 503              # no model here: it must say so
    monkeypatch.setattr(config, "llm_enabled", lambda: True)
    assert client.post(f"{base}/{first}/reread", json={"strategy": "wild"}, headers=H).status_code == 422
    assert client.post(f"{base}/{first}/reread", json={"pages": [99]}, headers=H).status_code == 422
    assert client.post(f"{base}/nope/reread", json={}, headers=H).status_code == 404
    from app.docproc.services.reading import ocr as ocr_mod
    from tests import test_pipeline as tp
    monkeypatch.setattr(ocr_mod, "read_image", tp.fake_ocr)
    monkeypatch.setattr(pipeline.form_extractor, "_read", tp.fake_form)
    r = client.post(f"{base}/{first}/reread", json={"pages": [4]}, headers=H)
    assert r.status_code == 202 and r.json()["id"] != first
    done = wait(client, wid, r.json()["id"])
    assert done["status"] == "done" and done["filename"] == "p.pdf"
    page4 = next(p for p in done["pages"]["pages"] if p["page"] == 4)
    assert page4["source"] == "ocr"                                                                  # the page you picked went to the models
    assert client.get(f"{base}/{first}", headers=H).json()["status"] == "done"                      # the original is untouched
    assert len(client.get(base, headers=H).json()) == 2
    other = mk(client, "Other")
    assert client.post(f"/api/workspaces/{other}/extractions/{first}/reread", json={}, headers=H).status_code == 404


# ---- downloading models from the page ---------------------------------------------------------------------------------

@pytest.fixture()
def preparing(monkeypatch, tmp_path):
    from app.docproc.engines.ocr import prepare
    monkeypatch.setattr(prepare, "_state", {})
    monkeypatch.setenv("DOCPROC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DOCPROC_MODELS_DIR", str(tmp_path / "models"))
    for k in ("DOCTR_CACHE_DIR", "PADDLE_PDX_CACHE_HOME", "LOCAL_ONLY", "LOCK_SETTINGS"):
        monkeypatch.delenv(k, raising=False)
    return prepare


class Downloader(FakeLocal):
    """An engine whose first read 'downloads its models' into the app's models folder."""
    id, label = "doctr", "docTR"

    def __init__(self, fail=False, gate=None):
        super().__init__()
        self.fail, self.gate = fail, gate

    async def read(self, jpeg):
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("no network")
        folder = local.model_folders()["doctr"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "m.pt").write_bytes(b"x" * 10)
        return local.build_reading([W(0, 0, 90, 20, "Invoice No. 48213 Date: 03/14/2026 Total due $1,250.75 Ref AB-9071", 0.9)], "doctr")


def test_a_download_refuses_what_it_should(preparing, monkeypatch):
    monkeypatch.setitem(local._ENGINES, "doctr", Downloader())
    with pytest.raises(preparing.Refused) as e:
        preparing.start("nope")
    assert e.value.status == 404
    for engine, status in (("tesseract", 409), ("rapidocr", 409)):                                  # nothing to download
        monkeypatch.setitem(local._ENGINES, "tesseract", FakeLocal())
        with pytest.raises(preparing.Refused) as e:
            preparing.start(engine)
        assert e.value.status == status
    absent = Downloader()
    absent.available = lambda: False
    monkeypatch.setitem(local._ENGINES, "doctr", absent)
    with pytest.raises(preparing.Refused, match="not installed") as e:
        preparing.start("doctr")
    assert e.value.status == 409
    monkeypatch.setitem(local._ENGINES, "doctr", Downloader())
    monkeypatch.setenv("LOCAL_ONLY", "1")
    with pytest.raises(preparing.Refused, match="Local-only") as e:
        preparing.start("doctr")                                                                    # nothing leaves a local-only server
    assert e.value.status == 403
    monkeypatch.delenv("LOCAL_ONLY")
    monkeypatch.setenv("LOCK_SETTINGS", "1")
    with pytest.raises(preparing.Refused, match="locked") as e:
        preparing.start("doctr")
    assert e.value.status == 403


def test_a_download_runs_in_the_background_and_reports(preparing, monkeypatch):
    async def go():
        gate = asyncio.Event()
        monkeypatch.setitem(local._ENGINES, "doctr", Downloader(gate=gate))
        assert local.model_status("doctr")["state"] == "missing"
        assert preparing.start("doctr")["state"] == "running" and preparing.running()
        with pytest.raises(preparing.Refused, match="already running"):
            preparing.start("doctr")
        gate.set()
        for _ in range(100):
            if preparing.current("doctr")["state"] != "running":
                break
            await asyncio.sleep(0.02)
        return preparing.current("doctr")
    state = run(go())
    assert state["state"] == "done" and not preparing.running()
    assert local.model_status("doctr")["state"] == "downloaded"                                     # the models are now in the app's folder


def test_a_failed_download_says_so_and_can_be_retried(preparing, monkeypatch):
    async def go(eng):
        monkeypatch.setitem(local._ENGINES, "doctr", eng)
        preparing.start("doctr")
        for _ in range(100):
            if preparing.current("doctr")["state"] != "running":
                break
            await asyncio.sleep(0.02)
        return preparing.current("doctr")
    bad = run(go(Downloader(fail=True)))
    assert bad["state"] == "failed" and "internet" in bad["error"] and "no network" not in bad["error"]   # a safe message, not the exception
    assert run(go(Downloader()))["state"] == "done"


def test_the_download_endpoint_and_overview(client, preparing, monkeypatch):
    monkeypatch.setitem(local._ENGINES, "doctr", Downloader())
    assert client.post("/api/engines/doctr/prepare").status_code == 401
    assert client.post("/api/engines/nope/prepare", headers=H).status_code == 404
    assert client.post("/api/engines/tesseract/prepare", headers=H).status_code == 409
    over = client.get("/api/engines", headers=H).json()
    doctr = next(e for e in over["local_engines"] if e["id"] == "doctr")
    assert doctr["models"]["state"] == "missing" and doctr["prepare"] is None and over["models_dir"] and over["locked"] is False
    assert next(e for e in over["local_engines"] if e["id"] == "tesseract")["models"]["state"] == "none"
    r = client.post("/api/engines/doctr/prepare", headers=H)
    assert r.status_code == 202 and r.json()["state"] == "running"
    for _ in range(100):
        doctr = next(e for e in client.get("/api/engines", headers=H).json()["local_engines"] if e["id"] == "doctr")
        if doctr["prepare"]["state"] != "running":
            break
        time.sleep(0.05)
    assert doctr["prepare"]["state"] == "done" and doctr["models"]["state"] == "downloaded"


# ---- engines in their own processes -------------------------------------------------------------------------------------

FAKE_WORKER = r'''
import sys, struct, json, os, time
inp, out = sys.stdin.buffer, sys.stdout.buffer
while True:
    h = inp.read(4)
    if len(h) < 4:
        break
    n = struct.unpack(">I", h)[0]
    data = inp.read(n)
    if data == b"die": os._exit(3)
    if data == b"hang": time.sleep(30)
    if data == b"junk":
        out.write(struct.pack(">I", 4) + b"nope"); out.flush(); continue
    reply = {"ok": False, "error": "ValueError: nope"} if data == b"bad" else {"ok": True, "words": [[0, 0, 10, 10, "pid%d" % os.getpid(), 0.9]]}
    b = json.dumps(reply).encode()
    out.write(struct.pack(">I", len(b)) + b); out.flush()
'''


@pytest.fixture()
def fake_worker(tmp_path):
    import sys
    from app.docproc.engines.ocr import isolated
    made = []

    def make(**kw):
        w = isolated.Worker("Fake engine", [sys.executable, "-c", FAKE_WORKER], log=tmp_path / "logs" / "fake.log", **kw)
        made.append(w)
        return w
    yield make
    for w in made:
        w.stop()


def test_a_worker_process_answers_page_after_page(fake_worker):
    w = fake_worker()
    first, second = w.call(b"page one"), w.call(b"page two")
    assert first[0][4] == second[0][4] and first[0][4].startswith("pid")                             # the same process: the model is loaded once
    assert first[0][:4] == [0, 0, 10, 10] and first[0][5] == 0.9


def test_a_bad_page_is_reported_and_the_process_carries_on(fake_worker):
    from app.docproc.engines.ocr import isolated
    w = fake_worker()
    pid = w.call(b"ok")[0][4]
    with pytest.raises(isolated.WorkerError, match="could not read the page: ValueError: nope"):
        w.call(b"bad")
    assert w.call(b"ok")[0][4] == pid                                                                # one bad page does not cost the model


def test_a_crashed_worker_fails_that_page_and_the_next_one_starts_fresh(fake_worker):
    from app.docproc.engines.ocr import isolated
    w = fake_worker()
    pid = w.call(b"ok")[0][4]
    with pytest.raises(isolated.WorkerError, match="stopped unexpectedly"):
        w.call(b"die")
    again = w.call(b"ok")[0][4]
    assert again != pid                                                                              # a new process, so the server keeps working


def test_a_worker_that_hangs_is_killed(fake_worker):
    from app.docproc.engines.ocr import isolated
    w = fake_worker(first_timeout=0.6, timeout=0.6)
    with pytest.raises(isolated.WorkerError, match="did not answer within"):
        w.call(b"hang")
    assert w.call(b"ok")[0][4].startswith("pid")                                                     # and the next page is fine


def test_an_unreadable_answer_is_an_error_not_a_crash(fake_worker):
    from app.docproc.engines.ocr import isolated
    w = fake_worker()
    with pytest.raises(isolated.WorkerError, match="could not be read"):
        w.call(b"junk")
    assert w.call(b"ok")[0][4].startswith("pid")


def test_the_worker_loop_answers_frames_and_survives_an_engine_error():
    import json
    import struct
    from app.docproc.engines import worker

    class Engine:
        def _words(self, jpeg):
            if jpeg == b"boom":
                raise RuntimeError("model fell over")
            return [local.Word(1, 2, 3, 4, "hi", 0.5)]

    def frame(b):
        return struct.pack(">I", len(b)) + b
    inp, out = io.BytesIO(frame(b"a") + frame(b"boom") + frame(b"b")), io.BytesIO()
    worker.serve(Engine(), inp, out)
    out.seek(0)
    replies = []
    while (data := worker.read_frame(out)) is not None:
        replies.append(json.loads(data))
    assert [r["ok"] for r in replies] == [True, False, True]
    assert replies[0]["words"] == [[1, 2, 3, 4, "hi", 0.5]] and "RuntimeError: model fell over" in replies[1]["error"]


def test_only_the_engines_that_cannot_share_a_process_are_isolated(monkeypatch):
    from app.docproc.engines.ocr import isolated
    assert {e.id: e.isolated for e in local.engines()} == {"tesseract": False, "rapidocr": False, "paddleocr": True, "doctr": True}

    class Heavy(local.Engine):
        id, label, isolated = "heavy", "Heavy", True
        def available(self): return True
        def _words(self, jpeg): raise AssertionError("must run in the child process")
    seen = []
    monkeypatch.setattr(isolated, "call", lambda eid, label, jpeg: (seen.append((eid, label)), [[1, 2, 30, 40, "x", 0.8]])[1])
    r = run(Heavy().read(jpeg_of(False)))
    assert seen == [("heavy", "Heavy")] and r.lines == ["x"] and r.confidence == 0.8                  # the parent only talks to the worker
    monkeypatch.setattr(local, "IN_WORKER", True)

    class Inside(Heavy):
        def _words(self, jpeg): return [W(0, 0, 10, 10, "direct")]
    assert run(Inside().read(jpeg_of(False))).lines == ["direct"]                                     # inside the worker it runs the engine itself


def test_the_worker_is_started_from_the_folder_that_holds_the_app_package():
    """Moving isolated.py a level deeper once left the child process unable to import `app` (it started in the wrong folder).
    The command that starts it must resolve from the folder it is given."""
    import subprocess
    import sys
    from app.docproc.engines.ocr import isolated
    assert (isolated._BACKEND_ROOT / "app" / "docproc" / "engines" / "worker.py").is_file()
    out = subprocess.run([sys.executable, "-m", "app.docproc.engines.worker"], cwd=str(isolated._BACKEND_ROOT), capture_output=True, timeout=60)
    assert out.returncode == 2 and b"usage: python -m app.docproc.engines.worker" in out.stderr                # it started, imported app, and asked for an engine


# -- run-together words (RapidOCR) and titles set beside other columns ---------------------------------------------------

def test_respace_restores_spaces_from_the_reference_reading_without_changing_a_character():
    ref = local.Reading("tesseract", ["Date of Initial Examination: 08/12/2026 | 25 Testway Ave Ste 201", "Provider Prior Authorization Request Form"], [])
    glued = ["DateofInitialExamination:08/12/2026 | 25TestwayAveSte201", "ProviderPriorAuthorizationRequestForm"]
    out = local.respace(local.Reading("rapidocr", list(glued), [None, None]), ref)
    assert out.lines[1] == "Provider Prior Authorization Request Form"
    assert out.lines[0].startswith("Date of Initial") and "25 Testway Ave Ste 201" in out.lines[0]
    assert [l.replace(" ", "") for l in out.lines] == [l.replace(" ", "") for l in glued]


def test_respace_leaves_words_the_reference_does_not_explain():
    ref = local.Reading("tesseract", ["Sample Health Associates"], [])
    odd = ["Xylophonemaestroqrstuvw", "Sample"]
    assert local.respace(local.Reading("rapidocr", list(odd), [None, None]), ref).lines == odd


def test_a_form_title_without_spaces_still_classifies():
    from app.docproc.services.documents import classify
    assert classify.classify_text("MAGNACARE\nProviderPriorAuthorizationRequestForm\n" + "x" * 60)["type"] == "prior_auth_form"


def test_a_title_in_a_side_column_classifies():
    from app.docproc.services.documents import classify
    page = "\n".join(["Sample Health Associates", "25 Testway Ave Ste 201 | Physical Therapy", "Testville NJ 07001-1718 | Initial",
                      "Phone: (201) 447-5757", "Fax: (201) 447-5750 | Examination", "Patient Name: Morgan Taylor | Visit No.: 1"])
    assert classify.classify_text(page)["type"] == "pt_initial_exam"


def test_a_sentence_that_mentions_a_form_is_still_not_its_title():
    from app.docproc.services.documents import classify
    page = "Dear Sir,\nPlease return the Prior Authorization Request Form within ten business days of receiving this letter.\n" + "text " * 30
    assert classify.classify_text(page)["type"] == "unknown"


def test_a_fax_stamp_without_spaces_is_still_a_stamp():
    from app.docproc.services.reading import pdf_pages
    content, meta = pdf_pages.strip_fax_stamp(["From Sample HealthAssociates1.201.555.0121 | WedNov1109:21:382026MSTPage4of9", "real line"])
    assert content == ["real line"] and meta["fax_page"] == 4 and meta["fax_pages"] == 9


def test_label_value_reading_does_not_carry_the_column_gap_into_values():
    from app.docproc.services.extraction import kv
    pairs = kv.parse_kv(["Patient Name: Morgan Taylor | Date of Initial Examination: 08/12/2026", "Visit No.: 1 | Initial"])
    assert {p["label"]: p["value"] for p in pairs} == {"Patient Name": "Morgan Taylor", "Date of Initial Examination": "08/12/2026", "Visit No.": "1"}


# -- reading a template's fields from OCR text, no model -----------------------------------------------------------------

_PT_TEXT = "\n".join([
    "Sample Health Associates",
    "25 Testway Ave Ste 201 | Physical Therapy",
    "Patient Name: Morgan Taylor | Date of Initial Examination:08/12/2026",
    "Date of Birth:01/15/1975 | Injury/Onset/Change of Status Date:03/10/2025",
    "Referring Physician/NPP: Jones, Stephen DC | Diagnosis: ICD10: M54.2: Cervicalgia",
    "Surgery: (Date/Type) 03/10/2025 peroneal tendon repair, | Visit No.: 1",
    "ostectomy of fibula, calcaneal osteotomy",
    "Treatment Diagnosis:ICD10: M54.2: Cervicalgia",
    "Treatment Side: Left",
])


def _pt_template():
    import json
    from pathlib import Path
    return json.loads((Path(__file__).resolve().parents[2] / "app" / "docproc" / "templates" / "pt_initial_exam.json").read_text())


def test_local_reader_fills_template_fields_from_their_printed_labels():
    from app.docproc.services.extraction import local_reader
    got = local_reader.read_fields(_PT_TEXT, _pt_template())["fields"]
    assert got["patient.name"] == "Morgan Taylor"
    assert got["exam_date"] == "08/12/2026" and got["patient.dob"] == "01/15/1975" and got["onset_date"] == "03/10/2025"
    assert got["referring_physician"] == "Jones, Stephen DC" and got["visit_no"] == "1" and got["treatment_side"] == "Left"
    assert got["surgery"] == "03/10/2025 peroneal tendon repair, ostectomy of fibula, calcaneal osteotomy"


def test_local_reader_values_must_suit_the_field_type_and_blanks_stay_blank():
    from app.docproc.services.extraction import local_reader
    got = local_reader.read_fields("Date of Birth: unknown\nPatient Name:", _pt_template())["fields"]
    assert "patient.dob" not in got and "patient.name" not in got


def test_local_reader_survives_glued_labels():
    from app.docproc.services.extraction import local_reader
    got = local_reader.read_fields("PatientName:Morgan Taylor | DateofBirth:01/15/1975", _pt_template())["fields"]
    assert got["patient.name"] == "Morgan Taylor" and got["patient.dob"] == "01/15/1975"


def test_a_template_page_read_locally_gets_its_text_fields_without_a_model():
    import asyncio
    from app.docproc.services.extraction import form_extractor
    from app.docproc import pipeline
    tpl = _pt_template()
    from pathlib import Path
    pdf = Path(__file__).resolve().parent.parent.parent / "sample_data" / "short_p3-5.pdf"
    if not pdf.exists():
        pytest.skip("sample PDF not present")
    res = asyncio.run(form_extractor.extract_form(str(pdf), 2, "", tpl, [], page_text=_PT_TEXT))
    assert res["fields"]["patient.name"]["value"] == "Morgan Taylor" and res["fields"]["patient.name"]["status"] == "single"


def test_local_reader_never_borrows_a_value_from_another_section():
    import json
    from pathlib import Path
    from app.docproc.services.extraction import local_reader
    tpl = json.loads((Path(__file__).resolve().parents[2] / "app" / "docproc" / "templates" / "magnacare_prior_auth.json").read_text())
    text = "MEMBER INFORMATION\nLast Name*: Morgan | First Name*: Taylor\nORDERING/SERVICE PROVIDER INFORMATION\nTax ID*: 99-9999999"
    got = local_reader.read_fields(text, tpl)["fields"]
    assert got["member.last_name"] == "Morgan"
    assert "ordering_provider.last_name" not in got and "ordering_provider.first_name" not in got
    assert got["ordering_provider.tax_id"] == "999999999"
    assert "ordering_provider.last_name" not in local_reader.read_fields("Last Name*: Morgan", tpl)["fields"]      # header missing: no guess


# -- tables read from OCR text, no model ---------------------------------------------------------------------------------

def _template(name):
    import json
    from pathlib import Path
    return json.loads((Path(__file__).resolve().parents[2] / "app" / "docproc" / "templates" / f"{name}.json").read_text())


def test_local_reader_reads_a_table_by_its_printed_header():
    from app.docproc.services.extraction import local_reader
    text = "CPT/HCPCS\nQTY* | CPT/HCPCS* | Description of Service: | U&C Charge\n12 VISITS | 97110 | THERAPEUTIC EXERCISE | 180.00\n12 VISITS | 97140 | MANUAL THERAPY | 150.00\nUpdate to Current Auth #: | # of Visits: | Requested Extension Date:\nREVIEW TIMELINES"
    rows = local_reader.read_fields(text, _template("magnacare_prior_auth"))["table_rows"]
    assert rows == [{"qty": "12 VISITS", "code": "97110", "description": "THERAPEUTIC EXERCISE", "charge": "180.00"},
                    {"qty": "12 VISITS", "code": "97140", "description": "MANUAL THERAPY", "charge": "150.00"}]


def test_local_reader_table_header_words_may_be_shorter_than_the_templates():
    from app.docproc.services.extraction import local_reader
    text = "CPT Code | Description | Units\nGP:97110 | Therapeutic exercise | 2\nGP:97140 | Manual therapy | 1\nTotal time: 45 min | Signature: J.K."
    rows = local_reader.read_fields(text, _template("daily_note"))["table_rows"]
    assert [r["code"] for r in rows] == ["GP:97110", "GP:97140"] and rows[0]["description"] == "Therapeutic exercise" and rows[1]["units"] == "1"


def test_local_reader_table_without_a_header_is_anchored_on_its_code_pattern_and_stops_when_rows_end():
    from app.docproc.services.extraction import local_reader
    rows = local_reader.read_table(["GP:97110 | Therapeutic exercise | 2", "GP:97140 | Manual therapy | 1", "Signature | J.K."], _template("daily_note")["tables"][0])
    assert len(rows) == 2 and rows[1] == {"code": "GP:97140", "description": "Manual therapy", "units": "1"}


def test_local_reader_does_not_invent_table_rows_from_other_lines():
    from app.docproc.services.extraction import local_reader
    assert local_reader.read_fields("Patient Name: Morgan Taylor\nSome free text here | and here", _template("daily_note"))["table_rows"] == []
