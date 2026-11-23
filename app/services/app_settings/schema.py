"""
The single description of every user-configurable option.

One declaration drives four things: the defaults, the validation of what a user saves, the JSON schema the UI renders its
settings forms from (GET /settings/schema), and the documentation. Add an option here and it appears everywhere.

Precedence when settings are resolved (last wins):
    SYSTEM_DEFAULTS -> system overrides (admin) -> user -> portfolio -> per-request override
"""
import copy
from typing import Any, Dict, List, Tuple

# gpt-4-series only (cost rule). Admins can extend the list through the "system" scope.
ALLOWED_MODELS = ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4-turbo"]

MODEL_ROLES: Dict[str, Tuple[str, str]] = {
    # role: (label, default model)
    "router": ("Query router and sub-query planner", "gpt-4o-mini"),
    "grader": ("Retrieval grader", "gpt-4o"),
    "generator": ("Answer generator", "gpt-4o"),
    "verifier": ("Answer verifier", "gpt-4o-mini"),
    "rewriter": ("Query rewriter", "gpt-4o-mini"),
    "analysis": ("ALPHA / scenario reports", "gpt-4o"),
    "macro_planner": ("Macro query planner", "gpt-4o-mini"),
    "macro_writer": ("Macro answer writer", "gpt-4o"),
    "summarizer": ("Chat summaries", "gpt-4o-mini"),
    "quant_supervisor": ("Quant supervisor", "gpt-4o"),
    "quant_agent": ("Quant sub-agents", "gpt-4o"),
}

QUANT_AGENTS = ["ticker_finder", "stock_information", "technical_analysis", "research", "options_intelligence", "postgres", "documents"]
BUILTIN_MODULES = ["alpha", "scenario", "macro"]
SOURCES = ["filings", "uploads", "notes", "web", "macro"]

# Field spec keys: type (bool|int|float|enum|text|list|model|json), default, label, help, min, max, options
def _f(type_, default, label, help_="", **kw) -> Dict[str, Any]:
    return {"type": type_, "default": default, "label": label, "help": help_, **kw}


SECTIONS: Dict[str, Dict[str, Any]] = {
    "models": {
        "label": "Models and cost",
        "help": "Which model does each job. Cheaper models for routing and rewriting, stronger ones for the final answer.",
        "fields": {
            **{f"{r}_model": _f("model", d, label, "", options=ALLOWED_MODELS) for r, (label, d) in MODEL_ROLES.items()},
            "temperature": _f("float", 0.0, "Answer temperature", "0 gives the same answer to the same question.", min=0.0, max=1.0),
            "request_timeout": _f("int", 60, "Model timeout (seconds)", "", min=10, max=300),
            "max_retries": _f("int", 2, "Model retries", "", min=0, max=5),
            "monthly_token_budget": _f("int", 0, "Monthly token budget", "0 = no limit. Warns in the UI when exceeded.", min=0, max=1_000_000_000),
        },
    },
    "retrieval": {
        "label": "Retrieval",
        "help": "How evidence is found, ranked and checked before an answer is written.",
        "fields": {
            "top_k": _f("int", 12, "Chunks per search", "", min=3, max=50),
            "sources": _f("list", ["filings", "uploads", "notes", "web", "macro"], "Sources to search", "", options=SOURCES),
            "rerank_enabled": _f("bool", True, "Re-rank results", "A cross-encoder re-orders the candidates; more accurate, slightly slower."),
            "rerank_provider": _f("enum", "local", "Re-ranker", "local = on this machine; cohere = hosted (sends text out).", options=["local", "cohere", "off"]),
            "rerank_top_n": _f("int", 8, "Keep after re-ranking", "", min=2, max=30),
            "corrective_retries": _f("int", 1, "Corrective retrieval retries", "When the evidence is weak, rewrite the query and search again this many times before using the web.", min=0, max=3),
            "web_fallback": _f("bool", True, "Web search fallback", "Use the web when the documents do not answer the question."),
            "contextual_prefix": _f("bool", True, "Contextual chunk prefix", "Prefix each chunk with its document, period and section when ingesting."),
            "context_token_budget": _f("int", 60000, "Context budget (tokens)", "Chunks past this are dropped, never cut.", min=4000, max=200000),
            "filing_types": _f("list", [], "Restrict to filing types", "Empty = any.", options=[]),
            "year_from": _f("int", 0, "Earliest fiscal year", "0 = no limit.", min=0, max=2100),
        },
    },
    "answers": {
        "label": "Answers",
        "help": "How answers are written and checked.",
        "fields": {
            "verify_mode": _f("enum", "warn", "Verify against evidence", "off = no check; warn = list figures not found in the documents under the answer; strict = lead with a prominent warning and mark the answer unverified.", options=["off", "warn", "strict"]),
            "style": _f("enum", "standard", "Answer style", "", options=["concise", "standard", "deep"]),
            "citations": _f("bool", True, "Show citations", ""),
            "streaming": _f("bool", False, "Stream answers as they are written", "Shows progress and text live. Streamed questions are answered directly instead of through the background queue, so they do not appear in the analysis-run history."),
            "clarify_ambiguous": _f("bool", False, "Ask when the question is ambiguous", "Pause and ask which company, period or document you mean."),
            "units_check": _f("bool", True, "Check units and scale", "Flag answers mixing thousands and millions, or currencies."),
        },
    },
    "modules": {
        "label": "Analysis modules",
        "help": "Built-in frameworks and your own. A module is a way of analysing a company: when it triggers, what it retrieves, how it reports.",
        "fields": {
            "enabled": _f("list", list(BUILTIN_MODULES), "Enabled built-in modules", "", options=BUILTIN_MODULES),
            "custom": _f("json", [], "Custom frameworks", "Managed from the Frameworks page."),
        },
    },
    "prompts": {
        "label": "Prompts",
        "help": "Override a built-in prompt. Empty = use the default.",
        "fields": {"overrides": _f("json", {}, "Prompt overrides", "Managed from the Prompts page.")},
    },
    "documents": {
        "label": "Your documents",
        "help": "How uploaded files (scans, statements, broker notes, spreadsheets) are read and indexed.",
        "fields": {
            "reading_strategy": _f("enum", "balanced", "Reading strategy", "economy = local OCR only (private, free); balanced = local first, a vision model for doubtful pages; accuracy = two vision models on every scan.", options=["economy", "balanced", "accuracy"]),
            "local_only": _f("bool", False, "Never send documents to a cloud model", "Forces local OCR only."),
            "escalate_below": _f("float", 0.85, "Escalate when OCR confidence is below", "", min=0.3, max=0.99),
            "token_cap": _f("int", 0, "Token cap per document", "0 = no cap.", min=0, max=5_000_000),
            "index_policy": _f("enum", "after_review", "Index low-confidence text", "after_review = only once a person confirms it (recommended); flagged = index now, marked low confidence; never = keep out of search.", options=["after_review", "flagged", "never"]),
            "auto_detect_type": _f("bool", True, "Detect document type automatically", ""),
            "max_upload_mb": _f("int", 50, "Largest file (MB)", "", min=1, max=500),
            "max_pages": _f("int", 200, "Most pages per file", "", min=1, max=2000),
            "default_visibility": _f("enum", "private", "Who can search your uploads", "", options=["private", "workspace"]),
        },
    },
    "quant": {
        "label": "Quant agents",
        "help": "The market-data agents: which are on, and how they behave.",
        "fields": {
            "enabled_agents": _f("list", list(QUANT_AGENTS), "Enabled agents", "", options=QUANT_AGENTS),
            "history_window": _f("enum", "1y", "Default price history", "", options=["1mo", "3mo", "6mo", "1y", "2y", "5y", "max"]),
            "benchmark": _f("text", "SPY", "Default benchmark ticker", ""),
            "risk_profile": _f("enum", "balanced", "Risk profile", "Shapes how the options and research agents word their conclusions.", options=["conservative", "balanced", "aggressive"]),
            "tool_timeout": _f("int", 45, "Tool timeout (seconds)", "", min=5, max=300),
            "cache_ttl": _f("int", 120, "Market data cache (seconds)", "0 = no cache.", min=0, max=3600),
            "recursion_limit": _f("int", 50, "Most agent steps", "", min=10, max=200),
        },
    },
    "ui": {
        "label": "Appearance and workflow",
        "help": "",
        "fields": {
            "theme": _f("enum", "system", "Theme", "", options=["system", "light", "dark"]),
            "density": _f("enum", "comfortable", "Density", "", options=["comfortable", "compact"]),
            "default_agent": _f("enum", "rag", "Open with", "", options=["rag", "quant"]),
            "show_node_progress": _f("bool", True, "Show analysis progress steps", ""),
            "show_cost": _f("bool", False, "Show tokens and cost per answer", ""),
        },
    },
}

SYSTEM_DEFAULTS: Dict[str, Dict[str, Any]] = {s: {k: copy.deepcopy(f["default"]) for k, f in sec["fields"].items()} for s, sec in SECTIONS.items()}

_CUSTOM_FRAMEWORK_KEYS = {"id", "name", "description", "triggers", "sections", "sources", "prompt", "enabled"}
MAX_CUSTOM_FRAMEWORKS = 20
MAX_PROMPT_CHARS = 20000


class SettingsError(ValueError):
    pass


def _coerce(spec: Dict[str, Any], value: Any, allowed_models: List[str], path: str) -> Any:
    t = spec["type"]
    if t == "bool":
        if not isinstance(value, bool):
            raise SettingsError(f"{path}: expected true or false")
        return value
    if t in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsError(f"{path}: expected a number")
        v = int(value) if t == "int" else float(value)
        if "min" in spec and v < spec["min"] or "max" in spec and v > spec["max"]:
            raise SettingsError(f"{path}: must be between {spec.get('min')} and {spec.get('max')}")
        return v
    if t == "model":
        if value not in allowed_models:
            raise SettingsError(f"{path}: model {value!r} is not allowed (choose one of {', '.join(allowed_models)})")
        return value
    if t == "enum":
        if value not in spec["options"]:
            raise SettingsError(f"{path}: must be one of {', '.join(spec['options'])}")
        return value
    if t == "text":
        if not isinstance(value, str) or len(value) > 200:
            raise SettingsError(f"{path}: expected short text")
        return value.strip()
    if t == "list":
        if not isinstance(value, list) or not all(isinstance(x, (str, int)) for x in value):
            raise SettingsError(f"{path}: expected a list")
        if spec.get("options") and any(x not in spec["options"] for x in value):
            raise SettingsError(f"{path}: allowed values are {', '.join(spec['options'])}")
        return list(dict.fromkeys(value))
    if t == "json":
        return value
    raise SettingsError(f"{path}: unknown type")


def _check_custom(section: str, key: str, value: Any, path: str) -> Any:
    if section == "modules" and key == "custom":
        if not isinstance(value, list) or len(value) > MAX_CUSTOM_FRAMEWORKS:
            raise SettingsError(f"{path}: expected a list of at most {MAX_CUSTOM_FRAMEWORKS} frameworks")
        seen = set()
        for i, fw in enumerate(value):
            if not isinstance(fw, dict) or not isinstance(fw.get("id"), str) or not fw["id"].strip() or not isinstance(fw.get("name"), str):
                raise SettingsError(f"{path}[{i}]: each framework needs an id and a name")
            if fw["id"] in seen or fw["id"] in BUILTIN_MODULES:
                raise SettingsError(f"{path}[{i}]: id {fw['id']!r} is already used")
            seen.add(fw["id"])
            extra = set(fw) - _CUSTOM_FRAMEWORK_KEYS
            if extra:
                raise SettingsError(f"{path}[{i}]: unknown keys {sorted(extra)}")
            for lk in ("triggers", "sections", "sources"):
                if lk in fw and not (isinstance(fw[lk], list) and all(isinstance(x, str) for x in fw[lk])):
                    raise SettingsError(f"{path}[{i}].{lk}: expected a list of text")
            if len(str(fw.get("prompt", ""))) > MAX_PROMPT_CHARS:
                raise SettingsError(f"{path}[{i}].prompt: too long")
    if section == "prompts" and key == "overrides":
        if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) or len(v) > MAX_PROMPT_CHARS for k, v in value.items()):
            raise SettingsError(f"{path}: expected {{prompt name: text}}")
    return value


def validate(patch: Dict[str, Any], allowed_models: List[str] = None) -> Dict[str, Dict[str, Any]]:
    """Check a (partial) settings object against the schema and return it cleaned. Unknown sections or keys are errors,
    never silently dropped, so a typo cannot look like a saved setting."""
    allowed_models = allowed_models or ALLOWED_MODELS
    if not isinstance(patch, dict):
        raise SettingsError("settings must be an object")
    out: Dict[str, Dict[str, Any]] = {}
    for section, values in patch.items():
        if section not in SECTIONS:
            raise SettingsError(f"unknown section {section!r}")
        if not isinstance(values, dict):
            raise SettingsError(f"{section}: expected an object")
        out[section] = {}
        for key, value in values.items():
            spec = SECTIONS[section]["fields"].get(key)
            if spec is None:
                raise SettingsError(f"{section}.{key}: unknown setting")
            path = f"{section}.{key}"
            out[section][key] = _check_custom(section, key, _coerce(spec, value, allowed_models, path), path)
    return out


def ui_schema(allowed_models: List[str] = None) -> Dict[str, Any]:
    """What the settings page renders its forms from."""
    allowed_models = allowed_models or ALLOWED_MODELS
    sections = []
    for sid, sec in SECTIONS.items():
        fields = []
        for key, f in sec["fields"].items():
            item = {"key": key, **{k: v for k, v in f.items()}}
            if f["type"] == "model":
                item["options"] = allowed_models
            fields.append(item)
        sections.append({"id": sid, "label": sec["label"], "help": sec["help"], "fields": fields})
    return {"sections": sections, "defaults": SYSTEM_DEFAULTS, "model_roles": {r: {"label": l, "default": d} for r, (l, d) in MODEL_ROLES.items()}}
