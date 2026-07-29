"""
SEC EDGAR Filing Ingestion Endpoints

POST /edgar/ingest — fetch 10-K/10-Q/8-K filings for a ticker directly from
SEC EDGAR, render each to PDF, and ingest them into the RAG vector store
under the correct ticker collection with the correct filing_type tag.
"""
import logging
import re
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from ingestion.edgar_fetcher import SecEdgarFetcher, VALID_FORM_TYPES
from app.database.models import User
from app.auth.deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/edgar", tags=["EDGAR - Filing Ingestion"])

TICKER_PATTERN = re.compile(r'^[A-Z]{1,5}$')


def _validate_ticker(raw: str) -> str:
    ticker = raw.upper().strip()
    if not TICKER_PATTERN.match(ticker):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid ticker '{ticker}'. Must be 1–5 uppercase letters (e.g. AAPL).",
        )
    return ticker


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class EdgarIngestRequest(BaseModel):
    ticker: str = Field(
        ...,
        description="Stock ticker symbol (e.g. AAPL). Must be 1–5 uppercase letters.",
        examples=["AAPL"],
    )
    form_types: List[str] = Field(
        default_factory=lambda: list(VALID_FORM_TYPES),
        description="SEC form types to fetch — any subset of 10-K, 10-Q, 8-K.",
        examples=[["10-K", "10-Q", "8-K"]],
    )
    start_date: Optional[date] = Field(
        None,
        description="Only include filings on/after this date (YYYY-MM-DD). Defaults to no lower bound.",
        examples=["2025-01-01"],
    )
    end_date: Optional[date] = Field(
        None,
        description="Only include filings on/before this date (YYYY-MM-DD). Defaults to today.",
    )
    ingest: bool = Field(
        True,
        description="If False, only downloads/renders PDFs without ingesting into the vector store.",
    )


class EdgarFilingResult(BaseModel):
    form: str
    filing_date: str
    url: str
    pdf_path: str
    status: str = Field(..., description="downloaded | ingested | failed | ingest_failed")
    chunks_added: Optional[int] = None
    error: Optional[str] = None
    message: Optional[str] = None


class EdgarIngestResponse(BaseModel):
    ticker: str
    form_types: List[str]
    date_range: dict
    total_filings_found: int
    ingested: int
    downloaded_only: int
    failed: int
    filings: List[EdgarFilingResult]


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/ingest",
    response_model=EdgarIngestResponse,
    summary="Fetch and ingest SEC filings (10-K/10-Q/8-K) for a ticker",
    description=(
        "Fetches the requested SEC filing types for a ticker directly from SEC "
        "EDGAR, renders each to PDF, and ingests them into the ticker's RAG "
        "vector collection tagged with the correct filing_type. Filings whose "
        "PDF was already rendered locally, or whose content was already "
        "ingested (by content hash), are skipped automatically."
    ),
)
async def ingest_edgar_filings(
    request: EdgarIngestRequest,
    current_user: User = Depends(get_current_user),
):
    ticker = _validate_ticker(request.ticker)

    invalid_types = [t for t in request.form_types if t not in VALID_FORM_TYPES]
    if invalid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid form_types {invalid_types}. Must be a subset of {list(VALID_FORM_TYPES)}.",
        )

    try:
        async with SecEdgarFetcher() as fetcher:
            summary = await fetcher.fetch_filings(
                ticker=ticker,
                form_types=request.form_types,
                start_date=request.start_date,
                end_date=request.end_date,
                ingest=request.ingest,
            )
        return EdgarIngestResponse(**summary)

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"EDGAR ingestion failed for {ticker}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"EDGAR ingestion failed for '{ticker}': {exc}",
        )
