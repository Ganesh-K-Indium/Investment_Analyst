#!/usr/bin/env python3
"""
Simple PDF Ingestion Script

Usage:
    python ingest_pdf.py <pdf_file_path> [ticker]

Example:
    python ingest_pdf.py /path/to/apple_10k.pdf AAPL
"""

import sys
import os
import argparse
import logging
from pathlib import Path

# Add project root directory to path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.insert(0, project_root)
# Add ingestion directory to path
sys.path.insert(0, current_dir)

from ingestion.pdf_processor1 import process_pdf_and_get_result

logger = logging.getLogger("ingestion.ingest_pdf")


def format_result(result: dict) -> str:
    """Format the processing result for display."""
    lines = [
        "\n" + "="*60,
        "PDF INGESTION RESULT",
        "="*60,
        f"File: {result['file_name']}",
        f"Ticker: {result.get('ticker', 'N/A')}",
        f"Filing type: {result.get('filing_type', 'N/A')}",
        f"Period end date: {result.get('period_end_date') or 'N/A'}",
        f"Status: {'✓ SUCCESS' if result['success'] else '✗ FAILED'}",
        ""
    ]
    
    if result['error']:
        lines.append(f"Error: {result['error']}")
    else:
        lines.extend([
            "TEXT PROCESSING:",
            f"  - Processed: {result['text_processed']}",
            f"  - Already existed: {result['text_already_existed']}",
            f"  - Chunks added: {result['text_chunks']}",
            "",
            "IMAGE PROCESSING:",
            f"  - Processed: {result['images_processed']}",
            f"  - Already existed: {result['images_already_existed']}",
            f"  - Images added: {result['image_count']}",
            "",
            "MESSAGES:",
        ])
        
        for msg in result['messages']:
            lines.append(f"  • {msg}")
    
    lines.append("="*60 + "\n")
    return "\n".join(lines)


async def ingest_pdf(pdf_path: str, ticker: str = None, filing_type: str = None, period_end_date: str = None, year: int = None) -> dict:
    """
    Ingest a PDF file and return the result.

    Args:
        pdf_path: Path to the PDF file
        ticker: Ticker symbol (optional)
        filing_type: SEC filing type - "10-K", "10-Q", or "8-K" (optional). Resolution
            order if omitted: document cover-page text > filename token > "10-K"
            default with a loud warning (never a silent guess).
        period_end_date: ISO date (YYYY-MM-DD) this filing covers — fiscal year end
            for a 10-K, fiscal quarter end for a 10-Q, event date for an 8-K
            (optional; pass this when the caller has an authoritative value, e.g.
            SEC EDGAR's reportDate — otherwise detected from cover-page text)
        year: Fiscal/Report year (optional; explicit year override for metadata tagging)

    Returns:
        dict: Processing result
    """
    # Validate input
    pdf_path = os.path.abspath(pdf_path)
    
    if not os.path.exists(pdf_path):
        return {
            "success": False,
            "file_name": os.path.basename(pdf_path),
            "error": f"File not found: {pdf_path}",
            "messages": [],
            "text_processed": False,
            "images_processed": False,
        }
    
    if not pdf_path.lower().endswith('.pdf'):
        return {
            "success": False,
            "file_name": os.path.basename(pdf_path),
            "error": f"File is not a PDF: {pdf_path}",
            "messages": [],
            "text_processed": False,
            "images_processed": False,
        }
    
    logger.info("Ingesting PDF: %s", pdf_path)
    if ticker:
        logger.info("Ticker: %s", ticker)
    if filing_type:
        logger.info("Filing type: %s", filing_type)
    if year:
        logger.info("Year: %s", year)

    result = await process_pdf_and_get_result(pdf_path, ticker=ticker, filing_type=filing_type, period_end_date=period_end_date, year=year)
    return result


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Ingest a PDF file into the vector database.")
    parser.add_argument("pdf_path", help="Path to the PDF file")
    parser.add_argument("ticker", nargs="?", help="Ticker symbol (optional)", default=None)
    parser.add_argument(
        "--filing-type",
        choices=["10-K", "10-Q", "8-K"],
        default=None,
        help="SEC filing type (optional; detected from the document's cover page, then filename, if omitted)",
    )
    parser.add_argument(
        "--period-end-date",
        default=None,
        help="ISO date (YYYY-MM-DD) this filing covers (optional; detected from cover page if omitted)",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Fiscal/Report year (optional; derived from period_end_date or filename if omitted)",
    )

    args = parser.parse_args()

    # Process the PDF
    result = await ingest_pdf(args.pdf_path, args.ticker, filing_type=args.filing_type, period_end_date=args.period_end_date, year=args.year)

    # Display formatted result
    logger.info(format_result(result))

    # Return appropriate exit code
    sys.exit(0 if result['success'] else 1)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
