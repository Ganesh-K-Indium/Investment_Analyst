"""
The language-model providers a user can bring, in one table.

A model is named by a spec, `provider:model` (`anthropic:claude-sonnet-5-5`, `ollama:llama3.1:8b`, `custom:my-model`); a bare name means
OpenAI, so every older setting keeps working. Suggested models are only suggestions: any model name the provider serves is valid.
Credential field names are the environment variables that set the same thing, so a deployment can be configured from the environment,
from the screen, or both (see service.py for the order).
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.docproc.engines.providers.credentials import FIELDS  # (field name -> label, secret, kind, help) per provider, shared with the extraction engine


@dataclass(frozen=True)
class ChatProvider:
    id: str
    label: str
    cloud: bool                                  # prompts leave this machine
    package: str                                 # LangChain integration module, for the "installed" check
    hint: str
    models: Tuple[str, ...] = ()
    strong: str = ""                             # default for writing / analysis jobs
    light: str = ""                              # default for routing / grading / rewriting jobs
    vision: bool = True
    caveat: str = ""
    presets: Tuple[Tuple[str, str], ...] = ()    # (label, base url) shortcuts for the self-hosted entry


PROVIDERS: Dict[str, ChatProvider] = {p.id: p for p in (
    ChatProvider("openai", "OpenAI", True, "langchain_openai", "Key from platform.openai.com. Also covers Azure-style gateways through a base URL.",
                 ("gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4-turbo"), "gpt-4o", "gpt-4o-mini"),
    ChatProvider("anthropic", "Anthropic Claude", True, "langchain_anthropic", "Key from console.anthropic.com.",
                 ("claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"), "claude-sonnet-5-5", "claude-haiku-5-5"),
    ChatProvider("gemini", "Google Gemini", True, "langchain_google_genai", "Key from aistudio.google.com.",
                 ("gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"), "gemini-2.5-pro", "gemini-2.5-flash"),
    ChatProvider("ollama", "Ollama (your own hardware)", False, "langchain_ollama", "Run Ollama and give its address. Nothing leaves your network.",
                 ("llama3.1:8b", "qwen2.5:14b", "mistral-nemo"), "", "", False,
                 "Choose a model that supports tool calling for the quant agents; small models often do not."),
    ChatProvider("custom", "Self-hosted or other OpenAI-compatible", False, "langchain_openai",
                 "Any server that speaks the OpenAI API: vLLM, LM Studio, OpenRouter, Groq, Together and others.", (), "", "", False,
                 "Structured output and tool calling depend on the server and model.",
                 (("OpenRouter", "https://openrouter.ai/api/v1"), ("Groq", "https://api.groq.com/openai/v1"), ("Together", "https://api.together.xyz/v1"),
                  ("LM Studio", "http://localhost:1234/v1"), ("vLLM", "http://localhost:8000/v1"))),
)}

EMBEDDINGS = {"provider": "openai", "model": "text-embedding-3-large", "dimensions": 3072,
              "note": "Search vectors are built with OpenAI embeddings and shared by every user, so this stays on the server's OpenAI key. "
                      "Changing the embedding model means re-indexing every collection."}

_SPEC_OK = re.compile(r"^[\w.:/@+\-]{1,100}$")


def split_spec(spec: str) -> Tuple[str, str]:
    """`provider:model` -> (provider, model). Only a known provider name before the first colon counts, so Ollama tags such as
    `llama3.1:8b` are not mistaken for a provider."""
    spec = (spec or "").strip()
    head, sep, rest = spec.partition(":")
    if sep and head.lower() in PROVIDERS:
        return head.lower(), rest.strip()
    return "openai", spec


def valid_spec(spec: Any) -> bool:
    return isinstance(spec, str) and bool(split_spec(spec)[1]) and bool(_SPEC_OK.match(spec))


def spec_of(provider: str, model: str) -> str:
    return model if provider == "openai" else f"{provider}:{model}"


def defaults_for(provider: str) -> Optional[Dict[str, str]]:
    """The strong/light model specs for a provider, or None when it has no built-in choice (Ollama, self-hosted: the user picks)."""
    p = PROVIDERS.get(provider)
    if not p or not p.strong:
        return None
    return {"strong": spec_of(provider, p.strong), "light": spec_of(provider, p.light)}


def installed(p: ChatProvider) -> bool:
    import importlib.util
    return importlib.util.find_spec(p.package) is not None


def fields_of(provider: str):
    return FIELDS.get(provider, ())
