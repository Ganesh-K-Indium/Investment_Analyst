import requests
import time
import logging
from typing import List, Optional
from settings import SEC_USER_AGENT, SEC_REQUEST_RATE_LIMIT, SEC_BASE_URL

logger = logging.getLogger(__name__)

class SecEdgarFetcher:
    """
    Fetches Form 4 filings from SEC EDGAR.
    Respects the rate limit of 10 requests per second.
    """
    
    BASE_URL = SEC_BASE_URL
    ARCHIVE_URL = "https://www.sec.gov/Archives"
    
    def __init__(self):
        self.headers = {
            "User-Agent": SEC_USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
            "Host": "www.sec.gov"
        }
        self.last_request_time = 0
        self.rate_limit_delay = 1.0 / SEC_REQUEST_RATE_LIMIT

    def _wait_for_rate_limit(self):
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - time_since_last)
        self.last_request_time = time.time()

    def _get_cik_from_ticker(self, ticker: str) -> Optional[str]:
        """
        Converts a ticker symbol to a CIK number using SEC's company tickers JSON.
        Returns the CIK with leading zeros (10 digits) or None if not found.
        """
        try:
            self._wait_for_rate_limit()
            response = requests.get(
                'https://www.sec.gov/files/company_tickers.json',
                headers=self.headers,
                timeout=10
            )
            response.raise_for_status()
            
            companies = response.json()
            ticker_upper = ticker.upper()
            
            # Search through the companies
            for company_data in companies.values():
                if company_data.get('ticker', '').upper() == ticker_upper:
                    cik = company_data.get('cik_str')
                    # Pad CIK to 10 digits with leading zeros
                    cik_padded = str(cik).zfill(10)
                    logger.info(f"Converted ticker '{ticker}' to CIK '{cik_padded}'")
                    return cik_padded
            
            logger.warning(f"Could not find CIK for ticker '{ticker}'")
            return None
            
        except Exception as e:
            logger.error(f"Error converting ticker to CIK: {e}")
            return None

    def fetch_latest_filings(self, ticker: Optional[str] = None, limit: Optional[int] = None) -> List[str]:
        """
        Fetches ALL available Form 4 filing URLs with pagination.
        SEC EDGAR returns max ~40 results per page, so we paginate until no more data.
        
        Returns a list of URLs to the XML files (latest to oldest).
        """
        PAGE_SIZE = 40  # Fetch 40 per page for efficiency
        
        # Convert ticker to CIK once
        cik = None
        if ticker:
            cik = self._get_cik_from_ticker(ticker)
            if not cik:
                logger.error(f"Failed to convert ticker '{ticker}' to CIK. Returning empty results.")
                return []
        
        all_xml_urls = []
        start = 0
        
        while True:
            # Check if we already have enough (if limit is set)
            if limit and len(all_xml_urls) >= limit:
                logger.info(f"Reached fetch limit of {limit}. Stopping pagination.")
                break

            params = {
                'action': 'getcurrent' if not ticker else 'getcompany',
                'type': '4',
                'company': '',
                'dateb': '',
                'owner': 'include',
                'start': str(start),
                'count': str(PAGE_SIZE),
                'output': 'atom'
            }
            
            if cik:
                params['CIK'] = cik
            
            self._wait_for_rate_limit()
            try:
                response = requests.get(self.BASE_URL, params=params, headers=self.headers)
                response.raise_for_status()
                
                batch_urls = self._extract_xml_links_from_atom(response.text)
                
                if not batch_urls:
                    logger.info(f"No more filings found. Total fetched: {len(all_xml_urls)}")
                    break
                
                all_xml_urls.extend(batch_urls)
                start += len(batch_urls)
                logger.info(f"Fetched page (start={start - len(batch_urls)}): got {len(batch_urls)} filings. Total so far: {len(all_xml_urls)}")
                
            except requests.RequestException as e:
                logger.error(f"Error fetching filings (page start={start}): {e}")
                break
        
        return all_xml_urls

    def _extract_xml_links_from_atom(self, atom_content: str) -> List[str]:
        """
        Extracts the link to the filing index from the ATOM feed, 
        then fetches that index to find the primary XML file.
        """
        import xml.etree.ElementTree as ET
        
        links = []
        try:
            # Remove namespace for easier parsing
            atom_content = atom_content.replace('xmlns="http://www.w3.org/2005/Atom"', '')
            root = ET.fromstring(atom_content)
            
            entries = root.findall('entry')
            for entry in entries:
                link_node = entry.find('link')
                if link_node is not None:
                    index_url = link_node.get('href')
                    # Now we need to fetch the index page to find the .xml file
                    # This is expensive (N+1 requests), but necessary for SEC structure
                    xml_url = self._get_primary_document_url(index_url)
                    if xml_url:
                        links.append(xml_url)
                        
        except Exception as e:
            logger.error(f"Error parsing ATOM feed: {e}")
            
        return links

    def _get_primary_document_url(self, index_url: str) -> Optional[str]:
        """
        Fetches the index page and finds the primary XML document link.
        """
        self._wait_for_rate_limit()
        try:
            response = requests.get(index_url, headers=self.headers)
            response.raise_for_status()
            
            # Helper to get filename from URL
            current_filename = index_url.split('/')[-1]
            
            # Improved logic: Find all XML links and pick the one that looks like a Form 4
            import re
            links = re.findall(r'href="([^"]+\.xml)"', response.text)
            
            xml_url = None
            for link in links:
                # Avoid summary files or other metadata XMLs
                if any(x in link.lower() for x in ['summary', 'recipient', 'submission', 'xsl']):
                    continue
                # Avoid self-reference (the index page itself if it ends in .xml)
                if link.endswith(current_filename):
                    continue
                    
                xml_url = link
                break
            
            if not xml_url and links:
                 # Fallback: try to find any xml that isn't the index or metadata
                 for link in links:
                    if not link.endswith(current_filename) and 'xsl' not in link.lower():
                        xml_url = link
                        break
                
            if xml_url:
                # handle relative path
                if not xml_url.startswith('http'):
                    # if it starts with /, append to authority, else append to current path
                    if xml_url.startswith('/'):
                        return f"https://www.sec.gov{xml_url}"
                    else:
                        base_url = index_url.rsplit('/', 1)[0]
                        return f"{base_url}/{xml_url}"
                return xml_url
                
        except Exception as e:
            logger.error(f"Error fetching index page {index_url}: {e}")
            return None
        return None

    def fetch_xml_content(self, url: str) -> Optional[str]:
        logger.info(f"Fetching XML from: {url}")
        self._wait_for_rate_limit()
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            if '<html' in response.text.lower()[:500]:
                logger.error(f"Error: Fetched HTML instead of XML from {url}")
                return None
            return response.text
        except requests.RequestException as e:
            logger.error(f"Error fetching XML content from {url}: {e}")
            return None

    def fetch_pdf_url(self, xml_url: str) -> Optional[str]:
        """
        Given an XML filing URL, constructs the human-readable filing index URL.
        SEC naming convention: {accession-number}-index.html
        
        Example:
          XML:   .../000078901926000014/form4.xml
          Index: .../000078901926000014/0000789019-26-000014-index.html
        """
        import re
        base_url = xml_url.rsplit('/', 1)[0]
        
        # Extract accession number from URL path (18 digits without dashes)
        acc_match = re.search(r'/(\d{18})/', xml_url)
        if not acc_match:
            logger.warning(f"Could not extract accession number from: {xml_url}")
            return None
        
        acc_raw = acc_match.group(1)  # e.g., "000078901926000014"
        # Convert to SEC dash format: 0000789019-26-000014
        acc_formatted = f"{acc_raw[:10]}-{acc_raw[10:12]}-{acc_raw[12:]}"
        
        # The human-readable index is always at this path
        index_url = f"{base_url}/{acc_formatted}-index.html"
        return index_url



    def fetch_pdf_content(self, url: str) -> Optional[bytes]:
        """Downloads the filing document (HTML rendered as PDF-like) as binary content."""
        logger.info(f"Fetching filing doc from: {url}")
        self._wait_for_rate_limit()
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            return response.content
        except requests.RequestException as e:
            logger.error(f"Error fetching filing doc from {url}: {e}")
            return None
