import os
import sys
import re
from pathlib import Path

# Add project root directory to path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# Add ingestion directory to path so internal imports in pdf_processor1 work
ingestion_dir = os.path.join(current_dir, 'ingestion')
sys.path.insert(0, ingestion_dir)

from ingestion.pdf_processor1 import process_pdf_and_get_result, extract_year_from_filename
from app.utils.company_mapping import get_ticker

def test_ingest_data_folder(manual_ticker=None):
    data_dir = os.path.join(current_dir, 'data')
    
    if not os.path.exists(data_dir):
        print(f"Directory not found: {data_dir}")
        return
    
    files = [f for f in os.listdir(data_dir) if f.lower().endswith('.pdf')]
    print(f"Found {len(files)} PDF files in the data directory.\n")
    
    for filename in files:
        file_path = os.path.join(data_dir, filename)
        
        # Identify company name, ticker and year from filename
        # Based on format: "Alphabet Inc.-10-k-2023.pdf"
        parts = filename.split('-')
        if len(parts) >= 2:
            company_raw = parts[0].strip()
            # Clean up company name for ticker lookup
            company_clean = company_raw.replace(" Inc.", "").replace(" Corp.", "").strip().lower()
            
            if manual_ticker:
                ticker = manual_ticker
            else:
                ticker = get_ticker(company_clean)
                if not ticker:
                    # Fallback to the first word if map looks for something else
                    ticker = get_ticker(company_clean.split()[0])
        else:
            company_raw = filename
            ticker = manual_ticker if manual_ticker else None
            
        year = extract_year_from_filename(filename)
        
        print("-" * 60)
        print(f"File:     {filename}")
        print(f"Company:  {company_raw}")
        print(f"Ticker:   {ticker}")
        print(f"Year:     {year}")
        print("-" * 60)
        
        # Test Ingestion
        print(f"Starting ingestion process for {filename}...")
        try:
            result = process_pdf_and_get_result(file_path, ticker=ticker if ticker else "")
            
            print(f"Status: {'✓ SUCCESS' if result['success'] else '✗ FAILED'}")
            if result['success']:
                print(f"  - Text Chunks Processed: {result['text_chunks']}")
                print(f"  - Images Extracted: {result['images_processed']}")
            else:
                print(f"  - Error: {result.get('error', 'Unknown Error')}")
                
        except Exception as e:
            print(f"Failed to ingest {filename}. Error: {e}")
        print("\n")

if __name__ == "__main__":
    passed_ticker = "googl"#sys.argv[1].lower() if len(sys.argv) > 1 else None
    # if passed_ticker:
    #     print(f"Using manual ticker override: {passed_ticker}")
    test_ingest_data_folder(passed_ticker)
