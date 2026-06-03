"""
Macro Data Ingestion Script
Fetches macroeconomic indicators from the FRED API and stores them as local CSVs.
Designed to run standalone (cron/manual) or be imported by the FastAPI startup hook.
"""
import os
import json
import logging
import datetime
from pathlib import Path
import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Anchor DATA_DIR to the project root, not the current working directory.
# This file lives at <project_root>/ingestion/ingest_macro_data.py,
# so parent.parent gives us <project_root>.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "macro"
METADATA_FILE = DATA_DIR / "metadata.json"

FRED_SERIES = {
    "GDP":    {"series_id": "GDPC1",     "frequency": "quarterly", "unit": "Billions of Chained 2017 Dollars"},
    "GDPCA":  {"series_id": "GDPCA",     "frequency": "annual",    "unit": "Billions of Chained 2017 Dollars"},
    "CPI":    {"series_id": "CPIAUCSL",  "frequency": "monthly",   "unit": "Index (1982-84=100)"},
    "PCE":  {"series_id": "PCEPI",     "frequency": "monthly",   "unit": "Index (2017=100)"},
    "PPI":  {"series_id": "PPIFIS",    "frequency": "monthly",   "unit": "Index (Nov 2009=100)"},
    "ECI":  {"series_id": "ECIALLCIV", "frequency": "quarterly", "unit": "Index (Dec 2005=100)"},
    
    # Interest Rate & Yield Curve (Monthly)
    "FEDFUNDS": {"series_id": "FEDFUNDS", "frequency": "monthly", "unit": "Percent"},
    "GS1M":     {"series_id": "GS1M",     "frequency": "monthly", "unit": "Percent"},
    "GS3M":     {"series_id": "GS3M",     "frequency": "monthly", "unit": "Percent"},
    "GS6M":     {"series_id": "GS6M",     "frequency": "monthly", "unit": "Percent"},
    "GS1":      {"series_id": "GS1",      "frequency": "monthly", "unit": "Percent"},
    "GS2":      {"series_id": "GS2",      "frequency": "monthly", "unit": "Percent"},
    "GS3":      {"series_id": "GS3",      "frequency": "monthly", "unit": "Percent"},
    "GS5":      {"series_id": "GS5",      "frequency": "monthly", "unit": "Percent"},
    "GS7":      {"series_id": "GS7",      "frequency": "monthly", "unit": "Percent"},
    "GS10":     {"series_id": "GS10",     "frequency": "monthly", "unit": "Percent"},
    "GS20":     {"series_id": "GS20",     "frequency": "monthly", "unit": "Percent"},
    "GS30":     {"series_id": "GS30",     "frequency": "monthly", "unit": "Percent"},
}

def _get_api_key() -> str:
    """Resolve the FRED API key at call time (not import time)."""
    key = os.getenv("FRED_API_KEY")
    if not key:
        from dotenv import load_dotenv
        load_dotenv(override=True)
        key = os.getenv("FRED_API_KEY")
    if not key:
        raise ValueError(
            "FRED_API_KEY is not set. Add it to your .env file or environment."
        )
    return key

def fetch_fred_series(series_id: str, api_key: str, years: int = 5) -> pd.DataFrame:
    """Fetch recent data for a FRED series using the official API."""
    # Calculate observation_start
    start_date = (datetime.date.today() - datetime.timedelta(days=365 * years)).strftime("%Y-%m-%d")
    
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start_date,
        "sort_order": "desc"
    }
    
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    
    if "observations" not in data:
        raise ValueError(f"Invalid response from FRED for {series_id}: {list(data.keys())}")
        
    records = []
    for obs in data["observations"]:
        if obs["value"] == ".":
            continue
        records.append({
            "date": obs["date"],
            "value": float(obs["value"])
        })
        
    df = pd.DataFrame(records)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date", ascending=True).reset_index(drop=True)
    return df

def run_ingestion():
    """Run the macro data ingestion process."""
    api_key = _get_api_key()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # --- Cross-process lock to prevent multi-worker race conditions ---
    try:
        import fcntl
        lock_file = DATA_DIR / "ingest.lock"
        # We assign to a local variable to hold the lock until the function exits
        lock_fd = open(lock_file, "w")
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        pass  # fcntl not available on Windows, skip locking
    except (IOError, OSError):
        logger.info("Ingestion is already running in another process. Skipping.")
        return
    # ------------------------------------------------------------------
    
    logger.info("Starting Macro Data Ingestion...")
    logger.info(f"  Data directory: {DATA_DIR}")
    success_count = 0
    
    for indicator, config in FRED_SERIES.items():
        try:
            logger.info(f"Fetching {indicator} ({config['series_id']})...")
            # Fetch 20 years of history for annual series to ensure sufficient data points, otherwise 5 years.
            fetch_years = 20 if config.get("frequency") == "annual" else 5
            df = fetch_fred_series(config["series_id"], api_key=api_key, years=fetch_years)
            
            if df.empty:
                logger.warning(f"No data returned for {indicator}.")
                continue
                
            csv_path = DATA_DIR / f"{indicator.lower()}.csv"
            tmp_path = DATA_DIR / f"{indicator.lower()}.csv.tmp"
            
            # Atomic write: write to temp file, then rename to final path.
            # This prevents a concurrent reader from seeing a half-written CSV.
            df.to_csv(tmp_path, index=False)
            tmp_path.rename(csv_path)
            
            logger.info(f"Successfully saved {indicator} data ({len(df)} rows).")
            success_count += 1
            
        except Exception as e:
            logger.error(f"Failed to fetch {indicator}: {e}")
            
    if success_count > 0:
        metadata = {
            "last_sync": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source": "FRED API",
            "series_ids": FRED_SERIES,
            "success_count": success_count
        }
        # Atomic write: write to temp file, then rename.
        # Prevents corrupted metadata.json if the process is killed mid-write.
        tmp_metadata = METADATA_FILE.with_suffix('.json.tmp')
        with open(tmp_metadata, "w") as f:
            json.dump(metadata, f, indent=4)
        tmp_metadata.rename(METADATA_FILE)
        logger.info(f"Ingestion complete. {success_count}/{len(FRED_SERIES)} indicators updated.")
    else:
        logger.warning("Ingestion failed for all indicators.")

if __name__ == "__main__":
    run_ingestion()
