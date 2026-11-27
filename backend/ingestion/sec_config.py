import logging
from app.core.config import get_settings


# General Settings
LOG_LEVEL = get_settings().log_level
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# SEC EDGAR Settings
SEC_USER_AGENT = get_settings().sec_user_agent
SEC_REQUEST_RATE_LIMIT = 10  # Requests per second

# SEC Configuration
SEC_BASE_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
EDGAR_SEARCH_URL = "https://www.sec.gov/edgar/searchedgar/companysearch.html"
EDGAR_FILINGS_URL = "https://www.sec.gov/Archives/edgar/data"
