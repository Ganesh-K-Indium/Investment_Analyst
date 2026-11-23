"""One place that builds every chat model, so the model, temperature and timeout of each job come from the user's settings."""
import logging
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from app.utils.usage_tracker import record_usage

from . import resolver
from .schema import MODEL_ROLES

logger = logging.getLogger("app.settings.llm")


class UsageCallback(BaseCallbackHandler):
    """Reports every call's tokens to the active usage tracker, labelled with the role that made it."""

    def __init__(self, role: str, model: str):
        self.role, self.model = role, model

    def on_llm_end(self, response, **kwargs):  # noqa: ANN001
        try:
            usage = (response.llm_output or {}).get("token_usage") or {}
            p, c = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
            if not (p or c):
                for gens in response.generations:
                    for g in gens:
                        um = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                        p, c = p + um.get("input_tokens", 0), c + um.get("output_tokens", 0)
            if p or c:
                record_usage(self.role, self.model, prompt_tokens=p, completion_tokens=c)
        except Exception:  # accounting must never break an answer
            logger.debug("usage callback failed", exc_info=True)


def model_for(role: str) -> str:
    if role not in MODEL_ROLES:
        raise KeyError(f"unknown model role {role!r}")
    return resolver.get("models", f"{role}_model", MODEL_ROLES[role][1])


def valid_spec_for_effective(spec) -> bool:
    from app.services.llm_providers.catalog import valid_spec
    return valid_spec(spec)


def get_llm(role: str, **overrides: Any):
    """A chat model for `role` using the settings in force: any connected provider (OpenAI, Anthropic, Gemini, Ollama, self-hosted).
    `overrides` (temperature=, timeout=, model=) win for that one call site. Raises ProviderNotConfigured with a message that is safe
    to show when the job's provider has no key."""
    from app.services.llm_providers import factory
    m = resolver.current().get("models", {})
    spec = overrides.pop("model", None) or model_for(role)
    temperature = m.get("temperature", 0.0)
    # roles whose output is structured/planning stay deterministic whatever the answer temperature is
    if role in ("router", "grader", "verifier", "rewriter", "macro_planner"):
        temperature = 0
    temperature = overrides.pop("temperature", temperature)
    track = overrides.pop("track", True)  # False where the call site already records its own usage (ALPHA dimensions)
    return factory.build(spec, temperature=temperature, timeout=overrides.pop("timeout", m.get("request_timeout", 60)),
                         max_retries=overrides.pop("max_retries", m.get("max_retries", 2)),
                         callbacks=[UsageCallback(role, spec)] if track else None, **overrides)
