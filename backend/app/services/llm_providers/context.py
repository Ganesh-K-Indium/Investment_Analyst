"""Provider credentials in force for the request being served, without passing them through every function.

Resolution, last wins:  environment  <  workspace-saved (admin)  <  the user's own saved value. The service loads the merged saved
values for the user once per request and makes them current here; code that builds a model asks `get(name)`.
"""
import contextvars
from contextlib import contextmanager
from typing import Dict, Iterator, Optional
from app.core.config import get_settings

_current: contextvars.ContextVar[Optional[Dict[str, str]]] = contextvars.ContextVar("llm_credentials", default=None)


def get(name: str) -> str:
    saved = (_current.get() or {}).get(name)
    if saved:
        return saved.strip()
    return get_settings().secret(name)


@contextmanager
def use(creds: Optional[Dict[str, str]]) -> Iterator[None]:
    token = _current.set(dict(creds or {}))
    try:
        yield
    finally:
        _current.reset(token)
