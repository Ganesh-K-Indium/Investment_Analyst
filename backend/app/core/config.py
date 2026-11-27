"""
Deployment configuration: the one place every server-side setting is declared, typed, documented and read.

Two kinds of settings
---------------------
1. **Deployment settings** (this module): secrets, service URLs, pools, limits, feature switches. Set by whoever runs the
   server; never changed from the UI.
2. **Product settings** (`app/services/app_settings/schema.py`): models, retrieval, answers, documents, quant, UI. Changed
   from the Settings screens. This module also supplies their *deployment defaults* (see "Defaults for the Settings screen").

Where a deployment setting comes from (first match wins)
--------------------------------------------------------
    1. a real environment variable                  e.g.  export DB_POOL_SIZE=20
    2. the `.env` file at the repo root             DB_POOL_SIZE=20
    3. the YAML config file                         db_pool_size: 20      (config.yaml, or the file APP_CONFIG_FILE names)
    4. the default declared below

Every field's name is its environment variable (case-insensitive) and its key in the YAML file, so one name works
everywhere. See `.env.example` and `config.example.yaml`.

Defaults for the Settings screen
--------------------------------
Product settings resolve in layers (last wins):

    code defaults (schema.py) -> deployment defaults (here) -> admin (UI) -> user (UI) -> portfolio (UI) -> per request

Deployment defaults come from, in order: the `settings_defaults:` block of the YAML file, then `SETTINGS__<SECTION>__<KEY>`
environment variables (e.g. `SETTINGS__MODELS__GENERATOR_MODEL=gpt-4o`), plus `MAX_UPLOAD_MB` / `MAX_PAGES` for the two
document limits. They are validated against the schema at startup, so a typo fails fast instead of being ignored.

Usage
-----
    from app.core.config import get_settings
    cfg = get_settings()
    cfg.database_url, cfg.db_pool_size, cfg.is_production

`get_settings()` is cached, and rebuilt automatically when the environment or the YAML file changes (tests, reloads), so
reading it in a function always reflects what is set now. Run `python -m app.core.config` to print the effective
configuration (secrets masked) and where each value came from.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Type

import yaml
from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

logger = logging.getLogger("app.core.config")

APP_VERSION = "2.2.0"

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent

# The .env file is loaded into the process environment once, here, without overriding variables that are already set,
# so a real environment variable (Docker, systemd, a shell export) always wins over the file. Libraries that read
# os.environ themselves (OpenAI, LangChain, Tavily) see the same values.
ENV_FILE = REPO_ROOT / ".env"
load_dotenv(ENV_FILE, override=False)

_TRUE = ("1", "true", "yes", "on")


def _yaml_path() -> Path:
    return Path(os.getenv("APP_CONFIG_FILE") or REPO_ROOT / "config.yaml")


def _read_yaml() -> Dict[str, Any]:
    path = _yaml_path()
    if not path.is_file():
        if os.getenv("APP_CONFIG_FILE"):
            raise ConfigError(f"APP_CONFIG_FILE points at {path}, which does not exist.")
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping of setting names to values.")
    return data


class ConfigError(RuntimeError):
    """The configuration is invalid or incomplete for the environment it runs in."""


class _YamlSource(PydanticBaseSettingsSource):
    """Values from the YAML config file, keyed by field name (case-insensitive). `settings_defaults` is read separately."""

    def __init__(self, settings_cls: Type[BaseSettings]):
        super().__init__(settings_cls)
        raw = {str(k).lower(): v for k, v in _read_yaml().items() if str(k).lower() != "settings_defaults"}
        unknown = sorted(set(raw) - set(settings_cls.model_fields))
        if unknown:
            raise ConfigError(f"Unknown setting(s) in {_yaml_path()}: {', '.join(unknown)}. See config.example.yaml.")
        self._data = raw

    def get_field_value(self, field: FieldInfo, field_name: str) -> Tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def __call__(self) -> Dict[str, Any]:
        return {k: v for k, v in self._data.items() if v is not None}


class Settings(BaseSettings):
    """Every deployment setting. The field name is the environment variable (upper-cased) and the YAML key."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore", validate_default=True, env_ignore_empty=True)

    # ── Application ──────────────────────────────────────────────────────────
    app_env: Literal["development", "production", "test"] = Field("development", description="`production` turns on the startup checks for secrets and safe defaults.")
    log_level: str = Field("INFO", description="Root log level: DEBUG, INFO, WARNING, ERROR.")
    data_dir: Optional[Path] = Field(None, description="Where the app writes files (filings, uploads, keys, caches, logs). Default: <repo>/data.")
    save_debug_responses: bool = Field(False, description="Dump full agent responses to data/output/json for debugging.")

    # ── HTTP / CORS ──────────────────────────────────────────────────────────
    cors_allowed_origins: str = Field("http://localhost:5173,http://127.0.0.1:5173", description="Comma-separated web UI origins allowed to call the API.")
    cors_allowed_origin_regex: str = Field(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$", description="Extra origins as a regex (default: localhost on any port, for development). Set empty in production.")

    # ── Security ─────────────────────────────────────────────────────────────
    jwt_secret_key: str = Field("", description="Signs login tokens. Required in production; in development a random key is used per start.")
    access_token_expire_minutes: int = Field(30, ge=1, description="Lifetime of an access token.")
    refresh_token_expire_days: int = Field(7, ge=1, description="Lifetime of a refresh token.")
    app_secret_key: str = Field("", description="Encrypts the model-provider keys users save. Changing it makes saved keys unreadable.")
    integration_secret_key: str = Field("", description="Encrypts stored file/database connector credentials (a Fernet key).")
    mcp_shared_secret: str = Field("", description="Signs the per-request token the Postgres MCP tool verifies. Falls back to JWT_SECRET_KEY outside production.")
    lock_settings: bool = Field(False, description="Make the AI-provider screen read-only (keys managed through configuration only).")
    local_only: bool = Field(False, description="Nothing leaves this machine: cloud models and model downloads are off.")
    llm_enabled: bool = Field(True, description="Master switch for document-reading model calls.")
    llm_allow_private_urls: bool = Field(True, description="Allow model servers on localhost/private networks (Ollama, a private gateway). Set 0 on a shared server.")
    mcp_allow_private_urls: bool = Field(False, description="Allow users' MCP servers on localhost/private networks.")

    # ── PostgreSQL ───────────────────────────────────────────────────────────
    database_url: str = Field("postgresql://investment_analyst:investment_analyst@localhost:5432/investment_analyst", description="Application database (async SQLAlchemy + LangGraph checkpoints).")
    db_pool_size: int = Field(10, ge=1, description="Pooled connections kept per process (API and each worker).")
    db_max_overflow: int = Field(20, ge=0, description="Extra connections a process may open under load. Keep processes x (size + overflow) under Postgres max_connections.")
    db_pool_timeout: int = Field(30, ge=1, description="Seconds to wait for a free connection before failing.")
    db_pool_recycle: int = Field(1800, ge=60, description="Recycle connections older than this (seconds), so a firewall cannot silently cut them.")

    # ── Redis, workers, concurrency ──────────────────────────────────────────
    redis_url: str = Field("redis://localhost:6379/0", description="Job queues and live-update channels.")
    worker_interactive_max_jobs: int = Field(30, ge=1, description="Concurrent jobs per interactive worker (chats, quick runs).")
    worker_interactive_job_timeout: int = Field(1800, ge=60, description="Longest an interactive job may run (seconds).")
    worker_batch_max_jobs: int = Field(5, ge=1, description="Concurrent jobs per batch worker (multi-ticker ALPHA runs).")
    worker_batch_job_timeout: int = Field(7200, ge=60, description="Longest a batch job may run (seconds).")
    model_prices: Dict[str, List[float]] = Field(default_factory=dict, description="Your own model prices, USD per 1M tokens as [input, output], keyed by model spec or name, e.g. {\"custom:*\": [0, 0], \"anthropic:claude-sonnet-5-5\": [2, 10]}. Overrides the built-in list prices; a model with no known price is counted in tokens only.")
    analysis_stale_days: int = Field(30, ge=1, description="A holding whose last ALPHA run is older than this many days is flagged as stale on the dashboards.")
    analysis_insider_alert_usd: float = Field(5_000_000, ge=0, description="Net open-market insider buying or selling (USD) since the last analysis that raises a dashboard alert.")
    analysis_schedules_allowed: bool = Field(True, description="Let users set up scheduled or new-filing ALPHA re-runs. Each schedule starts switched off; nothing runs until its owner turns it on.")
    chat_stream_mode: Literal["auto", "worker", "inline"] = Field("auto", description="Where streamed chats run: auto = on a worker when one is up, else in the API.")
    chat_stream_timeout_seconds: int = Field(1800, ge=60, description="Longest a streamed chat may run on a worker.")
    threadpool_workers: int = Field(64, ge=4, description="Threads per process for blocking model calls and agent steps.")

    # ── Vector database ──────────────────────────────────────────────────────
    qdrant_url: str = Field("http://localhost:6333", description="Qdrant (self-hosted or Qdrant Cloud).")
    qdrant_api_key: str = Field("", description="Qdrant Cloud API key.")
    use_hybrid_search: bool = Field(True, description="Store BM25 sparse vectors next to dense ones at ingestion.")
    contextual_prefix: bool = Field(True, description="Embed each chunk with a company/filing/period/section header.")
    rerank_local_model: str = Field("Xenova/ms-marco-MiniLM-L-6-v2", description="Local cross-encoder re-ranker (downloaded on first use).")
    rerank_cohere_model: str = Field("rerank-english-v3.0", description="Cohere re-ranker, when a user picks the hosted re-ranker.")
    cohere_api_key: str = Field("", description="Cohere key for the hosted re-ranker.")

    # ── Model providers (server-wide fallbacks; users can save their own in Settings) ──
    openai_api_key: str = Field("", description="Embeddings for all search, filing captions, and the default chat models. Required.")
    openai_base_url: str = Field("", description="OpenAI-compatible endpoint instead of api.openai.com.")
    anthropic_api_key: str = Field("", description="Anthropic models.")
    gemini_api_key: str = Field("", description="Google Gemini models (falls back to GOOGLE_API_KEY).")
    google_api_key: str = Field("", description="Gemini vision for quant chart summaries.")
    ollama_base_url: str = Field("", description="Self-hosted Ollama server.")
    llm_custom_base_url: str = Field("", description="Any OpenAI-compatible server.")
    llm_custom_api_key: str = Field("", description="Key for LLM_CUSTOM_BASE_URL.")
    groq_api_key: str = Field("", description="Groq models.")

    # ── Market, web and macro data ───────────────────────────────────────────
    tavily_api_key: str = Field("", description="Web search (research agent, web fallback).")
    fred_api_key: str = Field("", description="FRED macro series. Required for the macro framework.")
    bls_api_key: str = Field("", description="BLS macro series (optional).")
    sec_user_agent: str = Field("Indium Capital contact@indium.com", description="Sent on every SEC EDGAR request. SEC requires your company name and a contact email.")

    # ── Chart hosting ────────────────────────────────────────────────────────
    cloudinary_cloud_name: str = Field("", description="Cloudinary chart hosting (optional; charts still work without it).")
    cloudinary_api_key: str = Field("", description="")
    cloudinary_api_secret: str = Field("", description="")

    # ── Quant MCP servers ────────────────────────────────────────────────────
    mcp_host: str = Field("127.0.0.1", description="Host of the built-in MCP servers. Per server: MCP_<NAME>_URL / _ENABLED / _BIND.")
    mcp_tool_threads: int = Field(32, ge=1, description="Threads per MCP server for blocking market-data calls.")
    # Per server: where it is (default http://<MCP_HOST>:<port>/mcp), whether agents use it, and the address it listens on.
    mcp_stock_info_url: str = Field("", description="Stock information server (port 8565).")
    mcp_stock_info_enabled: bool = True
    mcp_stock_info_bind: str = "127.0.0.1"
    mcp_technical_url: str = Field("", description="Technical analysis server (port 8566).")
    mcp_technical_enabled: bool = True
    mcp_technical_bind: str = "127.0.0.1"
    mcp_research_url: str = Field("", description="Research and ratings server (port 8567).")
    mcp_research_enabled: bool = True
    mcp_research_bind: str = "127.0.0.1"
    mcp_options_url: str = Field("", description="Options intelligence server (port 8568).")
    mcp_options_enabled: bool = True
    mcp_options_bind: str = "127.0.0.1"
    mcp_postgres_url: str = Field("", description="Postgres query server (port 8570).")
    mcp_postgres_enabled: bool = True
    mcp_postgres_bind: str = "127.0.0.1"

    # ── Documents and OCR ────────────────────────────────────────────────────
    docproc_data_dir: Optional[Path] = Field(None, description="Document engine state (uploads, keys, models). Default: <data_dir>/docproc.")
    docproc_models_dir: Optional[Path] = Field(None, description="Downloaded OCR models. Default: <docproc_data_dir>/models.")
    max_upload_mb: int = Field(50, ge=1, le=500, description="Deployment default for Settings > Your documents > Largest file.")
    max_pages: int = Field(200, ge=1, le=2000, description="Deployment default for Settings > Your documents > Most pages per file.")
    tesseract_cmd: str = Field("", description="Path to the tesseract binary when it is not on PATH (Windows default install folders are found automatically).")
    tesseract_lang: str = Field("eng", description="Tesseract language(s), e.g. eng+deu; each needs its language data installed (README: OCR engines).")
    docproc_deskew: bool = Field(True, description="Straighten scanned pages before OCR.")
    reading_strategy: Literal["economy", "balanced", "accuracy"] = Field("balanced", description="Default reading strategy for a document workspace.")
    local_ocr_engine: Literal["tesseract", "rapidocr", "paddleocr", "doctr"] = Field("tesseract", description="Default local OCR engine for a document workspace.")
    escalate_below: Optional[float] = Field(None, ge=0.3, le=0.99, description="OCR confidence below which a page goes to a vision model.")
    token_cap: int = Field(0, ge=0, description="Token cap per document (0 = none).")
    extraction_models: str = Field("", description="Comma-separated reader pair for structured extraction (default: best two available).")
    adjudication_model: str = Field("", description="Model that settles disagreements between the two readers.")
    adjudicate: bool = Field(True, description="Settle reader disagreements with a third model.")
    discover_long_doc_pages: int = Field(25, ge=1, description="Pages above which discovery samples instead of reading everything.")
    figure_read_max: int = Field(12, ge=0, description="Most figures read per document.")
    discover_digital_model: str = Field("auto", description="When a model reads digital pages with no template: auto (only pages code could not parse), never, always.")
    classify_unknown_streak: int = Field(3, ge=1, description="Consecutive unknown pages before classification stops trying.")
    handwriting_ocr_enabled: bool = Field(True, description="Read handwritten notes with a vision model.")
    handwriting_ocr_models: str = Field("", description="Comma-separated vision models for handwriting (default: built-in pair).")
    image_min_ocr_words: int = Field(3, ge=0, description="Images in filings with fewer OCR words are treated as decoration.")

    @field_validator("app_env", mode="before")
    @classmethod
    def _env_name(cls, v: Any) -> str:
        v = str(v or "development").strip().lower()
        return {"dev": "development", "local": "development", "prod": "production", "testing": "test"}.get(v, v)

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return (v or "INFO").upper()

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        # .env was already loaded into the environment (see ENV_FILE), so env_settings covers it with the right precedence.
        return init_settings, env_settings, _YamlSource(settings_cls)

    # ── Derived values ───────────────────────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def cors_origin_regex(self) -> Optional[str]:
        return self.cors_allowed_origin_regex or None

    def secret(self, name: str) -> str:
        """A key by its environment-variable name (e.g. "ANTHROPIC_API_KEY"): the declared field if there is one, else the
        raw environment. Used where the name is chosen at run time (model providers)."""
        field = name.lower()
        value = getattr(self, field, None) if field in type(self).model_fields else os.getenv(name)
        value = (value or "").strip() if isinstance(value, str) or value is None else str(value)
        if not value and name == "GEMINI_API_KEY":
            value = self.google_api_key.strip()
        return value

    def check(self) -> List[str]:
        """Problems with this configuration. In production these stop the server from starting; in development they are
        logged as warnings."""
        problems = []
        if not self.openai_api_key:
            problems.append("OPENAI_API_KEY is not set: search, embeddings and the default models will not work.")
        if self.is_production:
            for name in ("jwt_secret_key", "app_secret_key", "integration_secret_key", "mcp_shared_secret"):
                if not getattr(self, name):
                    problems.append(f"{name.upper()} must be set when APP_ENV=production.")
            if self.cors_allowed_origin_regex:
                problems.append("CORS_ALLOWED_ORIGIN_REGEX allows any localhost origin; set it empty in production and list origins in CORS_ALLOWED_ORIGINS.")
        return problems


# ── Cached access ────────────────────────────────────────────────────────────

_ENV_NAMES = tuple(sorted(n.upper() for n in Settings.model_fields)) + ("APP_CONFIG_FILE",)
_lock = threading.Lock()
_cache: Dict[str, Any] = {"key": None, "settings": None, "defaults": None}


def _fingerprint() -> Tuple[Any, ...]:
    """What the configuration depends on: the relevant environment variables and the YAML file's modification time."""
    path = _yaml_path()
    try:
        mtime = path.stat().st_mtime if path.is_file() else None
    except OSError:
        mtime = None
    env = tuple(os.environ.get(n) for n in _ENV_NAMES)
    overrides = tuple(sorted((k, v) for k, v in os.environ.items() if k.startswith("SETTINGS__")))
    return env, overrides, str(path), mtime


def get_settings() -> Settings:
    """The deployment settings in force now (cached; rebuilt when the environment or config file changes)."""
    key = _fingerprint()
    if _cache["key"] != key:
        with _lock:
            if _cache["key"] != key:
                _cache["settings"] = Settings()
                _cache["defaults"] = None
                _cache["key"] = key
    return _cache["settings"]


# ── Deployment defaults for the Settings screen ──────────────────────────────

def _parse_env_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def settings_defaults_layer() -> Dict[str, Dict[str, Any]]:
    """The deployment's defaults for product settings (validated), applied between the code defaults and the admin layer."""
    cfg = get_settings()
    cached = _cache.get("defaults")
    if cached is not None and _cache["settings"] is cfg:
        return cached
    from app.services.app_settings import schema

    layer: Dict[str, Dict[str, Any]] = {}
    if cfg.max_upload_mb != schema.SECTIONS["documents"]["fields"]["max_upload_mb"]["default"]:
        layer.setdefault("documents", {})["max_upload_mb"] = cfg.max_upload_mb
    if cfg.max_pages != schema.SECTIONS["documents"]["fields"]["max_pages"]["default"]:
        layer.setdefault("documents", {})["max_pages"] = cfg.max_pages
    from_file = _read_yaml().get("settings_defaults") or {}
    if not isinstance(from_file, dict):
        raise ConfigError("settings_defaults in the config file must be a mapping of section -> {key: value}.")
    for section, values in from_file.items():
        layer.setdefault(str(section), {}).update(values or {})
    for name, raw in os.environ.items():
        if name.startswith("SETTINGS__"):
            parts = name.split("__")
            if len(parts) != 3:
                raise ConfigError(f"{name}: use SETTINGS__<SECTION>__<KEY>, e.g. SETTINGS__MODELS__GENERATOR_MODEL.")
            layer.setdefault(parts[1].lower(), {})[parts[2].lower()] = _parse_env_value(raw)
    try:
        clean = schema.validate(layer) if layer else {}
    except schema.SettingsError as e:
        raise ConfigError(f"Invalid deployment default for the Settings screen: {e}") from e
    _cache["defaults"] = clean
    return clean


# ── Startup and CLI ──────────────────────────────────────────────────────────

def validate_on_startup() -> Settings:
    """Check the configuration once at process start: fail in production, warn in development."""
    cfg = get_settings()
    settings_defaults_layer()           # raises ConfigError on an invalid deployment default
    problems = cfg.check()
    if problems and cfg.is_production:
        raise ConfigError("Configuration is not ready for production:\n  - " + "\n  - ".join(problems))
    for p in problems:
        logger.warning("Config: %s", p)
    return cfg


def _source_of(name: str) -> str:
    if os.environ.get(name.upper()) is not None:
        return ".env" if _in_env_file(name.upper()) else "environment"
    if name in {k.lower() for k in _read_yaml()}:
        return _yaml_path().name
    return "default"


def _in_env_file(name: str) -> bool:
    from dotenv import dotenv_values
    return ENV_FILE.is_file() and name in dotenv_values(ENV_FILE) and os.environ.get(name) == dotenv_values(ENV_FILE).get(name)


def describe() -> List[Tuple[str, str, str]]:
    """(NAME, value, source) for every setting, secrets masked."""
    cfg = get_settings()
    rows = []
    for name in Settings.model_fields:
        value = getattr(cfg, name)
        secret = any(s in name for s in ("key", "secret", "password")) or name == "database_url"
        shown = ("set" if value else "") if secret and name != "database_url" else str(value if value is not None else "")
        if name == "database_url" and "@" in shown:
            shown = shown.split("://", 1)[0] + "://***@" + shown.split("@", 1)[1]
        rows.append((name.upper(), shown, _source_of(name)))
    return rows


def example_yaml() -> str:
    """The text of config.example.yaml: every setting, grouped as declared above, with its default and description, all
    commented out. Regenerate with `python -m app.core.config --example > ../config.example.yaml` after adding a setting
    (tests/test_config.py fails while the file is out of date)."""
    import inspect
    import re
    groups: List[Tuple[str, List[str]]] = []
    for line in inspect.getsource(Settings).splitlines():
        heading = re.match(r"\s+# ── (.+?) ─", line)
        if heading:
            groups.append((heading.group(1), []))
            continue
        field = re.match(r"\s{4}([a-z][a-z0-9_]*):", line)
        if field and field.group(1) in Settings.model_fields and groups:
            groups[-1][1].append(field.group(1))
    out = [
        "# Investment Analyst: deployment configuration.",
        "#",
        "# GENERATED from backend/app/core/config.py by `python -m app.core.config --example`. Do not edit by hand.",
        "# Copy to config.yaml (repo root, or set APP_CONFIG_FILE) and uncomment what you change. Each key is also an",
        "# environment variable of the same name in upper case; the environment and .env win over this file.",
        "# Keep secrets (keys, passwords) in .env or the environment rather than here.",
        "# `python -m app.core.config` prints the values in force and where each came from.",
    ]
    for title, names in groups:
        if not names:
            continue
        out += ["", f"# ── {title} " + "─" * max(3, 100 - len(title))]
        for name in names:
            info = Settings.model_fields[name]
            default = info.get_default(call_default_factory=True)
            shown = "" if default is None else default
            value = json.dumps(str(shown)) if isinstance(shown, (str, Path)) else json.dumps(shown)
            desc = f"  # {info.description}" if info.description else ""
            out.append(f"# {name}: {value}{desc}")
    out += [
        "",
        "# ── Defaults for the Settings screen " + "─" * 66,
        "# What every user starts from, before an admin, the user or a portfolio changes it in the app. Any section/key of",
        "# GET /settings/schema. Also settable as SETTINGS__<SECTION>__<KEY> environment variables. Validated at startup.",
        "# settings_defaults:",
        "#   models:",
        "#     generator_model: gpt-4o",
        "#   answers:",
        "#     style: concise",
        "#   documents:",
        "#     reading_strategy: economy",
    ]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    import sys
    if "--example" in sys.argv:
        sys.stdout.write(example_yaml())
        raise SystemExit(0)
    rows = describe()
    width = max(len(r[0]) for r in rows)
    for name, value, source in rows:
        print(f"{name:<{width}}  {value:<50}  [{source}]")
    layer = settings_defaults_layer()
    print("\nDeployment defaults for the Settings screen:", json.dumps(layer) if layer else "(none)")
    problems = get_settings().check()
    print("\nProblems:" if problems else "\nNo problems found.")
    for p in problems:
        print("  -", p)
