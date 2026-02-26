"""
Form 4 Insider Trading Endpoints

POST /form4/ingest  — fetch and ingest Form 4 filings from SEC EDGAR for a ticker.
"""
import logging
import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from app.services.form4_ingestion import run_form4_ingestion

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/form4", tags=["Form 4 - Insider Trading"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class Form4IngestRequest(BaseModel):
    ticker: str = Field(
        ...,
        description="Stock ticker symbol (e.g. NVDA, AAPL). Must be 1–5 uppercase letters.",
        examples=["NVDA"],
    )
    start_date: Optional[date] = Field(
        None,
        description="Fetch filings from this date onward (YYYY-MM-DD). Defaults to 2025-01-01.",
        examples=["2025-01-01"],
    )


class Form4IngestResponse(BaseModel):
    ticker: str
    total_fetched: int = Field(..., description="Total filing URLs retrieved from SEC EDGAR.")
    saved: int = Field(..., description="Filings that contained ≥1 new transaction saved to DB.")
    skipped_duplicate: int = Field(..., description="Filings already present in the database.")
    failed: int = Field(..., description="Filings that could not be fetched or parsed.")
    date_range: dict = Field(..., description="Effective start/end dates used for ingestion.")
    message: Optional[str] = Field(None, description="Optional status message.")


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/ingest",
    response_model=Form4IngestResponse,
    summary="Ingest Form 4 data from SEC EDGAR",
    description=(
        "Fetches all available Form 4 (insider trading) filings for the given ticker "
        "directly from SEC EDGAR, filters for common-stock transactions, deduplicates "
        "by accession number, and persists new records to the application database."
    ),
)
async def ingest_form4(request: Form4IngestRequest):
    """
    Import Form 4 insider trading filings from SEC EDGAR for a specific ticker.

    - Fetches all filings from `start_date` to today
    - Paginates through SEC EDGAR results (newest first)
    - Filters for non-derivative (common stock) transactions only
    - Deduplicates by SEC accession number — safe to call repeatedly
    - Stores results in `portfolios.db` (`form4_transactions` table)
    """
    ticker = request.ticker.upper().strip()

    if not re.match(r'^[A-Z]{1,5}$', ticker):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid ticker '{ticker}'. Must be 1–5 uppercase letters (e.g. NVDA).",
        )

    try:
        result = run_form4_ingestion(
            ticker=ticker,
            start_date=request.start_date,
        )
        return Form4IngestResponse(**result)

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"Form4 ingestion failed for {ticker}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Ingestion failed for '{ticker}': {exc}",
        )
