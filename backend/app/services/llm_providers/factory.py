"""Build a LangChain chat model from a spec, using the credentials in force (context.py)."""
from typing import Any, List, Optional

from app.docproc import config as docproc_config
from . import context, urls
from .catalog import PROVIDERS, split_spec


class ProviderNotConfigured(RuntimeError):
    """The provider a job is set to use has no key or address. The message is safe to show to the user."""

    def __init__(self, provider: str, detail: str = ""):
        self.provider = provider
        label = PROVIDERS[provider].label if provider in PROVIDERS else provider
        super().__init__(f"{label} is not connected. {detail or 'Add its key under Settings, AI providers, or choose another model.'}")


def configured(provider: str) -> bool:
    """Does the current request have what this provider needs?"""
    if provider not in PROVIDERS:
        return False
    if PROVIDERS[provider].cloud and docproc_config.local_only():
        return False
    g = context.get
    return {"openai": bool(g("OPENAI_API_KEY")), "anthropic": bool(g("ANTHROPIC_API_KEY")), "gemini": bool(g("GEMINI_API_KEY")),
            "ollama": bool(g("OLLAMA_BASE_URL")), "custom": bool(g("LLM_CUSTOM_BASE_URL"))}[provider]


def build(spec: str, *, temperature: float = 0.0, timeout: float = 60, max_retries: int = 2, callbacks: Optional[List[Any]] = None, max_tokens: Optional[int] = None):
    provider, model = split_spec(spec)
    if provider not in PROVIDERS:
        raise ProviderNotConfigured(provider)
    if PROVIDERS[provider].cloud and docproc_config.local_only():
        raise ProviderNotConfigured(provider, "This deployment keeps everything on the machine (LOCAL_ONLY), so cloud models are switched off.")
    if not configured(provider):
        raise ProviderNotConfigured(provider)
    g = context.get
    common = {"callbacks": callbacks}
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        base = g("OPENAI_BASE_URL")
        return ChatOpenAI(model=model, api_key=g("OPENAI_API_KEY"), base_url=urls.check(base) if base else None, temperature=temperature,
                          timeout=timeout, max_retries=max_retries, max_tokens=max_tokens, **common)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, api_key=g("ANTHROPIC_API_KEY"), temperature=temperature, timeout=timeout, max_retries=max_retries,
                             max_tokens=max_tokens or 8192, **common)
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, google_api_key=g("GEMINI_API_KEY"), temperature=temperature, timeout=timeout, max_retries=max_retries,
                                      max_output_tokens=max_tokens, **common)
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model, base_url=urls.check(g("OLLAMA_BASE_URL")), temperature=temperature, num_predict=max_tokens, **common)
    from langchain_openai import ChatOpenAI          # custom: OpenAI-compatible
    return ChatOpenAI(model=model, base_url=urls.check(g("LLM_CUSTOM_BASE_URL")), api_key=g("LLM_CUSTOM_API_KEY") or "not-needed", temperature=temperature,
                      timeout=timeout, max_retries=max_retries, max_tokens=max_tokens, **common)
