
import sys
import os
import logging
from datetime import datetime, date

# Add parent directory to path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logging.handlers import RotatingFileHandler

# Configure Logging first
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_file = 'form4_ingestion.log'
file_handler = RotatingFileHandler(log_file, maxBytes=1024*1024*5, backupCount=1)
file_handler.setFormatter(log_formatter)
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

logger = logging.getLogger("Form4Ingestion")
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

try:
    from settings import LOG_LEVEL
    from sqlalchemy import select
    from rag.utils.Insights_Form4.database import reset_db, get_db, Form4Transaction, init, init_db
    from fetch import SecEdgarFetcher
    from parse import Form4Parser
    from analytics import TransactionAnalytics
    from save_xml import XMLSaver
    logger.info("Successfully imported all project modules.")
except ImportError as e:
    logger.critical(f"Failed to import project modules: {e}")
    sys.exit(1)


def is_common_stock(security_title: str, is_derivative: bool) -> bool:
    """
    Robust filter to identify common stock transactions.
    
    Args:
        security_title: The security title from the Form 4 filing
        is_derivative: Whether this is a derivative security (from Table II)
    
    Returns:
        True if this is a common stock transaction, False otherwise
    """
    # Rule 1: Must be from Table I (non-derivative)
    if is_derivative:
        return False
    
    # Rule 2: If no security title, reject it
    if not security_title:
        return False
    
    title_lower = security_title.lower().strip()
    
    # Rule 3: EXCLUSION patterns (reject these)
    exclusions = ['preferred', 'warrant', 'option', 'note', 'convertible', 'debenture']
    for term in exclusions:
        if term in title_lower:
            logger.debug(f"Excluded security: {security_title} (contains '{term}')")
            return False
    
    # Rule 4: INCLUSION patterns (accept these)
    # Common US patterns/
    common_patterns = [
        'common stock',
        'common shares',
        'common',  # Short form (just "Common")
        'class a common',
        'class b common',
        'class c common',
        'voting common',
        'non-voting common',
        'ordinary shares',  # Foreign issuers
    ]
    
    for pattern in common_patterns:
        if pattern in title_lower:
            return True
    
    # Rule 5: If we get here, it's suspicious - log and reject
    logger.warning(f"Unrecognized security title (rejected): {security_title}")
    return False


def run_form4_ingestion(ticker=None, start_date=None, end_date=None, reset_database=False):
    """
    Fetches and ingests ALL Form 4 filings from SEC EDGAR since start_date.
    
    Args:
        ticker: Stock ticker symbol to fetch filings for (required).
        start_date: Start date for fetching filings (inclusive). Defaults to Jan 1, 2025.
        end_date: End date for fetching filings (inclusive). Defaults to today.
        reset_database: If True, resets the database before ingestion.
    """
    if not ticker:
        logger.error("No ticker or company mentioned. Please provide a ticker symbol (e.g. 'NVDA', 'AAPL').")
        print("\nERROR: No ticker or company mentioned. Usage: run_form4_ingestion(ticker='NVDA')")
        return

    if start_date is None:
        start_date = date(2026, 2, 10)
    if end_date is None:
        end_date = date.today()

    logger.info(f"Starting Ingestion - Ticker: {ticker}, Date Range: {start_date} to {end_date}")
    
    # 1. Reset Database (optional)
    if reset_database:
        try:
            reset_db()
            logger.info("Database reset successfully. All previous data has been deleted.")
        except Exception as e:
            logger.critical(f"Database reset failed: {e}")
            return
    else:
        # Check and initialize database tables if they don't exist
        try:
            init_db()
            logger.info("Database tables verified/initialized successfully.")
        except Exception as e:
            logger.critical(f"Database initialization failed: {e}")
            return

    # 2. Setup Components
    try:
        fetcher = SecEdgarFetcher()
        parser = Form4Parser()
        xml_saver = XMLSaver(base_dir="xml_filings")  # Save XML files for verification
        logger.info("Fetcher, Parser, and XML Saver initialized.")
    except Exception as e:
         logger.critical(f"Component initialization failed: {e}")
         return

    # 3. Fetch filing URLs
    logger.info(f"Fetching all filings for {ticker}...")
    
    try:
        xml_urls = fetcher.fetch_latest_filings(
            ticker=ticker, 
            start_date=start_date, 
            end_date=end_date,
            limit=500
        )
        logger.info(f"SEC returned {len(xml_urls)} valid Form 4 filing URLs total within date range {start_date} to {end_date}.")
    except Exception as e:
        logger.error(f"Failed to fetch filings from SEC: {e}")
        return

    if not xml_urls:
        logger.warning("No filings found. Exiting.")
        return

    # Initialize counters
    success_count = 0
    fail_count = 0
    skip_count = 0

    with get_db() as db:
        for i, url in enumerate(xml_urls):
            try:
                logger.info(f"[{i+1}/{len(xml_urls)}] Processing: {url}")
                
                # Extract accession number from URL for dedup check and storage
                accession_number = xml_saver._extract_accession_id(url)

                # Check for duplicate
                existing = db.execute(
                    select(Form4Transaction).where(Form4Transaction.accession_number == accession_number)
                ).scalar()
                
                if existing:
                    logger.info(f"  -> Skipping (Already in DB)")
                    skip_count += 1
                    continue

                # Fetch Content
                content = fetcher.fetch_xml_content(url)
                if not content:
                    logger.warning(f"  -> Failed to fetch XML content. Skipping.")
                    fail_count += 1
                    continue
                
                # Parse
                data = parser.parse_xml(content)
                if not data:
                    logger.warning(f"  -> Failed to parse XML. Skipping.")
                    fail_count += 1
                    continue
                
                # Save XML file locally for verification
                filing_date = None
                if data['transactions'] and data['transactions'][0].get('date'):
                    filing_date = data['transactions'][0].get('date').replace('-', '')
                xml_saver.save_xml(content, ticker, url, filing_date)
                
                # Download and save the filing document (HTML) for cross-checking
                pdf_url = fetcher.fetch_pdf_url(url)
                if pdf_url:
                    pdf_content = fetcher.fetch_pdf_content(pdf_url)
                    if pdf_content:
                        xml_saver.save_pdf(pdf_content, url)
                
                
                # Enrich and Save
                rpt_owner = data['rpt_owner_name']
                title = data['rpt_owner_title']

                tx_count = 0
                for tx in data['transactions']:
                    # Filter: Only process common stock (comprehensive validation)
                    if not is_common_stock(tx.get('security_title'), tx.get('is_derivative', False)):
                        continue
                    
                    # Calculate Value
                    val = 0.0
                    if tx['shares'] and tx['price']:
                        val = tx['shares'] * tx['price']

                    # Create DB Record
                    record = Form4Transaction(
                        accession_number=accession_number,
                        issuer_symbol=data['issuer_symbol'],
                        issuer_name=data.get('issuer_name'),
                        security_title=tx.get('security_title'),
                        rpt_owner_name=rpt_owner,
                        rpt_owner_title=title,
                        is_director=data.get('is_director', False),
                        is_officer=data.get('is_officer', False),
                        is_ten_percent_owner=data.get('is_ten_percent_owner', False),
                        transaction_date=datetime.strptime(tx['date'], '%Y-%m-%d').date() if tx.get('date') else None,
                        transaction_code=tx['code'],
                        transaction_shares=tx['shares'],
                        transaction_price_per_share=tx['price'],
                        transaction_acquired_disposed_code=tx['acq_disp'],
                        transaction_value=val,
                    )
                    db.add(record)
                    tx_count += 1
                    
                db.commit()
                logger.info(f"  -> Success. Saved {tx_count} transactions.")
                success_count += 1
                
            except Exception as e:
                logger.error(f"  -> Critical Error processing {url}: {e}")
                db.rollback()
                fail_count += 1

    logger.info("="*30)
    logger.info("INGESTION SUMMARY")
    logger.info("="*30)
    logger.info(f"Total Fetched: {len(xml_urls)}")
    logger.info(f"Successfully Saved: {success_count}")
    logger.info(f"Skipped (Duplicate): {skip_count}")
    logger.info(f"Failed: {fail_count}")
    logger.info(f"Logs saved to: {os.path.abspath(log_file)}")
    logger.info("="*30)
    
    # Calculate and display transaction analytics
    try:
        with get_db() as analytics_db:
            analytics = TransactionAnalytics(analytics_db)
            analytics.print_summary(ticker=ticker)
    except Exception as e:
        logger.error(f"Failed to calculate analytics: {e}")

if __name__ == "__main__":
    ticker_input = "GOOGL" #input("Enter ticker symbol (e.g. NVDA, AAPL, MSFT): ").strip().upper()
    run_form4_ingestion(ticker=ticker_input)
