"""
Form 4 Ingestion Service

Thin wrapper that delegates to the existing ingestion pipeline in
ingestion/form4/ingest.py — no logic is duplicated here.
"""
from ingestion.form4.ingest import run_form4_ingestion, run_form4_ingestion_multi  # noqa: E402

__all__ = ["run_form4_ingestion", "run_form4_ingestion_multi"]
