#!/usr/bin/env python3
"""
Simple PDF Ingestion Script

Usage:
    python ingest_pdf.py /path/to/pdf_file.pdf
"""

import sys
import os
import json
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pdf_processor1 import process_pdf_and_get_result


def format_result(result: dict) -> str:
    """Format the processing result for display."""
    lines = [
        "\n" + "="*60,
        "PDF INGESTION RESULT",
        "="*60,
        f"File: {result['file_name']}",
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


def ingest_pdf(pdf_path: str) -> dict:
    """
    Ingest a PDF file and return the result.
    
    Args:
        pdf_path: Path to the PDF file
        
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
    result = process_pdf_and_get_result(pdf_path)
    return result


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python ingest_pdf.py <pdf_file_path>")
        print("\nExample: python ingest_pdf.py /path/to/document.pdf")
        print("Note: If the path contains spaces, enclose it in quotes or pass as separate arguments")
        sys.exit(1)
    
    pdf_path = ' '.join(sys.argv[1:])
    
    # Process the PDF
    result = ingest_pdf(pdf_path)
    
    # Display formatted result
    print(format_result(result))
    
    # Return appropriate exit code
    sys.exit(0 if result['success'] else 1)


if __name__ == "__main__":
    main()
