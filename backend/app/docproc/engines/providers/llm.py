"""
One way to call a language or vision model, whoever hosts it.

A model is named by a "spec": `provider:model`, for example `openai:gpt-4.1`, `anthropic:claude-sonnet-5-5`,
`gemini:gemini-2.5-flash`, `ollama:qwen2.5vl:7b`, `custom:my-model`. A bare name (`gpt-4.1`) means OpenAI, which keeps every
older setting working.

Providers
    openai     OPENAI_API_KEY (and OPENAI_BASE_URL for a proxy or Azure-style gateway)
    anthropic  ANTHROPIC_API_KEY
    gemini     GEMINI_API_KEY or GOOGLE_API_KEY
    ollama     OLLAMA_BASE_URL (e.g. http://localhost:11434): models that run on your own hardware
    custom     LLM_CUSTOM_BASE_URL (+ LLM_CUSTOM_API_KEY): any OpenAI-compatible server (vLLM, LM Studio, a private gateway)

LOCAL_ONLY=1 switches the cloud providers off entirely, so nothing can leave the machine, whatever a workspace asks for.

Every call goes through `complete()`, which records token use for the Documents page and returns the model's text. Callers
keep their own prompts and parsers; this module only moves the request and the reply.
"""
import base64
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.docproc import config
from app.utils.usage_tracker import record_usage

logger = logging.getLogger("app.docproc.engines.providers.llm")

TIMEOUT_SECONDS = 120
MAX_REPLY_TOKENS = 8192


class LLMError(Exception):
    """A model call failed. `message` is safe to show; details are in the log."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    cloud: bool                       # data leaves this machine
    env: Tuple[str, ...]              # variables that configure it (any one of the first group is enough)
    hint: str
    models: Tuple[str, ...] = ()      # suggestions for the settings screen; any model name the provider serves also works
    defaults: Tuple[str, ...] = ()    # primary reader, second reader when nothing else is chosen


PROVIDERS: Dict[str, Provider] = {p.id: p for p in (
    Provider("openai", "OpenAI", True, ("OPENAI_API_KEY",), "Add an OpenAI API key in Settings (or set OPENAI_API_KEY).",
             ("gpt-4.1", "gpt-4.1-mini", "gpt-5", "gpt-5-mini"), ("gpt-4.1", "gpt-4.1-mini")),
    Provider("anthropic", "Anthropic Claude", True, ("ANTHROPIC_API_KEY",), "Add an Anthropic API key in Settings (or set ANTHROPIC_API_KEY).",
             ("claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"), ("anthropic:claude-sonnet-5-5", "anthropic:claude-haiku-5-5")),
    Provider("gemini", "Google Gemini", True, ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "Add a Gemini API key in Settings (or set GEMINI_API_KEY).",
             ("gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"), ("gemini:gemini-2.5-flash", "gemini:gemini-2.5-flash-lite")),
    Provider("ollama", "Ollama (your own hardware)", False, ("OLLAMA_BASE_URL",), "Run Ollama, then add its address in Settings (or set OLLAMA_BASE_URL).",
             ("qwen2.5vl:7b", "llama3.2-vision", "minicpm-v"), ("ollama:qwen2.5vl:7b",)),
    Provider("custom", "Self-hosted, OpenAI-compatible", False, ("LLM_CUSTOM_BASE_URL",),
             "Add the server address in Settings (or set LLM_CUSTOM_BASE_URL). Name models as custom:<model>.", (), ()),
)}


def split(spec: str) -> Tuple[str, str]:
    """`provider:model` -> (provider, model). Only the first colon separates, because model names may contain one."""
    spec = (spec or "").strip()
    head, sep, rest = spec.partition(":")
    if sep and head.lower() in PROVIDERS:
        return head.lower(), rest.strip()          # "anthropic:" has no model: valid_spec rejects it rather than calling OpenAI
    return "openai", spec


def valid_spec(spec: Any) -> bool:
    return isinstance(spec, str) and bool(split(spec)[1]) and len(spec) <= 120 and not re.search(r"\s", spec)


def _env(name: str) -> str:
    """A provider setting: saved in the app's Settings screen, else the server's environment (engines/credentials.py)."""
    from app.docproc.engines.providers import credentials
    return credentials.get(name)


def provider_ready(provider_id: str) -> bool:
    p = PROVIDERS.get(provider_id)
    if p is None:
        return False
    if p.cloud and config.local_only():
        return False
    return any(_env(v) for v in p.env)


def spec_ready(spec: str) -> bool:
    return provider_ready(split(spec)[0])


def any_ready() -> bool:
    return any(provider_ready(p) for p in PROVIDERS)


def default_models() -> List[str]:
    """The two readers to use when nobody chose: the first configured provider's pair, so a server with only an Anthropic
    key works without further setup. With nothing configured the OpenAI pair is returned (calls then fail loudly, not silently)."""
    for pid in ("openai", "anthropic", "gemini", "ollama", "custom"):
        if provider_ready(pid):
            if pid == "custom":
                m = _env("LLM_CUSTOM_MODEL")
                return [f"custom:{m}"] if m else []
            return list(PROVIDERS[pid].defaults)
    return list(PROVIDERS["openai"].defaults)


def providers_status() -> List[Dict[str, Any]]:
    out = []
    for p in PROVIDERS.values():
        blocked = p.cloud and config.local_only()
        out.append({"id": p.id, "label": p.label, "cloud": p.cloud, "ready": provider_ready(p.id),
                    "status": "blocked" if blocked else "ready" if provider_ready(p.id) else "needs_setup",
                    "hint": "Cloud models are switched off (LOCAL_ONLY)." if blocked else p.hint,
                    "models": [m if p.id == "openai" else f"{p.id}:{m}" for m in p.models]})
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Reply cleanup
# ---------------------------------------------------------------------------------------------------------------------

def clean_json(text: str) -> str:
    """Some providers wrap JSON in code fences or add a sentence around it. Return just the JSON text when it is there."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t).strip()
    if t.startswith(("{", "[")):
        return t
    a, b = t.find("{"), t.rfind("}")
    return t[a:b + 1] if 0 <= a < b else t


# ---------------------------------------------------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Reply:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


_openai_clients: Dict[Tuple[str, str], Any] = {}


def _openai_client(base_url: Optional[str], api_key: Optional[str]):
    key = (base_url or "", api_key or "")
    if key not in _openai_clients:
        from openai import AsyncOpenAI
        kwargs: Dict[str, Any] = {"timeout": TIMEOUT_SECONDS}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key
        _openai_clients[key] = AsyncOpenAI(**kwargs)
    return _openai_clients[key]


def _image_b64(jpeg: bytes) -> str:
    return base64.b64encode(jpeg).decode()


async def _openai_style(provider: str, model: str, prompt: str, jpeg: Optional[bytes], json_mode: bool) -> Reply:
    if provider == "ollama":
        client = _openai_client(_env("OLLAMA_BASE_URL").rstrip("/") + "/v1", "ollama")
    elif provider == "custom":
        client = _openai_client(_env("LLM_CUSTOM_BASE_URL"), _env("LLM_CUSTOM_API_KEY") or "none")
    else:
        client = _openai_client(_env("OPENAI_BASE_URL") or None, _env("OPENAI_API_KEY") or None)
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    if jpeg is not None:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_image_b64(jpeg)}", "detail": "high"}})
    kwargs: Dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": content}]}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if provider == "openai" and model.startswith(("gpt-5", "o")):
        kwargs["reasoning_effort"] = "low"
    else:
        kwargs["temperature"] = 0
    try:
        resp = await client.chat.completions.create(**kwargs)
    except Exception as e:
        # Self-hosted servers differ in what they accept. Drop the optional JSON switch once and try again.
        if provider in ("ollama", "custom") and "response_format" in kwargs and getattr(e, "status_code", None) == 400:
            kwargs.pop("response_format")
            resp = await client.chat.completions.create(**kwargs)
        else:
            raise
    usage = getattr(resp, "usage", None)
    return Reply(resp.choices[0].message.content or "", getattr(usage, "prompt_tokens", 0) or 0,
                 getattr(usage, "completion_tokens", 0) or 0, getattr(usage, "total_tokens", 0) or 0)


async def _post(url: str, headers: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
    import httpx
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
        r = await http.post(url, headers=headers, json=body)
    if r.status_code >= 400:
        detail = ""
        try:
            err = r.json().get("error", {})
            detail = err.get("message", "") if isinstance(err, dict) else str(err)
        except ValueError:
            detail = r.text[:200]
        raise LLMError(f"The model service returned an error ({r.status_code}). {detail[:200]}".strip(), r.status_code)
    return r.json()


async def _anthropic(model: str, prompt: str, jpeg: Optional[bytes], json_mode: bool) -> Reply:
    content: List[Dict[str, Any]] = []
    if jpeg is not None:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _image_b64(jpeg)}})
    content.append({"type": "text", "text": prompt + ("\n\nReply with the JSON object only, no other text." if json_mode else "")})
    body: Dict[str, Any] = {"model": model, "max_tokens": MAX_REPLY_TOKENS, "temperature": 0, "messages": [{"role": "user", "content": content}]}
    headers = {"x-api-key": _env("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01", "content-type": "application/json"}
    try:
        data = await _post("https://api.anthropic.com/v1/messages", headers, body)
    except LLMError as e:
        if e.status == 400 and "temperature" in str(e).lower():   # some newer models fix the temperature themselves
            body.pop("temperature")
            data = await _post("https://api.anthropic.com/v1/messages", headers, body)
        else:
            raise
    text = "".join(b.get("text", "") for b in data.get("content", []) if isinstance(b, dict) and b.get("type") == "text")
    u = data.get("usage") or {}
    pt, ct = int(u.get("input_tokens", 0) or 0), int(u.get("output_tokens", 0) or 0)
    return Reply(text, pt, ct, pt + ct)


async def _gemini(model: str, prompt: str, jpeg: Optional[bytes], json_mode: bool) -> Reply:
    parts: List[Dict[str, Any]] = []
    if jpeg is not None:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": _image_b64(jpeg)}})
    parts.append({"text": prompt})
    gen: Dict[str, Any] = {"temperature": 0, "maxOutputTokens": MAX_REPLY_TOKENS}
    if json_mode:
        gen["responseMimeType"] = "application/json"
    key = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
    data = await _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                       {"x-goog-api-key": key, "content-type": "application/json"},
                       {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen})
    cands = data.get("candidates") or []
    text = "".join(p.get("text", "") for p in ((cands[0].get("content") or {}).get("parts") or []) if isinstance(p, dict)) if cands else ""
    u = data.get("usageMetadata") or {}
    pt, ct = int(u.get("promptTokenCount", 0) or 0), int(u.get("candidatesTokenCount", 0) or 0)
    return Reply(text, pt, ct, int(u.get("totalTokenCount", 0) or 0) or pt + ct)


async def complete(spec: str, prompt: str, jpeg: Optional[bytes] = None, *, stage: str, json_mode: bool = True) -> str:
    """Ask one model one question, with an optional page image. Returns the reply text (JSON text when `json_mode`)."""
    provider, model = split(spec)
    if provider not in PROVIDERS:
        raise LLMError("That model provider is not supported.", 400)
    if not provider_ready(provider):
        why = "cloud models are switched off on this server (LOCAL_ONLY)" if PROVIDERS[provider].cloud and config.local_only() else PROVIDERS[provider].hint
        raise LLMError(f"{PROVIDERS[provider].label} is not available: {why}", 503)
    start = time.perf_counter()
    if provider == "anthropic":
        reply = await _anthropic(model, prompt, jpeg, json_mode)
    elif provider == "gemini":
        reply = await _gemini(model, prompt, jpeg, json_mode)
    else:
        reply = await _openai_style(provider, model, prompt, jpeg, json_mode)
    record_usage(stage=stage, model=spec, prompt_tokens=reply.prompt_tokens, completion_tokens=reply.completion_tokens,
                 total_tokens=reply.total_tokens, duration_seconds=time.perf_counter() - start)
    return clean_json(reply.text) if json_mode else reply.text
