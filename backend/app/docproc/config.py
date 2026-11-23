import os
from pathlib import Path

from dotenv import load_dotenv

from app.core.paths import DOCPROC_DIR

load_dotenv()

DATA_DIR = Path(os.getenv("DOCPROC_DATA_DIR", DOCPROC_DIR)).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
DB_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'extractions.db'}")
API_KEY = os.getenv("APP_API_KEY", "")
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "50")) * 1024 * 1024
MAX_PAGES = int(os.getenv("MAX_PAGES", "200"))


def max_pages() -> int:
    """Most pages in one file: the user's setting (documents.max_pages), else MAX_PAGES."""
    from app.services import app_settings
    return int(app_settings.get("documents", "max_pages", MAX_PAGES))


def data_dir() -> Path:
    """Read from the environment at call time, like the folders below."""
    return Path(os.getenv("DOCPROC_DATA_DIR", DOCPROC_DIR)).resolve()


def templates_dir() -> Path:
    """Legacy location of custom templates from before workspaces; read only when migrating."""
    return Path(os.getenv("DOCPROC_DATA_DIR", DOCPROC_DIR)).resolve() / "templates"


def custom_types_file() -> Path:
    return Path(os.getenv("DOCPROC_DATA_DIR", DOCPROC_DIR)).resolve() / "custom_types.json"


def local_only() -> bool:
    """LOCAL_ONLY=1: nothing may leave this machine. Cloud model providers are switched off; local OCR and self-hosted
    models (Ollama, a private OpenAI-compatible server) still work."""
    from app.services import app_settings
    return os.getenv("LOCAL_ONLY", "0").lower() in ("1", "true", "yes") or bool(app_settings.get("documents", "local_only", False))


def llm_enabled() -> bool:
    """True when models may be called at all: not switched off, and at least one provider is configured."""
    if os.getenv("LLM_ENABLED", "1").lower() in ("0", "false", "no"):
        return False
    from app.docproc.engines.providers import llm
    return llm.any_ready()


def extraction_models() -> list:
    """The server's default reader pair for structured extraction (a workspace can choose its own, see engines/plan.py)."""
    raw = os.getenv("EXTRACTION_MODELS", "").strip()
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
    explicit = os.getenv("ADJUDICATION_MODEL", "").strip()
    if explicit:
        return explicit
    from app.docproc.engines.strategy import plan
    return (plan.current().extract_models or [""])[0]


PIPELINE_VERSION = "2026.11-engines-v6"  # bump when extraction behaviour changes: older cached results are then not reused


def long_doc_pages() -> int:
    """Digital documents longer than this are read once per page (primary model only) instead of by two readers."""
    return int(os.getenv("DISCOVER_LONG_DOC_PAGES", "25"))


def figure_read_max() -> int:
    """Most figures read in one request, and in one document altogether: reading a figure is a model call, so it is capped."""
    return int(os.getenv("FIGURE_READ_MAX", "12"))


def digital_model_mode() -> str:
    """When a model reads a digital (text-layer) page with no template: "auto" (default) only for form-like pages code could
    not make sense of, "never" (code only), "always" (every digital page)."""
    v = os.getenv("DISCOVER_DIGITAL_MODEL", "auto").strip().lower()
    return v if v in ("auto", "never", "always") else "auto"


def classify_give_up_after() -> int:
    """The classifier model is only a second opinion for pages the keyword rules could not name. After this many in a row
    that it also could not name, stop asking: the rest of a long untyped document is not going to be a known form."""
    return int(os.getenv("CLASSIFY_UNKNOWN_STREAK", "3"))


def ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
