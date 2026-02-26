import os
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# General Settings
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# SEC EDGAR Settings
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "Indium Capital contact@indium.com")
SEC_REQUEST_RATE_LIMIT = 10  # Requests per second

# SEC Configuration
SEC_BASE_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
EDGAR_SEARCH_URL = "https://www.sec.gov/edgar/searchedgar/companysearch.html"
EDGAR_FILINGS_URL = "https://www.sec.gov/Archives/edgar/data"
