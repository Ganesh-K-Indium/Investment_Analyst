"""
advisory_hub.py
---------------
The core logic for the Investment Bot's Advisory Pipeline (Read-Only).

This module provides a function `get_advisory_report(ticker)` that:
1.  Connects to the database (Postgres/SQLite via DATABASE_URL).
2.  Fetches existing transactions for the given ticker.
3.  Runs the advisory analysis using the LLM.
4.  Returns the structured report object.

It does NOT trigger data ingestion.
"""

import sys
import logging
from collections import defaultdict
import os

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Ensure project root is on sys.path for app.* imports via database.py
_project_root = os.path.dirname(
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
    )
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Ensure this file's directory is on sys.path for advisory_analyst import
_this_dir = os.path.dirname(os.path.abspath(__file__))
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)

from sqlalchemy import text
from database import get_db, Form4Transaction
from advisory_analyst import analyze_transactions
from datetime import date, timedelta

# Configure logging to be minimal/appropriate for a library function
logger = logging.getLogger(__name__)

def build_relationship_list(record: Form4Transaction) -> list:
    """Convert boolean DB flags to a list of role strings for the analyst."""
    roles = []
    if record.is_officer:
        roles.append("Officer")
    if record.is_director:
        roles.append("Director")
    if record.is_ten_percent_owner:
        roles.append("10% Owner")
    if not roles:
        roles.append("Other")
    return roles

def _normalize_name(name: str) -> str:
    """Normalise insider names so 'HENNESSY JOHN L' and 'Hennessy John L.' merge."""
    if not name:
        return ""
    return name.upper().strip().rstrip('.').replace('  ', ' ')

async def fetch_data_for_ticker(ticker: str, start_date: date = None, end_date: date = None) -> list:
    """
    Fetches deduplicated transactions for a ticker using SQL GROUP BY — identical
    logic to check_form4.py so the numbers always agree with the verification script.

    Deduplication: SQL groups by (rpt_owner_name, date, code, ad_code, shares, price).
    Name merging : after SQL dedup, rows are grouped in Python by normalised name so
                   variant spellings (HENNESSY JOHN L / Hennessy John L.) merge into
                   one insider entry.
    """
    ticker = ticker.upper()

    params: dict = {"ticker": ticker}
    date_filter = ""
    if start_date:
        date_filter += " AND transaction_date >= :start_date"
        params["start_date"] = start_date
    if end_date:
        date_filter += " AND transaction_date <= :end_date"
        params["end_date"] = end_date

    sql = text(f"""
        SELECT
            rpt_owner_name,
            issuer_name,
            issuer_symbol,
            rpt_owner_title,
            is_director,
            is_officer,
            is_ten_percent_owner,
            transaction_date,
            transaction_code,
            transaction_acquired_disposed_code AS ad_code,
            transaction_shares,
            transaction_price_per_share       AS price
        FROM form4_transactions
        WHERE UPPER(issuer_symbol) = UPPER(:ticker)
        {date_filter}
        GROUP BY
            rpt_owner_name,
            issuer_name,
            issuer_symbol,
            rpt_owner_title,
            is_director,
            is_officer,
            is_ten_percent_owner,
            transaction_date,
            transaction_code,
            transaction_acquired_disposed_code,
            transaction_shares,
            transaction_price_per_share
        ORDER BY transaction_date ASC, rpt_owner_name
    """)

    async with get_db() as db:
        result = await db.execute(sql, params)
        rows = result.fetchall()

    if not rows:
        logger.warning(f"No transactions found in DB for ticker '{ticker}'.")
        return []

    # Group by (issuer_name, normalised_owner_name) so variant spellings merge
    grouped = defaultdict(lambda: {
        "issuer_name": None,
        "ticker": ticker,
        "reporting_person_name": None,
        "relationship": [],
        "transactions": []
    })

    for row in rows:
        norm_name = _normalize_name(row.rpt_owner_name)
        key = (row.issuer_name or ticker, norm_name)
        entry = grouped[key]
        entry["issuer_name"] = row.issuer_name or ticker

        # Prefer the longest/most readable name variant
        current = entry.get("reporting_person_name") or ""
        if len(row.rpt_owner_name or "") > len(current):
            entry["reporting_person_name"] = row.rpt_owner_name

        # Build relationship from flags
        roles = []
        if row.is_officer:   roles.append("Officer")
        if row.is_director:  roles.append("Director")
        if row.is_ten_percent_owner: roles.append("10% Owner")
        if not roles:        roles.append("Other")
        entry["relationship"] = roles

        if row.transaction_date and row.transaction_code and row.transaction_shares is not None:
            price = row.price or 0.0
            price_str = (
                f"${price:,.2f}"
                if price and float(price) > 0
                else "N/A (grant/vest/exercise)"
            )
            entry["transactions"].append({
                "date": str(row.transaction_date),
                "code": row.transaction_code or "",
                "amount": str(int(row.transaction_shares)),
                "price": price_str,
                "acquired_disposed": row.ad_code or "A"
            })

    return list(grouped.values())

async def get_advisory_report(ticker: str, start_date: date = None, end_date: date = None) -> dict:
    """
    Main entry point for the Advisory Pipeline.
    
    Args:
        ticker (str): The stock ticker symbol (e.g., 'NVDA').
        start_date (date): The start date for analysis.
        end_date (date): The end date for analysis.
        
    Returns:
        dict: The advisory report generated by the LLM, or an error/status message.
              Structure: { 'IssuerName': { 'Recommendation': '...', ... } }
    """
    if not ticker:
        return {"error": "No ticker provided."}
    
    ticker = ticker.upper().strip()
    
    if not end_date:
        end_date = date.today()

    # 1. Fetch Data (Read-Only)
    data = await fetch_data_for_ticker(ticker, start_date=start_date, end_date=end_date)
    
    if not data:
        return {
            "status": "no_data",
            "message": f"No insider trading data found for {ticker} in the database.",
            "ticker": ticker
        }
        
    # 2. Run Analysis
    try:
        report = analyze_transactions(data)
        return report
    except Exception as e:
        logger.error(f"Analysis failed for {ticker}: {e}")
        return {"error": f"Analysis failed: {str(e)}"}
def print_report(ticker: str, report: dict):
    """Prints the advisory report in the exact required format."""
    logger.info("\n--- Running Advisory Analysis --- \n")

    if "error" in report:
        logger.error(f"ERROR: {report['error']}")
        return

    if report.get("status") == "no_data":
        logger.warning(f"NOTICE: {report['message']}")
        logger.warning("To ingest data, please run the separate ingestion pipeline: python ingest.py")
        return

    logger.info("=== Analyst Report === \n")
    
    for issuer, detail in report.items():
        if issuer in ["error", "status", "message"]: continue
        
        # print(f"Issuer: {issuer} [ {ticker} ] \n")
        # print(f"Recommendation: {detail.get('Recommendation', 'N/A')} \n")
        # print(f"Net Insider Flow (Shares): {detail.get('Net_Inside_Flow', 0):,.0f} \n")
        # print(f"Net Insider Flow (Dollars): ${detail.get('Net_Cash_Flow', 0):,.2f} \n")
        # print(f"Transaction Count: {detail.get('Transaction_Count', 0)} \n")
        
        # print("  --- Acquired Metrics --- \n")
        # print(f"Total Acquired (Shares): {detail.get('Total_Acquired_Shares', 0):,.0f} \n")
        # print(f"Total Acquired (Dollars): ${detail.get('Total_Bought', 0):,.2f} \n")
        # print(f"Transactions (Acquired): {detail.get('Acquired_Txn_Count', 0)} \n")
        # print(f"Average Acquired Price: ${detail.get('Avg_Acquired_Price', 0):,.2f} \n")
        
        # print("  --- Disposed Metrics --- \n")
        # print(f"Total Disposed (Shares): {detail.get('Total_Disposed_Shares', 0):,.0f} \n")
        # print(f"Total Disposed (Dollars): ${detail.get('Total_Sold', 0):,.2f} \n")
        # print(f"Transactions (Disposed): {detail.get('Disposed_Txn_Count', 0)} \n")
        # print(f"Average Disposed Price: ${detail.get('Avg_Disposed_Price', 0):,.2f} \n")
        logger.info("  --- AI Analysis --- \n")
        reason = detail.get('Reason', 'No analysis available')
        logger.info(f"{reason}\n")

if __name__ == "__main__":
    import asyncio
    import sys

    ticker = "GOOGL"
    if not ticker:
        logger.warning("No ticker provided.")
        sys.exit(1)

    result = asyncio.run(get_advisory_report(ticker))
    print_report(ticker, result)
