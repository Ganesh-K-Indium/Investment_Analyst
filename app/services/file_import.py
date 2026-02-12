"""
File import service for downloading and ingesting files from integrations
"""
import os
import sys
from typing import List, Dict, Tuple
from sqlalchemy.orm import Session

# Add ingestion directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ingestion'))

from app.database.models import Integration
from app.services.connectors.base import BaseConnector
from app.services.integration import IntegrationService


class FileImportService:
    """Service for importing files from data source integrations"""
    
    @staticmethod
    def import_files(
        db: Session,
        integration_id: int,
        file_paths: List[str],
        ticker: str
    ) -> List[Dict]:
        """
        Import files from an integration and ingest them into the vector database

        Args:
            db: Database session
            integration_id: Integration ID to import from
            file_paths: List of file paths to import
            ticker: Ticker symbol for these files (e.g., AAPL, GOOGL)

        Returns:
            List[Dict]: List of import results for each file
        """
        # Get integration
        integration = IntegrationService.get_integration(db, integration_id)
        if not integration:
            raise ValueError(f"Integration {integration_id} not found")
        
        # Get connector
        connector = BaseConnector.get_connector(
            vendor=integration.vendor,
            credentials=integration.credentials,
            url=integration.url
        )
        
        results = []
        
        for file_path in file_paths:
            result = {
                "file_path": file_path,
                "status": "pending",
                "success": False,
                "message": "",
                "chunks_added": None,
                "error": None
            }
            
            try:
                # Update status to downloading
                result["status"] = "downloading"
                
                # Download file from connector
                local_path = connector.download_file(file_path)
                result["message"] = f"Downloaded to {local_path}"
                
                # Update status to processing
                result["status"] = "processing"
                
                # Check file extension - currently only PDF supported
                if not local_path.lower().endswith('.pdf'):
                    result["status"] = "failed"
                    result["error"] = "Only PDF files are currently supported"
                    result["message"] = "File type not supported"
                    results.append(result)
                    continue
                
                # Import the PDF processing function
                try:
                    from ingestion.ingest_pdf import ingest_pdf

                    # Process the file with ticker
                    ingest_result = ingest_pdf(local_path, ticker=ticker)

                    # Parse result
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
                
                # Clean up temporary file
                try:
                    if os.path.exists(local_path):
                        os.remove(local_path)
                except:
                    pass
                
            except Exception as e:
                result["status"] = "failed"
                result["error"] = str(e)
                result["message"] = f"Failed to import file: {str(e)}"
            
            results.append(result)
        
        # Update integration last_sync timestamp
        IntegrationService.update_last_sync(db, integration_id)
        
        return results
    
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
