"""
Macro Data Ingestion Script
Fetches macroeconomic indicators from the FRED API and stores them as local CSVs.
Designed to run standalone (cron/manual) or be imported by the FastAPI startup hook.
"""
import asyncio
import os
import json
import logging
import datetime
from pathlib import Path
from typing import Optional
import pandas as pd
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Anchor DATA_DIR to the project root, not the current working directory.
# This file lives at <project_root>/ingestion/ingest_macro_data.py,
# so parent.parent gives us <project_root>.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "macro"
METADATA_FILE = DATA_DIR / "metadata.json"

FRED_SERIES = {
    "GDP":    {"series_id": "GDPC1",     "frequency": "quarterly", "unit": "Billions of Chained 2017 Dollars", "source": "FRED"},
    "GDPCA":  {"series_id": "GDPCA",     "frequency": "annual",    "unit": "Billions of Chained 2017 Dollars", "source": "FRED"},
    "CPI":    {"series_id": "CPIAUCSL",  "frequency": "monthly",   "unit": "Index (1982-84=100)", "source": "FRED"},
    "PCE":  {"series_id": "PCEPI",     "frequency": "monthly",   "unit": "Index (2017=100)", "source": "FRED"},
    "PPI":  {"series_id": "PPIFIS",    "frequency": "monthly",   "unit": "Index (Nov 2009=100)", "source": "FRED"},
    "ECI":  {"series_id": "ECIALLCIV", "frequency": "quarterly", "unit": "Index (Dec 2005=100)", "source": "FRED"},

    # Interest Rate & Yield Curve (Monthly)
    "FEDFUNDS": {"series_id": "FEDFUNDS", "frequency": "monthly", "unit": "Percent", "source": "FRED"},
    "GS1M":     {"series_id": "GS1M",     "frequency": "monthly", "unit": "Percent", "source": "FRED"},
    "GS3M":     {"series_id": "GS3M",     "frequency": "monthly", "unit": "Percent", "source": "FRED"},
    "GS6M":     {"series_id": "GS6M",     "frequency": "monthly", "unit": "Percent", "source": "FRED"},
    "GS1":      {"series_id": "GS1",      "frequency": "monthly", "unit": "Percent", "source": "FRED"},
    "GS2":      {"series_id": "GS2",      "frequency": "monthly", "unit": "Percent", "source": "FRED"},
    "GS3":      {"series_id": "GS3",      "frequency": "monthly", "unit": "Percent", "source": "FRED"},
    "GS5":      {"series_id": "GS5",      "frequency": "monthly", "unit": "Percent", "source": "FRED"},
    "GS7":      {"series_id": "GS7",      "frequency": "monthly", "unit": "Percent", "source": "FRED"},
    "GS10":     {"series_id": "GS10",     "frequency": "monthly", "unit": "Percent", "source": "FRED"},
    "GS20":     {"series_id": "GS20",     "frequency": "monthly", "unit": "Percent", "source": "FRED"},
    "GS30":     {"series_id": "GS30",     "frequency": "monthly", "unit": "Percent", "source": "FRED"},
}

# Core BLS labor-market rates, fetched directly from the BLS API.
BLS_SERIES = {
    "U3":   {"series_id": "LNS14000000", "frequency": "monthly", "unit": "Percent", "source": "BLS"},  # official unemployment rate
    "U1":   {"series_id": "LNS13025670", "frequency": "monthly", "unit": "Percent", "source": "BLS"},
    "U2":   {"series_id": "LNS14023621", "frequency": "monthly", "unit": "Percent", "source": "BLS"},
    "U4":   {"series_id": "LNS13327707", "frequency": "monthly", "unit": "Percent", "source": "BLS"},
    "U5":   {"series_id": "LNS13327708", "frequency": "monthly", "unit": "Percent", "source": "BLS"},
    "U6":   {"series_id": "LNS13327709", "frequency": "monthly", "unit": "Percent", "source": "BLS"},
    "LFPR": {"series_id": "LNS11300000", "frequency": "monthly", "unit": "Percent", "source": "BLS"},
    "EPOP": {"series_id": "LNS12300000", "frequency": "monthly", "unit": "Percent", "source": "BLS"},
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

async def fetch_fred_series(client: httpx.AsyncClient, series_id: str, api_key: str, years: int = 5) -> pd.DataFrame:
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

    response = await client.get(url, params=params, timeout=30)
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

async def _fetch_and_save_one(client: httpx.AsyncClient, semaphore: asyncio.Semaphore, indicator: str, config: dict, api_key: str) -> bool:
    """Fetch one FRED series and atomically write its CSV. Returns True on success."""
    async with semaphore:
        try:
            logger.info(f"Fetching {indicator} ({config['series_id']})...")
            # Fetch 20 years of history for annual series to ensure sufficient data points, otherwise 5 years.
            fetch_years = 20 if config.get("frequency") == "annual" else 5
            df = await fetch_fred_series(client, config["series_id"], api_key=api_key, years=fetch_years)

            if df.empty:
                logger.warning(f"No data returned for {indicator}.")
                return False

            _save_indicator_csv(indicator, df)
            logger.info(f"Successfully saved {indicator} data ({len(df)} rows).")
            return True

        except Exception as e:
            logger.error(f"Failed to fetch {indicator}: {e}")
            return False


def _save_indicator_csv(indicator: str, df: pd.DataFrame) -> None:
    """Atomically write an indicator's DataFrame to its CSV (write-then-rename)."""
    csv_path = DATA_DIR / f"{indicator.lower()}.csv"
    tmp_path = DATA_DIR / f"{indicator.lower()}.csv.tmp"
    df.to_csv(tmp_path, index=False)
    tmp_path.rename(csv_path)


def _get_bls_api_key() -> Optional[str]:
    """Resolve the BLS API registration key, if configured. BLS works unregistered too
    (lower daily query limit and 10-year lookback cap), so this is optional."""
    key = os.getenv("BLS_API_KEY")
    if not key:
        from dotenv import load_dotenv
        load_dotenv(override=True)
        key = os.getenv("BLS_API_KEY")
    return key or None


async def fetch_bls_series_batch(client: httpx.AsyncClient, series_ids: list, api_key: Optional[str] = None, years: int = 5) -> dict:
    """Fetch multiple BLS series in a single batched request (BLS API v2 supports up to 25/50 series per call).

    Returns {series_id: DataFrame}.
    """
    end_year = datetime.date.today().year
    start_year = end_year - years

    url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
    payload = {
        "seriesid": series_ids,
        "startyear": str(start_year),
        "endyear": str(end_year),
    }
    if api_key:
        payload["registrationkey"] = api_key

    response = await client.post(url, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError(f"BLS API request failed: {data.get('message', data.get('status'))}")

    results = {}
    for series in data.get("Results", {}).get("series", []):
        series_id = series.get("seriesID")
        records = []
        for obs in series.get("data", []):
            period = obs.get("period", "")
            # Skip annual-average pseudo-periods (M13); we only want the 12 monthly values.
            if not period.startswith("M") or period == "M13":
                continue
            month = int(period[1:])
            try:
                value = float(obs["value"])
            except (ValueError, TypeError):
                continue
            records.append({
                "date": datetime.date(int(obs["year"]), month, 1),
                "value": value
            })

        df = pd.DataFrame(records)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date", ascending=True).reset_index(drop=True)
        results[series_id] = df

    return results


async def _fetch_and_save_bls_batch(client: httpx.AsyncClient) -> int:
    """Fetch all BLS_SERIES in one batched request and save each to its CSV. Returns success count."""
    if not BLS_SERIES:
        return 0
    try:
        api_key = _get_bls_api_key()
        # Multiple indicators could point at the same series_id —
        # fetch each distinct series_id once, then fan the result back out to every indicator using it.
        series_id_to_indicators = {}
        for name, cfg in BLS_SERIES.items():
            series_id_to_indicators.setdefault(cfg["series_id"], []).append(name)
        logger.info(f"Fetching {len(series_id_to_indicators)} distinct BLS series for {len(BLS_SERIES)} indicators: {list(BLS_SERIES.keys())}...")

        results = await fetch_bls_series_batch(client, list(series_id_to_indicators.keys()), api_key=api_key, years=5)

        success_count = 0
        for series_id, indicators in series_id_to_indicators.items():
            df = results.get(series_id)
            if df is None or df.empty:
                logger.warning(f"No BLS data returned for {indicators} ({series_id}).")
                continue
            for indicator in indicators:
                _save_indicator_csv(indicator, df)
                logger.info(f"Successfully saved {indicator} data ({len(df)} rows).")
                success_count += 1
        return success_count
    except Exception as e:
        logger.error(f"Failed to fetch BLS series batch: {e}")
        return 0


async def run_ingestion():
    """Run the macro data ingestion process. Fetches all FRED series concurrently (bounded)."""
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

    # Bounded concurrency — FRED's API has no documented hard rate limit, but this keeps
    # request bursts modest rather than firing all ~17 series at once.
    semaphore = asyncio.Semaphore(5)
    async with httpx.AsyncClient() as client:
        fred_results, bls_success_count = await asyncio.gather(
            asyncio.gather(*(
                _fetch_and_save_one(client, semaphore, indicator, config, api_key)
                for indicator, config in FRED_SERIES.items()
            )),
            _fetch_and_save_bls_batch(client),
        )
    success_count = sum(fred_results) + bls_success_count
    total_series = len(FRED_SERIES) + len(BLS_SERIES)

    if success_count > 0:
        metadata = {
            "last_sync": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source": "FRED API + BLS API",
            "series_ids": {**FRED_SERIES, **BLS_SERIES},
            "success_count": success_count
        }
        # Atomic write: write to temp file, then rename.
        # Prevents corrupted metadata.json if the process is killed mid-write.
        tmp_metadata = METADATA_FILE.with_suffix('.json.tmp')
        with open(tmp_metadata, "w") as f:
            json.dump(metadata, f, indent=4)
        tmp_metadata.rename(METADATA_FILE)
        logger.info(f"Ingestion complete. {success_count}/{total_series} indicators updated.")
    else:
        logger.warning("Ingestion failed for all indicators.")

if __name__ == "__main__":
    asyncio.run(run_ingestion())
