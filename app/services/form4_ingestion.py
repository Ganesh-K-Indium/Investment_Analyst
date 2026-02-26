"""
Form 4 Ingestion Service

Fetches and ingests SEC Form 4 insider trading filings from EDGAR
for a given ticker into the main application database (portfolios.db).
"""
import sys
import os
import re
import logging
from datetime import datetime, date
from typing import Optional

from sqlalchemy import select

# Add ingestion/Form4 Ingestion/ to sys.path so we can import fetch.py and parse.py.
# settings.py (imported by fetch.py) lives at the project root, which is already on
# sys.path when the FastAPI app runs.
_form4_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "ingestion", "Form4 Ingestion"
)
if _form4_dir not in sys.path:
    sys.path.insert(0, _form4_dir)

from fetch import SecEdgarFetcher  # noqa: E402
from parse import Form4Parser       # noqa: E402

from app.database.connection import get_db
from app.database.models import Form4Transaction

logger = logging.getLogger(__name__)


def _is_common_stock(security_title: str, is_derivative: bool) -> bool:
    """Return True only for non-derivative common stock transactions."""
    if is_derivative:
        return False
    if not security_title:
        return False

    title_lower = security_title.lower().strip()

    exclusions = ['preferred', 'warrant', 'option', 'note', 'convertible', 'debenture']
    for term in exclusions:
        if term in title_lower:
            return False

    common_patterns = [
        'common stock', 'common shares', 'common',
        'class a common', 'class b common', 'class c common',
        'voting common', 'non-voting common', 'ordinary shares',
    ]
    for pattern in common_patterns:
        if pattern in title_lower:
            return True

    logger.warning(f"Unrecognized security title (rejected): {security_title}")
    return False


def run_form4_ingestion(
    ticker: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> dict:
    """
    Fetch and ingest Form 4 filings from SEC EDGAR for *ticker*.

    Filings are stored in the main application database (portfolios.db).
    Duplicate filings (same accession number) are skipped automatically.

    Args:
        ticker:     Uppercase stock ticker symbol, e.g. 'NVDA'.
        start_date: Earliest filing date to store (inclusive). Defaults to 2025-01-01.
        end_date:   Latest filing date to store (inclusive). Defaults to today.

    Returns:
        Summary dict: ticker, total_fetched, saved, skipped_duplicate, failed, date_range.
    """
    if not ticker:
        raise ValueError("Ticker symbol is required.")

    ticker = ticker.upper().strip()

    if start_date is None:
        start_date = date(2025, 1, 1)
    if end_date is None:
        end_date = date.today()

    logger.info(f"Form4 ingestion starting — ticker={ticker}, range={start_date}→{end_date}")

    fetcher = SecEdgarFetcher()
    parser = Form4Parser()

    # ------------------------------------------------------------------ #
    # 1. Fetch all filing XML URLs from SEC EDGAR (paginated)             #
    # ------------------------------------------------------------------ #
    xml_urls = fetcher.fetch_latest_filings(ticker=ticker)

    if not xml_urls:
        logger.warning(f"No Form 4 filings found on SEC EDGAR for '{ticker}'.")
        return {
            "ticker": ticker,
            "total_fetched": 0,
            "saved": 0,
            "skipped_duplicate": 0,
            "failed": 0,
            "date_range": {"start": str(start_date), "end": str(end_date)},
            "message": f"No Form 4 filings found on SEC EDGAR for '{ticker}'.",
        }

    logger.info(f"Found {len(xml_urls)} filing URLs for {ticker}. Processing…")

    saved = 0
    skipped = 0
    failed = 0

    # ------------------------------------------------------------------ #
    # 2. Process each filing URL                                          #
    # ------------------------------------------------------------------ #
    with get_db() as db:
        for i, url in enumerate(xml_urls):
            try:
                # --- Extract accession number (18 digits, no dashes) ----
                acc_match = re.search(r'/(\d{18})/', url)
                if not acc_match:
                    logger.warning(f"[{i+1}/{len(xml_urls)}] Cannot extract accession number: {url}")
                    failed += 1
                    continue

                accession_number = acc_match.group(1)

                # --- Deduplication check --------------------------------
                existing = db.execute(
                    select(Form4Transaction).where(
                        Form4Transaction.accession_number == accession_number
                    )
                ).scalar()

                if existing:
                    logger.debug(f"[{i+1}] Duplicate accession {accession_number}, skipping.")
                    skipped += 1
                    continue

                # --- Fetch XML content ----------------------------------
                content = fetcher.fetch_xml_content(url)
                if not content:
                    logger.warning(f"[{i+1}] Empty XML content for {url}")
                    failed += 1
                    continue

                # --- Parse XML ------------------------------------------
                data = parser.parse_xml(content)
                if not data:
                    logger.warning(f"[{i+1}] Failed to parse XML from {url}")
                    failed += 1
                    continue

                # --- Date filtering ------------------------------------
                period_of_report = data.get('period_of_report', '')
                if period_of_report:
                    try:
                        filing_date_obj = datetime.strptime(period_of_report, '%Y-%m-%d').date()
                        if filing_date_obj < start_date:
                            logger.info(
                                f"[{i+1}] Filing date {filing_date_obj} < start {start_date}. "
                                "Stopping pagination (filings are newest-first)."
                            )
                            break
                        if filing_date_obj > end_date:
                            logger.info(f"[{i+1}] Filing date {filing_date_obj} > end {end_date}. Skipping.")
                            skipped += 1
                            continue
                    except ValueError:
                        pass  # Malformed date — proceed anyway

                # --- Persist common-stock transactions ------------------
                tx_saved = 0
                for tx in data.get('transactions', []):
                    if not _is_common_stock(tx.get('security_title'), tx.get('is_derivative', False)):
                        continue

                    val = 0.0
                    if tx.get('shares') and tx.get('price'):
                        val = float(tx['shares']) * float(tx['price'])

                    record = Form4Transaction(
                        accession_number=accession_number,
                        issuer_symbol=data['issuer_symbol'],
                        issuer_name=data.get('issuer_name'),
                        security_title=tx.get('security_title'),
                        rpt_owner_name=data['rpt_owner_name'],
                        rpt_owner_title=data.get('rpt_owner_title'),
                        is_director=data.get('is_director', False),
                        is_officer=data.get('is_officer', False),
                        is_ten_percent_owner=data.get('is_ten_percent_owner', False),
                        transaction_date=(
                            datetime.strptime(tx['date'], '%Y-%m-%d').date()
                            if tx.get('date') else None
                        ),
                        transaction_code=tx.get('code'),
                        transaction_shares=tx.get('shares'),
                        transaction_price_per_share=tx.get('price'),
                        transaction_acquired_disposed_code=tx.get('acq_disp'),
                        transaction_value=val,
                    )
                    db.add(record)
                    tx_saved += 1

                db.commit()

                if tx_saved > 0:
                    saved += 1
                    logger.info(f"[{i+1}] Saved {tx_saved} transaction(s) from {accession_number}.")
                else:
                    logger.info(f"[{i+1}] No common-stock transactions in {accession_number}.")

            except Exception as exc:
                logger.error(f"[{i+1}] Error processing {url}: {exc}", exc_info=True)
                db.rollback()
                failed += 1

    logger.info(
        f"Form4 ingestion complete for {ticker}: "
        f"saved={saved}, skipped={skipped}, failed={failed}"
    )

    return {
        "ticker": ticker,
        "total_fetched": len(xml_urls),
        "saved": saved,
        "skipped_duplicate": skipped,
        "failed": failed,
        "date_range": {"start": str(start_date), "end": str(end_date)},
    }
