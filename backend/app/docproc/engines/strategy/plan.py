"""
How a workspace wants its scanned pages read: the reading plan.

A strategy is a preset for the escalation ladder (see ladder.py):

    economy    local OCR only. No model is ever called, so nothing leaves the server and nothing costs money.
    balanced   local OCR first. A page goes to the vision models only when the local reading is doubtful.   (default)
    accuracy   every scanned page is read by two vision models and compared (the original behaviour).
    custom     the same knobs, set by hand.

The plan also names the local OCR engine, the vision models (up to two; the second one cross-checks the first), the
confidence the local reading must reach, and an optional token cap per document.

Where each setting comes from, last wins:  built-in default -> environment (READING_STRATEGY, LOCAL_OCR_ENGINE,
ESCALATE_BELOW, TOKEN_CAP) -> the workspace's saved settings -> a per-upload choice of strategy.

Digital pages are not affected by any of this: PDFs with real text are read by code at no cost under every strategy.
"""
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

from app.docproc import config
from app.docproc.engines.providers import llm
from app.docproc.engines.ocr import local
from app.core.config import get_settings

STRATEGIES: List[Dict[str, Any]] = [
    {"id": "economy", "label": "Economy", "mode": "local",
     "summary": "Local OCR only. No model is called: free, private, and fine for clean printed scans."},
    {"id": "balanced", "label": "Balanced", "mode": "escalate",
     "summary": "Local OCR first; only pages it reads poorly go to a vision model. The best cost for most documents."},
    {"id": "accuracy", "label": "Accuracy", "mode": "vision",
     "summary": "Two vision models read every scanned page and are compared. Highest accuracy, highest cost."},
    {"id": "custom", "label": "Custom", "mode": "escalate",
     "summary": "Set the engine, models, confidence and cost cap yourself."},
]
_BY_ID = {s["id"]: s for s in STRATEGIES}
MODES = ("local", "escalate", "vision")
DEFAULT_THRESHOLD = 0.85


@dataclass(frozen=True)
class Plan:
    strategy: str
    mode: str                           # local | escalate | vision
    local_engine: Optional[str]         # None when no local engine is installed
    models: Tuple[str, ...]             # the user's choice; empty means the server's defaults
    escalate_below: float
    token_cap: int                      # per document; 0 = no cap
    llm: bool                           # models may be called under this plan
    notes: Tuple[str, ...] = ()

    @property
    def read_models(self) -> List[str]:
        from app.docproc.services.reading import ocr
        return list(self.models) or ocr.configured_models()

    @property
    def extract_models(self) -> List[str]:
        return list(self.models) or config.extraction_models()

    def fingerprint(self) -> str:
        """Anything that changes what a reading would produce. A cached result is only reused under the same fingerprint."""
        key = json.dumps([self.strategy, self.mode, self.local_engine, list(self.models) or self.read_models, self.escalate_below,
                          self.token_cap, self.llm], sort_keys=True)
        return hashlib.sha1(key.encode()).hexdigest()[:12]

    def describe(self) -> Dict[str, Any]:
        return {"strategy": self.strategy, "mode": self.mode, "local_engine": self.local_engine, "models": self.read_models if self.llm else [],
                "escalate_below": self.escalate_below, "token_cap": self.token_cap, "uses_models": self.llm and self.mode != "local",
                "notes": list(self.notes), "fingerprint": self.fingerprint()}


def _float(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _int(v: Any, default: int) -> int:
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return default


def resolve(stored: Optional[Dict[str, Any]] = None, override: Optional[Dict[str, Any]] = None) -> Plan:
    """The plan in force for the given saved settings and optional per-upload override."""
    stored, override = stored or {}, override or {}
    notes: List[str] = []
    strategy = override.get("strategy") or stored.get("strategy") or get_settings().reading_strategy
    if strategy not in _BY_ID:
        strategy = "balanced"
    mode = stored.get("mode") if strategy == "custom" and stored.get("mode") in MODES else _BY_ID[strategy]["mode"]

    ready = [e.id for e in local.ready()]
    wanted = stored.get("local_engine") or get_settings().local_ocr_engine
    engine = wanted if wanted in ready else (ready[0] if ready else None)
    if engine != wanted and wanted:
        notes.append(f"{wanted} is not installed, so {engine or 'no local engine'} is used")
    chosen = local.get(engine)
    if chosen is not None and not chosen.calibrated and "tesseract" not in ready:
        notes.append(f"{chosen.label} is checked against Tesseract, which is not installed, so most pages will go to the vision models")
    if engine is None and mode == "escalate":
        mode = "vision"
        notes.append("no local OCR engine is installed, so pages go straight to the vision models")

    models = tuple(m for m in (stored.get("models") or []) if llm.valid_spec(m))[:2]
    has_llm = config.llm_enabled() and mode != "local"
    if mode != "local" and not config.llm_enabled():
        notes.append("no model is configured on this server, so pages are read locally")
    threshold = min(0.99, max(0.3, _float(stored.get("escalate_below", get_settings().escalate_below), DEFAULT_THRESHOLD)))
    cap = _int(stored.get("token_cap", get_settings().token_cap), 0)
    return Plan(strategy, mode, engine, models, threshold, cap, has_llm, tuple(notes))


def clean(body: Dict[str, Any]) -> Dict[str, Any]:
    """Validate settings a user sent. Raises ValueError with a message safe to show."""
    out: Dict[str, Any] = {}
    s = body.get("strategy")
    if s is not None:
        if s not in _BY_ID:
            raise ValueError("Choose Economy, Balanced, Accuracy or Custom.")
        out["strategy"] = s
    mode = body.get("mode")
    if mode is not None:
        if mode not in MODES:
            raise ValueError("Unknown reading mode.")
        out["mode"] = mode
    eng = body.get("local_engine")
    if eng:
        e = local.get(eng)
        if e is None:
            raise ValueError("That local OCR engine does not exist.")
        if not e.available():
            raise ValueError(f"{e.label} is not installed on this server. {e.install}")
        out["local_engine"] = eng
    models = body.get("models")
    if models is not None:
        if not isinstance(models, list) or len(models) > 2 or not all(llm.valid_spec(m) for m in models):
            raise ValueError("Pick up to two models, written as provider:model (for example anthropic:claude-sonnet-5-5).")
        for m in models:
            if not llm.spec_ready(m):
                p = llm.PROVIDERS[llm.split(m)[0]]
                raise ValueError(f"{p.label} is not available on this server. " + ("Cloud models are switched off (LOCAL_ONLY)." if p.cloud and config.local_only() else p.hint))
        out["models"] = list(dict.fromkeys(models))
    if body.get("escalate_below") is not None:
        t = _float(body["escalate_below"], -1)
        if not 0.3 <= t <= 0.99:
            raise ValueError("The confidence needed must be between 30% and 99%.")
        out["escalate_below"] = round(t, 2)
    if body.get("token_cap") is not None:
        try:
            n = int(body["token_cap"])
        except (TypeError, ValueError):
            n = -1
        if n < 0 or n > 50_000_000:
            raise ValueError("The token cap must be zero (no cap) or a positive number.")
        out["token_cap"] = n
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Per-workspace storage
# ---------------------------------------------------------------------------------------------------------------------

def stored(workspace_id: Optional[str]) -> Dict[str, Any]:
    if not workspace_id:
        return {}
    from app.docproc import db
    with db.session() as s:
        row = s.get(db.WorkspaceReading, workspace_id)
        return dict(row.settings or {}) if row else {}


def save(workspace_id: str, settings: Dict[str, Any]) -> None:
    from app.docproc import db
    with db.session() as s:
        row = s.get(db.WorkspaceReading, workspace_id)
        if row is None:
            s.add(db.WorkspaceReading(workspace_id=workspace_id, settings=settings))
        else:
            row.settings = settings
        s.commit()


def for_workspace(workspace_id: Optional[str], override: Optional[Dict[str, Any]] = None) -> Plan:
    return resolve(stored(workspace_id), override)


_CURRENT: ContextVar[Optional[Plan]] = ContextVar("reading_plan", default=None)


@contextmanager
def use(plan: Plan) -> Iterator[None]:
    token = _CURRENT.set(plan)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current() -> Plan:
    """The plan of the extraction being run (set by `use`), or the server's defaults outside one."""
    return _CURRENT.get() or resolve()
