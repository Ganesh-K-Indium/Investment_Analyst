import os
import re
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

class XMLSaver:
    """
    Saves raw Form 4 XML files locally for verification and auditing.
    Files are saved with descriptive names: {ticker}_{date}_{accession_id}.xml
    """
    
    def __init__(self, base_dir: str = "xml_filings"):
        """
        Initialize XML saver.
        
        Args:
            base_dir: Directory to save XML files (default: xml_filings/)
        """
        self.base_dir = base_dir
        self._ensure_directory_exists()
    
    def _ensure_directory_exists(self):
        """Create the storage directory if it doesn't exist."""
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)
            logger.info(f"Created XML storage directory: {self.base_dir}")
    
    def _sanitize_filename(self, text: str) -> str:
        """Remove invalid characters from filename."""
        return re.sub(r'[<>:"/\\|?*]', '_', text)
    
    def _extract_accession_id(self, url: str) -> str:
        """
        Extract accession number from SEC URL.
        Example: https://www.sec.gov/Archives/edgar/data/1045810/000158867026000004/wk-form4_1770415598.xml
        Returns: 000158867026000004
        """
        match = re.search(r'/(\d+)/(\d+)/', url)
        if match:
            return match.group(2)  # Return the accession number part
        return "unknown"
    
    def save_xml(self, 
                 xml_content: str, 
                 ticker: str, 
                 filing_url: str,
                 filing_date: Optional[str] = None) -> str:
        """
        Save XML content to a local file.
        
        Args:
            xml_content: The raw XML content
            ticker: Stock ticker (e.g., NVDA, MSFT)
            filing_url: The SEC URL of the filing
            filing_date: Transaction date (YYYY-MM-DD format), optional
        
        Returns:
            Full path to the saved file
        """
        try:
            # Generate filename
            accession_id = self._extract_accession_id(filing_url)
            date_str = filing_date if filing_date else datetime.now().strftime("%Y%m%d")
            
            # Format: 000158867026000004.xml (matches accession_number in DB)
            filename = f"{accession_id}.xml"
            
            # Full path
            filepath = os.path.join(self.base_dir, filename)
            
            # Save file
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(xml_content)
            
            logger.info(f"Saved XML: {filename}")
            return filepath
            
        except Exception as e:
            logger.error(f"Failed to save XML for {ticker}: {e}")
            return None
    
    def save_pdf(self, pdf_content: bytes, filing_url: str) -> str:
        """
        Save the filing document (HTML) as a local file for cross-checking.
        Named by accession number to match the XML files.
        
        Args:
            pdf_content: The raw binary content of the filing document
            filing_url: The SEC URL (used to extract accession ID)
        
        Returns:
            Full path to the saved file, or None on failure
        """
        try:
            pdf_dir = os.path.join(os.path.dirname(self.base_dir), "pdf_filings")
            if not os.path.exists(pdf_dir):
                os.makedirs(pdf_dir)
                logger.info(f"Created PDF storage directory: {pdf_dir}")
            
            accession_id = self._extract_accession_id(filing_url)
            filename = f"{accession_id}.html"
            filepath = os.path.join(pdf_dir, filename)
            
            with open(filepath, 'wb') as f:
                f.write(pdf_content)
            
            logger.info(f"Saved PDF: {filename}")
            return filepath
            
        except Exception as e:
            logger.error(f"Failed to save PDF: {e}")
            return None
    
    def get_saved_files(self, ticker: Optional[str] = None) -> list:
        """
        List all saved XML files.
        
        Args:
            ticker: Optional ticker to filter by
        
        Returns:
            List of filenames
        """
        try:
            files = os.listdir(self.base_dir)
            
            if ticker:
                files = [f for f in files if f.startswith(ticker.upper())]
            
            return sorted(files)
        except Exception as e:
            logger.error(f"Error listing XML files: {e}")
            return []
    
    def clear_old_files(self, days_old: int = 7):
        """
        Delete XML files older than specified days.
        
        Args:
            days_old: Delete files older than this many days
        """
        try:
            cutoff_time = datetime.now().timestamp() - (days_old * 86400)
            deleted_count = 0
            
            for filename in os.listdir(self.base_dir):
                filepath = os.path.join(self.base_dir, filename)
                if os.path.getmtime(filepath) < cutoff_time:
                    os.remove(filepath)
                    deleted_count += 1
            
            logger.info(f"Deleted {deleted_count} XML files older than {days_old} days")
            
        except Exception as e:
            logger.error(f"Error clearing old XML files: {e}")
