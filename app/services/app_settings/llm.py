"""One place that builds every chat model, so the model, temperature and timeout of each job come from the user's settings."""
import logging
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI

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


def get_llm(role: str, **overrides: Any) -> ChatOpenAI:
    """A chat model for `role` using the settings in force. `overrides` (temperature=, timeout=, model=) win for that one call site."""
    m = resolver.current().get("models", {})
    kwargs = {"model": model_for(role), "temperature": m.get("temperature", 0.0), "timeout": m.get("request_timeout", 60), "max_retries": m.get("max_retries", 2)}
    # roles whose output is structured/planning stay deterministic whatever the answer temperature is
    if role in ("router", "grader", "verifier", "rewriter", "macro_planner"):
        kwargs["temperature"] = 0
    track = overrides.pop("track", True)  # False where the call site already records its own usage (ALPHA dimensions)
    kwargs.update(overrides)
    return ChatOpenAI(callbacks=[UsageCallback(role, kwargs["model"])] if track else None, **kwargs)
