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
from pathlib import Path

# Add project root directory to path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.insert(0, project_root)
# Add ingestion directory to path
sys.path.insert(0, current_dir)

from ingestion.pdf_processor1 import process_pdf_and_get_result


def format_result(result: dict) -> str:
    """Format the processing result for display."""
    lines = [
        "\n" + "="*60,
        "PDF INGESTION RESULT",
        "="*60,
        f"File: {result['file_name']}",
        f"Ticker: {result.get('ticker', 'N/A')}",
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


def ingest_pdf(pdf_path: str, ticker: str = None) -> dict:
    """
    Ingest a PDF file and return the result.
    
    Args:
        pdf_path: Path to the PDF file
        ticker: Ticker symbol (optional)
        
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
    
    print(f"Ingesting PDF: {pdf_path}")
    if ticker:
        print(f"Ticker: {ticker}")
        
    result = process_pdf_and_get_result(pdf_path, ticker=ticker)
    return result


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Ingest a PDF file into the vector database.")
    parser.add_argument("pdf_path", help="Path to the PDF file")
    parser.add_argument("ticker", nargs="?", help="Ticker symbol (optional)", default=None)
    
    args = parser.parse_args()
    
    # Process the PDF
    result = ingest_pdf(args.pdf_path, args.ticker)
    
    # Display formatted result
    print(format_result(result))
    
    # Return appropriate exit code
    sys.exit(0 if result['success'] else 1)


if __name__ == "__main__":
    main()
