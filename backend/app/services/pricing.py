"""What a model's tokens cost, for any provider a user can connect. Estimates from list prices, in USD per 1M tokens.

A model is named by its spec (`provider:model`, see app/services/llm_providers/catalog.py). Lookup order:
  1. the deployment's own prices (`model_prices` in config.yaml / SETTINGS__MODEL_PRICES): exact spec, then bare model name,
     then a `provider:*` wildcard (e.g. `custom:*: [0, 0]` for a free self-hosted server);
  2. models running on your own hardware (Ollama, or a self-hosted server on localhost / a private address) cost nothing;
  3. the list prices below, matched on the model name: exact first, then the longest known name it starts with, so dated or
     tagged releases (`gpt-4o-2024-08-06`, `claude-sonnet-4-5-20250929`, OpenRouter's `anthropic/claude-sonnet-5-5`) still match.
Anything else is "unpriced": its tokens are still counted, and the dashboards say the cost is unknown rather than $0.

Update the table when providers change prices (PRICES_AS_OF says when it was last checked)."""
from __future__ import annotations

import ipaddress
import re
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

PRICES_AS_OF = "2026-11-27"

# (input, output) USD per 1M tokens. Embedding models have no output price.
LIST_PRICES: Dict[str, Tuple[float, float]] = {
    # OpenAI
    "gpt-5": (1.25, 10.0), "gpt-5-mini": (0.25, 2.0), "gpt-5-nano": (0.05, 0.40),
    "gpt-4.1": (2.0, 8.0), "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.0), "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.0, 30.0), "gpt-4": (30.0, 60.0), "gpt-3.5-turbo": (0.50, 1.50),
    "o1": (15.0, 60.0), "o1-mini": (1.10, 4.40), "o3": (2.0, 8.0), "o3-mini": (1.10, 4.40), "o4-mini": (1.10, 4.40),
    "text-embedding-3-large": (0.13, 0.0), "text-embedding-3-small": (0.02, 0.0), "text-embedding-ada-002": (0.10, 0.0),
    # Anthropic (first-party API rates)
    "claude-fable-5-2": (8.0, 40.0), "claude-mythos-5-2": (8.0, 40.0), "claude-fable-5-1": (10.0, 50.0),
    "claude-mythos-5-1": (10.0, 50.0), "claude-fable-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0), "claude-opus-4-8": (5.0, 25.0), "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0), "claude-opus-4-5": (5.0, 25.0), "claude-opus-4-1": (15.0, 75.0), "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-5-5": (2.0, 10.0), "claude-sonnet-5": (2.0, 10.0), "claude-sonnet-4-6": (3.0, 15.0), "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4": (3.0, 15.0), "claude-3-7-sonnet": (3.0, 15.0), "claude-3-5-sonnet": (3.0, 15.0),
    "claude-haiku-5-5": (0.10, 0.50), "claude-haiku-4-5": (1.0, 5.0), "claude-3-5-haiku": (0.80, 4.0), "claude-3-haiku": (0.25, 1.25),
    # Google Gemini (prompts up to 200K tokens)
    "gemini-2.5-pro": (1.25, 10.0), "gemini-2.5-flash": (0.30, 2.50), "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.0-flash": (0.10, 0.40), "gemini-2.0-flash-lite": (0.075, 0.30),
    "gemini-1.5-pro": (1.25, 5.0), "gemini-1.5-flash": (0.075, 0.30),
}

_LOCAL_PROVIDERS = {"ollama"}
_DATE_SUFFIX = re.compile(r"(?:[-@](?:\d{8}|\d{4}-\d{2}-\d{2}))$")
_VENDOR_PREFIX = re.compile(r"^(?:models/|(?:openai|anthropic|google|meta-llama|mistralai|x-ai|deepseek)/)")


def _split(spec: str) -> Tuple[str, str]:
    from app.services.llm_providers.catalog import split_spec
    return split_spec(spec or "")


def _normalise(model: str) -> str:
    m = (model or "").strip().lower()
    m = _VENDOR_PREFIX.sub("", m)
    m = m.split(":", 1)[0] if m.endswith((":free", ":beta")) else m
    return _DATE_SUFFIX.sub("", m)


def _list_price(model: str) -> Optional[Tuple[float, float]]:
    name = _normalise(model)
    if name in LIST_PRICES:
        return LIST_PRICES[name]
    best = None
    for known in LIST_PRICES:
        if name.startswith(known + "-") or name.startswith(known + "."):
            if best is None or len(known) > len(best):
                best = known
    return LIST_PRICES[best] if best else None


def _custom_is_local() -> bool:
    """A self-hosted OpenAI-compatible server on this machine or a private network (vLLM, LM Studio): no per-token bill."""
    try:
        from app.services.llm_providers import context
        url = context.get("LLM_CUSTOM_BASE_URL")
    except Exception:
        return False
    host = (urlparse(url).hostname or "") if url else ""
    if not host:
        return False
    if host in ("localhost", "host.docker.internal") or host.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _overrides() -> Dict[str, Tuple[float, float]]:
    try:
        from app.core.config import get_settings
        raw = get_settings().model_prices or {}
    except Exception:
        return {}
    out = {}
    for k, v in raw.items():
        try:
            out[str(k).strip().lower()] = (float(v[0]), float(v[1]) if len(v) > 1 else 0.0)
        except (TypeError, ValueError, IndexError):
            continue
    return out


def price(spec: str) -> Optional[Tuple[float, float]]:
    """(input, output) USD per 1M tokens for a model spec, (0, 0) for a model on your own hardware, None when unknown."""
    provider, model = _split(spec)
    over = _overrides()
    for key in ((spec or "").strip().lower(), model.strip().lower(), f"{provider}:*"):
        if key in over:
            return over[key]
    if provider in _LOCAL_PROVIDERS or (provider == "custom" and _custom_is_local()):
        return (0.0, 0.0)
    return _list_price(model)


def cost(spec: str, prompt_tokens: int, completion_tokens: int) -> Optional[float]:
    """Estimated USD for these tokens, or None when the model's price is unknown."""
    p = price(spec)
    if p is None:
        return None
    return round((prompt_tokens * p[0] + completion_tokens * p[1]) / 1_000_000, 6)
