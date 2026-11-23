import pytest

# Tests that call into the standalone app's own HTTP layer (app.api.*), which is not part of this backend.
NEEDS_STANDALONE_API = {"test_tesseract_reads_a_clean_page_confidently", "test_generic_labels_are_not_offered_as_what_a_file_turned_out_to_be"}


def pytest_collection_modifyitems(items):
    skip = pytest.mark.skip(reason="drives the standalone extraction app's HTTP layer, which is not part of this backend")
    for item in items:
        if item.name in NEEDS_STANDALONE_API:
            item.add_marker(skip)


@pytest.fixture(autouse=True, scope="module")
def standalone_environment():
    """The folded-in extraction engine's tests were written for the standalone app: no cloud models and no provider keys in the environment,
    and the two-reader path exercised by default. Applied per test module (module fixtures build their results first), and undone after, so
    the rest of the suite sees the real environment."""
    mp = pytest.MonkeyPatch()
    for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OLLAMA_BASE_URL", "LLM_CUSTOM_BASE_URL", "LLM_CUSTOM_API_KEY"):
        mp.delenv(k, raising=False)
    mp.setenv("LLM_ENABLED", "0")
    mp.setenv("READING_STRATEGY", "accuracy")
    yield
    mp.undo()
