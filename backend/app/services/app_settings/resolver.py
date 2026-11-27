"""Resolve the effective settings for a request, and make them available to the graph without threading a parameter through every node.

Layers, last wins:
    code defaults (schema.SYSTEM_DEFAULTS) -> deployment defaults (config.yaml / SETTINGS__* env, app/core/config.py)
    -> admin (system scope, UI) -> user (UI) -> portfolio (UI) -> per-request override
"""
import contextvars
import copy
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from .schema import SYSTEM_DEFAULTS

_current: contextvars.ContextVar[Optional[Dict[str, Dict[str, Any]]]] = contextvars.ContextVar("app_settings", default=None)


def merge(base: Dict[str, Dict[str, Any]], *layers: Optional[Dict[str, Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """Section-by-section, key-by-key overlay; later layers win. Values are replaced, not deep-merged, so a saved list stays what the user saved."""
    out = copy.deepcopy(base)
    for layer in layers:
        for section, values in (layer or {}).items():
            out.setdefault(section, {}).update(copy.deepcopy(values))
    return out


def defaults() -> Dict[str, Dict[str, Any]]:
    """What a user starts from: the code defaults with this deployment's defaults applied."""
    from app.core.config import settings_defaults_layer
    layer = settings_defaults_layer()
    if not layer:
        return SYSTEM_DEFAULTS
    cached = _defaults_cache.get("value")
    if _defaults_cache.get("layer") is not layer:
        cached = merge(SYSTEM_DEFAULTS, layer)
        _defaults_cache.update(layer=layer, value=cached)
    return cached


_defaults_cache: Dict[str, Any] = {}


def resolve(*layers: Optional[Dict[str, Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    return merge(defaults(), *layers)


def current() -> Dict[str, Dict[str, Any]]:
    """The settings in force for this request, or the defaults outside one (scripts, tests, ingestion)."""
    return _current.get() or defaults()


@contextmanager
def use(settings: Optional[Dict[str, Dict[str, Any]]]) -> Iterator[Dict[str, Dict[str, Any]]]:
    s = settings or defaults()
    token = _current.set(s)
    try:
        yield s
    finally:
        _current.reset(token)


def get(section: str, key: str, default: Any = None) -> Any:
    return current().get(section, {}).get(key, default)
