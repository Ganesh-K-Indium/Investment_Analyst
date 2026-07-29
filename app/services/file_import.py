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
    async def import_files(
        db: AsyncSession,
        integration_id: int,
        file_paths: List[str],
        ticker: str,
        filing_type: str = None
    ) -> List[Dict]:
        """
        Import files from an integration and ingest them into the vector database,
        concurrently (bounded), so one slow/large file doesn't serialize the rest.

        Args:
            db: Database session
            integration_id: Integration ID to import from
            file_paths: List of file paths to import
            ticker: Ticker symbol for these files (e.g., AAPL, GOOGL)
            filing_type: SEC filing type - "10-K", "10-Q", or "8-K" (optional; auto-detected
                from each file's name if omitted, defaulting to "10-K" if no token is found)

        Returns:
            List[Dict]: List of import results for each file, in the same order as file_paths
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

        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_INGESTS)

        async def _import_one(file_path: str) -> Dict:
            result = {
                "file_path": file_path,
                "status": "pending",
                "success": False,
                "message": "",
                "chunks_added": None,
                "error": None
            }

            async with semaphore:
                try:
                    result["status"] = "downloading"

                    # Connector I/O is sync — offload to a thread so it doesn't block the event loop.
                    local_path = await asyncio.to_thread(connector.download_file, file_path)
                    result["message"] = f"Downloaded to {local_path}"

                    result["status"] = "processing"

                    if not local_path.lower().endswith('.pdf'):
                        result["status"] = "failed"
                        result["error"] = "Only PDF files are currently supported"
                        result["message"] = "File type not supported"
                        return result

                    try:
                        from ingestion.ingest_pdf import ingest_pdf

                        ingest_result = await ingest_pdf(local_path, ticker=ticker, filing_type=filing_type)

                        if ingest_result.get("success"):
                            result["status"] = "completed"
                            result["success"] = True
                            result["chunks_added"] = ingest_result.get("text_chunks", 0)
                            result["ticker"] = ticker
                            result["message"] = f"Successfully ingested to ticker_{ticker.lower()} collection. Added {result['chunks_added']} text chunks"
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

            return result

        results = await asyncio.gather(*(_import_one(path) for path in file_paths))

        # Update integration last_sync timestamp
        await IntegrationService.update_last_sync(db, integration_id)

        return list(results)

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
