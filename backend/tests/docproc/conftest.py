import pytest


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
