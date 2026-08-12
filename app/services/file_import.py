"""
File import service for downloading and ingesting files from integrations
"""
import asyncio
import os
import sys
from typing import List, Dict
from sqlalchemy.ext.asyncio import AsyncSession

# Add ingestion directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ingestion'))

from app.services.connectors.base import BaseConnector
from app.services.integration import IntegrationService

# Bounds how many files ingest concurrently — each one drives OpenAI embedding
# calls and Qdrant upserts, so this isn't "as many as possible", it's "enough
# to overlap I/O wait without hammering rate limits".
_MAX_CONCURRENT_INGESTS = 3


class FileImportService:
    """Service for importing files from data source integrations"""

    @staticmethod
    async def import_files_stream(
        db: AsyncSession,
        integration_id: int,
        file_paths: List[str],
        ticker: str,
        filing_type: str = None,
        period_end_date: str = None,
        year: int = None
    ):
        """
        Import files from an integration and ingest them into the vector database,
        concurrently (bounded), so one slow/large file doesn't serialize the rest.

        Args:
            db: Database session
            integration_id: Integration ID to import from
            file_paths: List of file paths to import
            ticker: Ticker symbol for these files (e.g., AAPL, GOOGL)
            filing_type: SEC filing type - "10-K", "10-Q", or "8-K" (optional). Resolution
                order if omitted: each file's own cover-page text > filename token >
                "10-K" default with a loud warning — never a silent guess. Note this
                explicit value, if passed, applies to ALL files in this batch; leave it
                unset when importing a mixed batch of filing types and let cover-page
                detection resolve each file individually.
            period_end_date: ISO date (YYYY-MM-DD) this filing covers (optional; applies
                to ALL files in this batch if passed — leave unset for a mixed batch and
                let cover-page detection resolve each file individually)
            year: Fiscal/Report year (optional; explicit year override for metadata tagging)

        Returns:
            Async generator yielding JSON-serializable dicts with 'event' and 'data' keys.
        """
        # Get integration
        integration = await IntegrationService.get_integration(db, integration_id)
        if not integration:
            raise ValueError(f"Integration {integration_id} not found")

        # Get connector
        connector = BaseConnector.get_connector(
            vendor=integration.vendor,
            credentials=integration.credentials,
            url=integration.url
        )

        queue = asyncio.Queue()
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_INGESTS)

        yield {"event": "start", "data": {"total": len(file_paths)}}

        async def _import_one(file_path: str):
            result = {
                "file_path": file_path,
                "status": "pending",
                "success": False,
                "message": "",
                "chunks_added": None,
                "filing_type": None,
                "period_end_date": None,
                "year": None,
                "error": None
            }

            async with semaphore:
                try:
                    result["status"] = "downloading"
                    await queue.put({"event": "progress", "data": {"file": file_path, "status": "downloading"}})

                    # Connector I/O is sync — offload to a thread so it doesn't block the event loop.
                    local_path = await asyncio.to_thread(connector.download_file, file_path)
                    result["message"] = f"Downloaded to {local_path}"

                    result["status"] = "processing"
                    await queue.put({"event": "progress", "data": {"file": file_path, "status": "processing", "message": result["message"]}})

                    if not local_path.lower().endswith('.pdf'):
                        result["status"] = "failed"
                        result["error"] = "Only PDF files are currently supported"
                        result["message"] = "File type not supported"
                        await queue.put({"event": "file_completed", "data": result})
                        return result

                    try:
                        from ingestion.ingest_pdf import ingest_pdf

                        ingest_result = await ingest_pdf(
                            local_path,
                            ticker=ticker,
                            filing_type=filing_type,
                            period_end_date=period_end_date,
                            year=year
                        )

                        if ingest_result.get("success"):
                            result["status"] = "completed"
                            result["success"] = True
                            result["chunks_added"] = ingest_result.get("text_chunks", 0)
                            result["ticker"] = ticker
                            result["filing_type"] = ingest_result.get("filing_type")
                            result["period_end_date"] = ingest_result.get("period_end_date")
                            result["year"] = ingest_result.get("year")
                            result["message"] = (
                                f"Successfully ingested to ticker_{ticker.lower()} collection as "
                                f"{result['filing_type']} (period end: {result['period_end_date'] or 'unknown'}, "
                                f"year: {result['year'] or 'unknown'}). Added {result['chunks_added']} text chunks"
                            )
                        else:
                            result["status"] = "failed"
                            result["error"] = ingest_result.get("error", "Unknown error")
                            result["message"] = "Ingestion failed"

                    except Exception as ingest_error:
                        result["status"] = "failed"
                        result["error"] = f"Ingestion error: {str(ingest_error)}"
                        result["message"] = "Failed to process file"

                    try:
                        if os.path.exists(local_path):
                            os.remove(local_path)
                    except Exception:
                        pass

                except Exception as e:
                    result["status"] = "failed"
                    result["error"] = str(e)
                    result["message"] = f"Failed to import file: {str(e)}"
            
            await queue.put({"event": "file_completed", "data": result})
            return result

        # Run workers in background
        async def _run_all():
            results = await asyncio.gather(*(_import_one(path) for path in file_paths))
            # Update integration last_sync timestamp
            try:
                await IntegrationService.update_last_sync(db, integration_id)
            except Exception as e:
                print(f"Failed to update last_sync: {e}")
            await queue.put({"event": "completed", "data": {"results": results}})
            await queue.put(None) # Sentinel

        task = asyncio.create_task(_run_all())

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        except asyncio.CancelledError:
            task.cancel()
            raise


    @staticmethod
    def get_import_summary(results: List[Dict]) -> Dict:
        """
        Generate a summary of import results

        Args:
            results: List of import results

        Returns:
            Dict: Summary with total, successful, and failed counts
        """
        total = len(results)
        successful = sum(1 for r in results if r["success"])
        failed = total - successful

        return {
            "total_files": total,
            "successful": successful,
            "failed": failed,
            "file_results": results
        }
