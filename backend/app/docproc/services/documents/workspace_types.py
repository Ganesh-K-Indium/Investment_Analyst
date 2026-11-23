"""
Custom document types belong to a workspace. The classifier, router and pipeline read "the custom types in force",
which this module holds in a context variable set for the duration of a request or an extraction run:

    with workspace_types.use(workspace_id):
        ...classify / route / extract...

Built-in types are global; a workspace sees them plus only its own. A ContextVar (not a global) keeps two workspaces'
concurrent extractions from seeing each other's types.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List, Optional

_ITEMS: ContextVar[List[Dict[str, Any]]] = ContextVar("workspace_types", default=[])


def load(workspace_id: str) -> List[Dict[str, Any]]:
    from app.docproc import db
    with db.session() as s:
        rows = s.query(db.CustomType).filter_by(workspace_id=workspace_id).order_by(db.CustomType.id).all()
        return [{"entry": r.entry, "template": r.template} for r in rows]


@contextmanager
def use(workspace_id: Optional[str]) -> Iterator[None]:
    token = _ITEMS.set(load(workspace_id) if workspace_id else [])
    try:
        yield
    finally:
        _ITEMS.reset(token)


def entries() -> List[Dict[str, Any]]:
    return [i["entry"] for i in _ITEMS.get()]


def template(name: str) -> Optional[Dict[str, Any]]:
    for i in _ITEMS.get():
        if i["entry"].get("template") == name or i["template"].get("doc_type") == name:
            return i["template"]
    return None
