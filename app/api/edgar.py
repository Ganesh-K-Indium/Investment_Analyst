"""
SEC EDGAR Filing Ingestion Endpoints

POST /edgar/list — fetch list of 10-K/10-Q/8-K filings for a ticker directly from SEC EDGAR.
POST /edgar/ingest — fetch and ingest SEC filings (via SSE streaming).
GET /edgar/file/{file_path} — serve SEC filing PDF file
"""
import logging
import re
import json
import asyncio
from datetime import date
from typing import List, Optional
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse, FileResponse
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

@router.get(
    "/file/{file_path:path}",
    summary="Serve SEC filing PDF file",
)
async def get_sec_filing_pdf(
    file_path: str,
    current_user: User = Depends(get_current_user),
):
    """
    Serve SEC filing PDFs with authentication.
    File path can be just the filename or include sec_filings/ prefix.
    """
    # Security: prevent directory traversal attacks
    if ".." in file_path or file_path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Strip sec_filings/ prefix if present (frontend may include it)
    if file_path.startswith("sec_filings/"):
        file_path = file_path[len("sec_filings/"):]

    file_path_obj = Path("sec_filings") / file_path

    # Ensure file exists and is within sec_filings directory
    try:
        file_path_obj = file_path_obj.resolve()
        sec_filings_dir = Path("sec_filings").resolve()

        if not file_path_obj.is_relative_to(sec_filings_dir):
            raise HTTPException(status_code=403, detail="Access denied")

        # If file doesn't exist and path doesn't have a directory component,
        # try to find it in ticker subdirectories
        if not file_path_obj.exists() and "/" not in file_path:
            # Extract ticker from filename (e.g., GOOGL_10-K_... -> GOOGL)
            ticker_candidate = file_path.split("_")[0].upper()
            alternative_path = sec_filings_dir / ticker_candidate / file_path
            if alternative_path.exists() and alternative_path.is_file():
                file_path_obj = alternative_path

        if not file_path_obj.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

        if not file_path_obj.is_file():
            raise HTTPException(status_code=400, detail="Not a file")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Return PDF file with appropriate headers
    return FileResponse(
        file_path_obj,
        media_type="application/pdf",
        filename=file_path_obj.name
    )
