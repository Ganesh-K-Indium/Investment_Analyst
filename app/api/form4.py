"""
Form 4 Insider Trading Endpoints

POST /form4/ingest         — fetch and ingest Form 4 filings from SEC EDGAR for a ticker.
POST /form4/ingest/batch   — same, for several tickers (single JSON response).
POST /form4/ingest/stream  — same as batch, streamed as SSE (progress + live logs).
"""
import asyncio
import json
import logging
import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services.form4_ingestion import run_form4_ingestion, run_form4_ingestion_multi
from app.database.models import User
from app.auth.deps import get_current_user
from app.utils.log_capture import sse_log_context

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
    end_date: Optional[date] = Field(
        None,
        description="Only used by /form4/ingest/stream: fetch filings up to this date (YYYY-MM-DD). Defaults to today.",
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
async def ingest_form4(
    request: Form4IngestRequest,
    current_user: User = Depends(get_current_user),
):
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
async def ingest_form4_batch(
    request: Form4BatchIngestRequest,
    current_user: User = Depends(get_current_user),
):
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


# Progress dicts emitted by the ingestion pipeline -> SSE event names.
_PROGRESS_EVENT_NAMES = {
    "ticker_start": "ticker_start",
    "fetched": "fetched",
    "filing": "progress",
    "ticker_done": "ticker_completed",
}


@router.post(
    "/ingest/stream",
    summary="Ingest Form 4 data for one or more tickers via SSE",
    description=(
        "Same ingestion as /form4/ingest/batch, streamed as Server-Sent Events so the UI can show "
        "live progress. Events (each `data: {\"event\": ..., \"data\": {...}}`): `start` "
        "{tickers, total}, `ticker_start` {ticker, index, count}, `fetched` {ticker, total}, "
        "`progress` {ticker, processed, total}, `log` {message}, `ticker_completed` {result}, "
        "`completed` {tickers, results, totals, date_range}, `error` {detail}. Foreign private "
        "issuers are skipped with an explanatory message (they file no Form 4)."
    ),
)
async def ingest_form4_stream(
    request: Form4BatchIngestRequest,
    current_user: User = Depends(get_current_user),
):
    # Validated before the stream opens so bad input is a normal 400, not a stream error.
    tickers = list(dict.fromkeys(_validate_ticker(t) for t in request.tickers))

    async def event_generator():
        log_queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        token = sse_log_context.set((loop, log_queue))
        main_queue = asyncio.Queue()

        def on_progress(payload: dict):
            event = _PROGRESS_EVENT_NAMES.get(payload.get("type"))
            if event:
                data = {k: v for k, v in payload.items() if k != "type"}
                main_queue.put_nowait({"event": event, "data": data})

        async def flush_logs():
            # Let pending log callbacks land, then move anything the drainer hasn't yet, so the
            # last lines (e.g. the ingestion summary) arrive before `completed` / `error`.
            await asyncio.sleep(0)
            while not log_queue.empty():
                msg = log_queue.get_nowait()
                if msg is not None:
                    await main_queue.put({"event": "log", "data": {"message": msg}})

        async def run_ingestion():
            try:
                await main_queue.put({"event": "start", "data": {"tickers": tickers, "total": len(tickers)}})
                result = await run_form4_ingestion_multi(
                    tickers=tickers,
                    start_date=request.start_date,
                    end_date=request.end_date,
                    progress_cb=on_progress,
                )
                if result is None:
                    raise RuntimeError("Ingestion pipeline returned no result (check logs for details).")
                await flush_logs()
                await main_queue.put({"event": "completed", "data": result})
            except Exception as exc:
                logger.error(f"Form4 stream ingestion failed for {tickers}: {exc}", exc_info=True)
                await flush_logs()
                await main_queue.put({"event": "error", "data": {"detail": f"Form 4 ingestion failed: {exc}"}})
            finally:
                await main_queue.put(None)

        async def run_log_drainer():
            try:
                while True:
                    msg = await log_queue.get()
                    if msg is None:
                        break
                    await main_queue.put({"event": "log", "data": {"message": msg}})
            except asyncio.CancelledError:
                pass

        ingest_task = asyncio.create_task(run_ingestion())
        drainer_task = asyncio.create_task(run_log_drainer())

        try:
            while True:
                event = await main_queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event, default=str)}\n\n"
        finally:
            ingest_task.cancel()
            drainer_task.cancel()
            sse_log_context.reset(token)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
