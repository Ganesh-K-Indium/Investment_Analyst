"""
Form 4 Ingestion Service

Thin wrapper that delegates to the existing ingestion pipeline in
ingestion/Form4_Ingestion/ingest.py — no logic is duplicated here.
"""
import sys
import os

# Make ingestion/Form4_Ingestion/ importable (fetch.py, parse.py, etc.)
_form4_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "ingestion", "Form4_Ingestion"
)
if _form4_dir not in sys.path:
    sys.path.insert(0, _form4_dir)

from ingestion.Form4_Ingestion.ingest import run_form4_ingestion  # noqa: E402

__all__ = ["run_form4_ingestion"]
