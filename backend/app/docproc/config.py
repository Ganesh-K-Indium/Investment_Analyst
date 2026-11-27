"""Document-engine settings, read at call time from the central configuration (app/core/config.py) so a test or a reload
sees the current values. User-level choices (reading strategy, engine, limits) live in the Settings screen instead."""
from pathlib import Path

from app.core.config import get_settings
from app.core.paths import DOCPROC_DIR


def max_pages() -> int:
    """Most pages in one file: the user's setting (documents.max_pages), else MAX_PAGES."""
    from app.services import app_settings
    return int(app_settings.get("documents", "max_pages", get_settings().max_pages))


def data_dir() -> Path:
    """DOCPROC_DATA_DIR, else <data_dir>/docproc."""
    return Path(get_settings().docproc_data_dir or DOCPROC_DIR).resolve()


def templates_dir() -> Path:
    """Legacy location of custom templates from before workspaces; read only when migrating."""
    return data_dir() / "templates"


def custom_types_file() -> Path:
    return data_dir() / "custom_types.json"


def local_only() -> bool:
    """LOCAL_ONLY=1: nothing may leave this machine. Cloud model providers are switched off; local OCR and self-hosted
    models (Ollama, a private OpenAI-compatible server) still work."""
    from app.services import app_settings
    return get_settings().local_only or bool(app_settings.get("documents", "local_only", False))


def llm_enabled() -> bool:
    """True when models may be called at all: not switched off, and at least one provider is configured."""
    if not get_settings().llm_enabled:
        return False
    from app.docproc.engines.providers import llm
    return llm.any_ready()


def extraction_models() -> list:
    """The server's default reader pair for structured extraction (a workspace can choose its own, see engines/plan.py)."""
    raw = get_settings().extraction_models.strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()][:2]
    from app.docproc.engines.providers import llm
    return llm.default_models()


def adjudicator_model() -> str:
    """Model for the third look at disputed values (see services/adjudicate.py). Empty when no model is available.
    Defaults to the primary extraction model of the plan in force: it is called once per page that has a dispute, never
    for pages that agree."""
    if not llm_enabled():
        return ""
    explicit = get_settings().adjudication_model.strip()
    if explicit:
        return explicit
    from app.docproc.engines.strategy import plan
    return (plan.current().extract_models or [""])[0]


PIPELINE_VERSION = "2026.11-engines-v6"  # bump when extraction behaviour changes: older cached results are then not reused


def long_doc_pages() -> int:
    """Digital documents longer than this are read once per page (primary model only) instead of by two readers."""
    return get_settings().discover_long_doc_pages


def figure_read_max() -> int:
    """Most figures read in one request, and in one document altogether: reading a figure is a model call, so it is capped."""
    return get_settings().figure_read_max


def digital_model_mode() -> str:
    """When a model reads a digital (text-layer) page with no template: "auto" (default) only for form-like pages code could
    not make sense of, "never" (code only), "always" (every digital page)."""
    v = get_settings().discover_digital_model.strip().lower()
    return v if v in ("auto", "never", "always") else "auto"


def classify_give_up_after() -> int:
    """The classifier model is only a second opinion for pages the keyword rules could not name. After this many in a row
    that it also could not name, stop asking: the rest of a long untyped document is not going to be a known form."""
    return get_settings().classify_unknown_streak
