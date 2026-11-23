"""Single source of truth for where the backend reads and writes files.

Layout (repo root = the folder that holds `backend/`, `data/`, `venv/`):

    backend/        all Python code (this package's parent)
    data/           everything the app writes at runtime (git-ignored)

`DATA_DIR` can be moved with the DATA_DIR environment variable (the Docker image sets it to /app/data).
Nothing here depends on the current working directory.
"""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
DATA_DIR = Path(os.getenv("DATA_DIR") or REPO_ROOT / "data").resolve()

SEC_FILINGS_DIR = DATA_DIR / "sec_filings"        # downloaded 10-K/10-Q/... PDFs, one folder per ticker
XML_FILINGS_DIR = DATA_DIR / "xml_filings"        # Form 4 XML kept for verification
PDF_FILINGS_DIR = DATA_DIR / "pdf_filings"        # Form 4 rendered PDFs
MACRO_DIR = DATA_DIR / "macro"                    # FRED macro series
USER_DOCS_DIR = DATA_DIR / "user_docs"            # documents users upload
DOCPROC_DIR = DATA_DIR / "docproc"                # extraction engine state (uploads, keys, models)
OUTPUT_DIR = DATA_DIR / "output"                  # debug dumps of agent responses
CHARTS_DIR = DATA_DIR / "generated_charts"        # locally saved plotly figures
LOGS_DIR = DATA_DIR / "logs"                      # ingestion and MCP server logs
