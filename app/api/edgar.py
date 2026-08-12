"""
SEC EDGAR Filing Ingestion Endpoints

POST /edgar/list — fetch list of 10-K/10-Q/8-K filings for a ticker directly from SEC EDGAR.
POST /edgar/ingest — fetch and ingest SEC filings (via SSE streaming).
"""
import logging
import re
import json
import asyncio
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.database.models import User
from app.auth.deps import get_current_user
from app.services.sec_edgar import SecEdgarService
from app.utils.log_capture import sse_log_context

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

class EdgarListRequest(BaseModel):
    ticker: str = Field(..., description="Stock ticker symbol (e.g. AAPL). Must be 1–5 uppercase letters.")
    form_types: Optional[List[str]] = Field(["10-K", "10-Q", "8-K"], description="SEC form types to fetch.")
    start_date: Optional[date] = Field(None, description="Only include filings on/after this date (YYYY-MM-DD).")
    end_date: Optional[date] = Field(None, description="Only include filings on/before this date (YYYY-MM-DD).")

class EdgarIngestRequest(BaseModel):
    ticker: str = Field(..., description="Stock ticker symbol (e.g. AAPL). Must be 1–5 uppercase letters.")
    form_types: Optional[List[str]] = Field(["10-K", "10-Q", "8-K"], description="SEC form types to fetch.")
    accessions: Optional[List[str]] = Field(None, description="Specific accession numbers to ingest. If None, ingests all matching filters.")
    start_date: Optional[date] = Field(None, description="Only include filings on/after this date (YYYY-MM-DD).")
    end_date: Optional[date] = Field(None, description="Only include filings on/before this date (YYYY-MM-DD).")

# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/list",
    summary="List SEC filings (10-K/10-Q/8-K) for a ticker",
)
async def list_edgar_filings(
    request: EdgarListRequest,
    current_user: User = Depends(get_current_user),
):
    ticker = _validate_ticker(request.ticker)
    try:
        filings = await SecEdgarService.list_filings(
            ticker=ticker,
            form_types=request.form_types or ["10-K", "10-Q", "8-K"],
            start_date=request.start_date,
            end_date=request.end_date
        )
        return {"filings": filings}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"EDGAR list failed for {ticker}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"EDGAR list failed for '{ticker}': {exc}",
        )

@router.post(
    "/ingest",
    summary="Fetch and ingest SEC filings (10-K/10-Q/8-K) for a ticker via SSE",
)
async def ingest_edgar_filings(
    request: EdgarIngestRequest,
    current_user: User = Depends(get_current_user),
):
    ticker = _validate_ticker(request.ticker)

    async def event_generator():
        log_queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        token = sse_log_context.set((loop, log_queue))
        main_queue = asyncio.Queue()

        async def run_ingestion():
            try:
                async for event in SecEdgarService.fetch_filings_stream(
                    ticker=ticker,
                    form_types=request.form_types or ["10-K", "10-Q", "8-K"],
                    start_date=request.start_date,
                    end_date=request.end_date,
                    accession_filter=set(request.accessions) if request.accessions else None,
                    ingest=True,
                ):
                    await main_queue.put(event)
            except ValueError as exc:
                await main_queue.put({"event": "error", "data": {"detail": str(exc)}})
            except Exception as exc:
                logger.error(f"EDGAR ingestion failed for {ticker}: {exc}", exc_info=True)
                await main_queue.put({"event": "error", "data": {"detail": f"EDGAR ingestion failed: {str(exc)}"}})
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
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            ingest_task.cancel()
            drainer_task.cancel()
            sse_log_context.reset(token)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
