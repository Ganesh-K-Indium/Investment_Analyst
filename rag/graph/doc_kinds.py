"""Tells analyst-note chunks apart from filing/web chunks inside the RAG graph.

Notes (app/services/analyst_notes.py) travel through the graph as ordinary Documents, marked by
metadata.source_type == "analyst_note". They are the user's own views, not company disclosures, so grading and
routing must judge whether the FILINGS answer the question, while generation still gets the notes as context.
"""
from typing import Any, Iterable, List, Tuple

ANALYST_NOTE = "analyst_note"


def is_analyst_note(doc: Any) -> bool:
    meta = getattr(doc, "metadata", None)
    return isinstance(meta, dict) and meta.get("source_type") == ANALYST_NOTE


def split_notes(docs: Iterable[Any]) -> Tuple[List[Any], List[Any]]:
    """(everything that is not a note, the notes), each in the original order."""
    rest: List[Any] = []
    notes: List[Any] = []
    for d in docs or []:
        (notes if is_analyst_note(d) else rest).append(d)
    return rest, notes
