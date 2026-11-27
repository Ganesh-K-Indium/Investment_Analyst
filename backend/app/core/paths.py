"""Single source of truth for where the backend reads and writes files.

Layout (repo root = the folder that holds `backend/`, `data/`, `venv/`):

    backend/        all Python code (this package's parent)
    data/           everything the app writes at runtime (git-ignored)

`DATA_DIR` can be moved with the DATA_DIR setting (environment, .env or config.yaml; the Docker image sets it to /app/data).
Nothing here depends on the current working directory.
"""
from __future__ import annotations

from app.core.config import BACKEND_ROOT, REPO_ROOT, get_settings  # noqa: F401  (BACKEND_ROOT re-exported; importing config loads .env first)

DATA_DIR = (get_settings().data_dir or REPO_ROOT / "data").resolve()

SEC_FILINGS_DIR = DATA_DIR / "sec_filings"        # downloaded 10-K/10-Q/... PDFs, one folder per ticker
XML_FILINGS_DIR = DATA_DIR / "xml_filings"        # Form 4 XML kept for verification
PDF_FILINGS_DIR = DATA_DIR / "pdf_filings"        # Form 4 rendered PDFs
MACRO_DIR = DATA_DIR / "macro"                    # FRED macro series
USER_DOCS_DIR = DATA_DIR / "user_docs"            # documents users upload
DOCPROC_DIR = DATA_DIR / "docproc"                # extraction engine state (uploads, keys, models)
OUTPUT_DIR = DATA_DIR / "output"                  # debug dumps of agent responses
CHARTS_DIR = DATA_DIR / "generated_charts"        # locally saved plotly figures
LOGS_DIR = DATA_DIR / "logs"                      # ingestion and MCP server logs
