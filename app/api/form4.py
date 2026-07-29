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

from app.services.form4_ingestion import run_form4_ingestion, run_form4_ingestion_multi

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/form4", tags=["Form 4 - Insider Trading"])

TICKER_PATTERN = re.compile(r'^[A-Z]{1,5}$')


def _validate_ticker(raw: str) -> str:
    ticker = raw.upper().strip()
    if not TICKER_PATTERN.match(ticker):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid ticker '{ticker}'. Must be 1–5 uppercase letters (e.g. NVDA).",
        )
    return ticker


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
    total_url_fetched: int = Field(..., description="Total filing URLs retrieved from SEC EDGAR.")
    forms_with_common_stock: int = Field(..., description="Forms that contained ≥1 common stock transaction saved to DB.")
    forms_with_0_common_stock: int = Field(..., description="Forms with only derivatives (dummy row inserted to prevent reprocessing).")
    transactions_saved_total: int = Field(..., description="Total individual stock movement records added to the DB in this run.")
    skipped_already_in_db: int = Field(..., description="Filings already present in the database (skipped).")
    failed: int = Field(..., description="Filings that could not be fetched or parsed due to errors.")
    date_range: dict = Field(..., description="Effective start/end dates used for ingestion.")
    message: Optional[str] = Field(None, description="Optional status message.")


class Form4BatchIngestRequest(BaseModel):
    tickers: list[str] = Field(
        ...,
        description="Stock ticker symbols to ingest (e.g. ['NVDA', 'AAPL', 'MSFT']). Each must be 1–5 uppercase letters. Duplicates are ignored.",
        examples=[["NVDA", "AAPL", "MSFT"]],
        min_length=1,
    )
    start_date: Optional[date] = Field(
        None,
        description="Fetch filings from this date onward (YYYY-MM-DD), applied to every ticker. Defaults to 2025-01-01.",
        examples=["2025-01-01"],
    )


class Form4BatchIngestResponse(BaseModel):
    tickers: list[str] = Field(..., description="Normalized, de-duplicated tickers actually ingested, in order.")
    results: list[Form4IngestResponse] = Field(..., description="Per-ticker ingestion result, in the same order as `tickers`.")
    totals: dict = Field(..., description="Aggregate counts summed across all tickers in this batch.")
    date_range: dict = Field(..., description="Effective start/end dates used for the whole batch.")


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
    ticker = _validate_ticker(request.ticker)

    try:
        result = await run_form4_ingestion(
            ticker=ticker,
            start_date=request.start_date,
        )
        if result is None:
            raise RuntimeError("Ingestion pipeline returned no result (check logs for details).")
        return Form4IngestResponse(**result)

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"Form4 ingestion failed for {ticker}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Ingestion failed for '{ticker}': {exc}",
        )


@router.post(
    "/ingest/batch",
    response_model=Form4BatchIngestResponse,
    summary="Ingest Form 4 data from SEC EDGAR for multiple tickers",
    description=(
        "Fetches all available Form 4 (insider trading) filings for each of the given "
        "tickers directly from SEC EDGAR, sequentially, and aggregates the per-ticker "
        "results. Same filtering/dedup/persistence behavior as /form4/ingest, just "
        "looped across tickers with one combined summary."
    ),
)
async def ingest_form4_batch(request: Form4BatchIngestRequest):
    """
    Import Form 4 insider trading filings from SEC EDGAR for multiple tickers in one call.

    - Validates and de-duplicates the ticker list up front
    - Ingests tickers sequentially (each ticker's failure doesn't stop the others)
    - Returns a per-ticker breakdown plus totals summed across the whole batch
    - Safe to call repeatedly — dedup by SEC accession number still applies per ticker
    """
    tickers = [_validate_ticker(t) for t in request.tickers]

    try:
        result = await run_form4_ingestion_multi(
            tickers=tickers,
            start_date=request.start_date,
        )
        if result is None:
            raise RuntimeError("Batch ingestion pipeline returned no result (check logs for details).")
        return Form4BatchIngestResponse(**result)

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"Form4 batch ingestion failed for {tickers}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Batch ingestion failed for {tickers}: {exc}",
        )
