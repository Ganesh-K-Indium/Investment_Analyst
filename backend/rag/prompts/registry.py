"""
User-editable prompts.

A prompt is registered under a name. Where the pipeline uses it, `apply(name, default)` returns the user's override from their
settings (prompts.overrides) when there is one, else the built-in text. The UI lists the registry, shows each default, and saves
overrides through /settings. Only the prompts below can be overridden: the structured-output prompts that code parses (grader,
sub-query planner) are deliberately not editable, because a changed format would break the pipeline.
"""
from typing import Any, Dict, List

from app.services import app_settings

# name -> (label, what it controls)
EDITABLE: Dict[str, Dict[str, str]] = {
    "generator.system": {"label": "Answer writer: system prompt", "help": "The role, source rules and arithmetic rules the answer writer follows for every question."},
    "macro.planner": {"label": "Macro: query planner", "help": "How a macroeconomic question is turned into a plan (indicator, period, comparison)."},
    "macro.synthesis": {"label": "Macro: answer writer", "help": "How calculated macro figures are written up."},
}

STYLE_SUFFIX = {
    "concise": "\n\n**ANSWER LENGTH:** Be concise: lead with the answer, then at most a short table or three bullets of support. No background the question did not ask for.",
    "standard": "",
    "deep": "\n\n**ANSWER DEPTH:** Give a thorough analysis: the figures, how they changed and why (cite the filing's own explanation), the risks and caveats in the documents, and what it means for an investor.",
}


def apply(name: str, default: str) -> str:
    if name not in EDITABLE:
        raise KeyError(f"prompt {name!r} is not editable")
    override = (app_settings.get("prompts", "overrides", {}) or {}).get(name)
    return override.strip() if isinstance(override, str) and override.strip() else default


def style_suffix() -> str:
    return STYLE_SUFFIX.get(app_settings.get("answers", "style", "standard"), "")


def listing() -> List[Dict[str, Any]]:
    """Every editable prompt with its built-in text, for the prompt editor."""
    from rag.prompts import prompts as p
    defaults = {"generator.system": p._generator_base_prompt(p._current_year()), "macro.planner": p.MACRO_PLANNER_SYSTEM_PROMPT, "macro.synthesis": p.MACRO_SYNTHESIS_PROMPT}
    return [{"name": n, **meta, "default": defaults[n]} for n, meta in EDITABLE.items()]
