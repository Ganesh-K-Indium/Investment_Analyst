"""
Macro Data Calculation Engine
Loads locally stored FRED data, aggregates monthly → quarterly,
and computes YoY / QoQ percentage changes.
"""
import json
import datetime
from pathlib import Path
from typing import Dict, Any, Optional
import pandas as pd
import logging

logger = logging.getLogger(__name__)

# Anchor DATA_DIR to the project root, not the current working directory.
# This file lives at <project_root>/app/utils/macro_utils.py,
# so parent.parent.parent gives us <project_root>.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "macro"
METADATA_FILE = DATA_DIR / "metadata.json"

SERIES_DISPLAY_NAMES = {
    "GDP":      "Real Gross Domestic Product",
    "GDPCA":    "Real Gross Domestic Product (Annual)",
    "CPI":      "Consumer Price Index for All Urban Consumers",
    "PCE":      "Personal Consumption Expenditures Price Index",
    "PPI":      "Producer Price Index for Final Demand",
    "ECI":      "Employment Cost Index",
    "FEDFUNDS": "Federal Funds Effective Rate",
    "GS1M":     "1-Month Treasury Constant Maturity Rate",
    "GS3M":     "3-Month Treasury Constant Maturity Rate",
    "GS6M":     "6-Month Treasury Constant Maturity Rate",
    "GS1":      "1-Year Treasury Constant Maturity Rate",
    "GS2":      "2-Year Treasury Constant Maturity Rate",
    "GS3":      "3-Year Treasury Constant Maturity Rate",
    "GS5":      "5-Year Treasury Constant Maturity Rate",
    "GS7":      "7-Year Treasury Constant Maturity Rate",
    "GS10":     "10-Year Treasury Constant Maturity Rate",
    "GS20":     "20-Year Treasury Constant Maturity Rate",
    "GS30":     "30-Year Treasury Constant Maturity Rate",
}

def get_metadata() -> Dict[str, Any]:
    if not METADATA_FILE.exists():
        return {}
    with open(METADATA_FILE, "r") as f:
        return json.load(f)

def is_data_stale(metadata: Dict[str, Any], days: int = 7) -> bool:
    if "last_sync" not in metadata:
        return True
    try:
        last_sync_str = metadata["last_sync"].replace("Z", "+00:00")
        last_sync = datetime.datetime.fromisoformat(last_sync_str)
        now = datetime.datetime.now(datetime.timezone.utc)
        return (now - last_sync).days > days
    except Exception:
        return True

# Track the running ingestion subprocess to prevent concurrent runs and handle reset
_reingestion_process = None
_last_ingestion_failure = None  # Cooldown timestamp to prevent retry storms
_INGESTION_COOLDOWN_SECONDS = 3600  # 1 hour cooldown after a failed ingestion

def trigger_auto_reingestion_if_stale():
    """
    Check if macro data is stale and trigger a background re-ingestion.
    This is a non-blocking call — the ingestion runs as a subprocess.
    
    Includes a cooldown mechanism: if the last ingestion attempt failed,
    we wait at least 1 hour before retrying to prevent spawn storms
    (e.g. when FRED_API_KEY is missing).
    """
    global _reingestion_process, _last_ingestion_failure
    
    # If a process was spawned, check if it's still running
    if _reingestion_process is not None:
        if _reingestion_process.poll() is None:
            return  # Still running, skip triggering another one
        else:
            # Process finished — check if it failed
            if _reingestion_process.returncode != 0:
                _last_ingestion_failure = datetime.datetime.now(datetime.timezone.utc)
                logger.warning(
                    f"Background macro ingestion failed (exit code {_reingestion_process.returncode}). "
                    f"Will retry after {_INGESTION_COOLDOWN_SECONDS}s cooldown."
                )
            else:
                # Success — clear any previous failure cooldown
                _last_ingestion_failure = None
            _reingestion_process = None  # Finished running, reset handle
    
    # Cooldown check: don't retry if the last attempt failed recently
    if _last_ingestion_failure is not None:
        elapsed = (datetime.datetime.now(datetime.timezone.utc) - _last_ingestion_failure).total_seconds()
        if elapsed < _INGESTION_COOLDOWN_SECONDS:
            return  # Still in cooldown period, skip
        else:
            _last_ingestion_failure = None  # Cooldown expired, allow retry
    
    metadata = get_metadata()
    if not is_data_stale(metadata):
        return
    
    import subprocess
    import sys
    
    ingestion_script = PROJECT_ROOT / "ingestion" / "ingest_macro_data.py"
    if not ingestion_script.exists():
        logger.warning(f"Ingestion script not found at {ingestion_script}")
        return
    
    try:
        logger.info("Macro data is stale. Triggering background re-ingestion...")
        _reingestion_process = subprocess.Popen(
            [sys.executable, str(ingestion_script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(PROJECT_ROOT)
        )
    except Exception as e:
        logger.error(f"Failed to trigger macro data re-ingestion: {e}")
        _reingestion_process = None
        _last_ingestion_failure = datetime.datetime.now(datetime.timezone.utc)

def load_indicator_data(indicator: str) -> Optional[pd.DataFrame]:
    """Load the CSV for a specific indicator. Returns DataFrame or None."""
    csv_path = DATA_DIR / f"{indicator.lower()}.csv"
    if not csv_path.exists():
        return None
    df = pd.read_csv(csv_path)
    df["date"] = pd.to_datetime(df["date"])
    return df

def aggregate_monthly_to_quarterly(df: pd.DataFrame) -> pd.DataFrame:
    """Average 3 months of data to produce a quarterly figure.
    
    Checks that the quarter has complete data (3 months) before aggregating.
    If incomplete, it is excluded.
    """
    df['quarter'] = df['date'].dt.to_period('Q')
    
    # Calculate average value and count of months per quarter
    agg = df.groupby('quarter').agg(
        value=('value', 'mean'),
        count=('value', 'count')
    ).reset_index()
    
    # Convert quarter to date (first day of the quarter)
    agg['date'] = agg['quarter'].dt.start_time
    
    return agg[['date', 'value', 'count']]

def parse_period_str(period_str: str, granularity: str) -> pd.Period:
    """Parse a period string like '2025-03', 'Q1 2025', '2025 Q1' into a pandas Period."""
    clean = period_str.strip().upper()
    gran = granularity.lower()
    freq = 'Y' if gran == 'annual' else ('Q' if gran == 'quarterly' else 'M')
    
    try:
        if freq == 'Q':
            # Handle 'Q1 2025' or '2025 Q1' or '2025-Q1'
            if 'Q' in clean:
                parts = [p.strip() for p in clean.replace('-', ' ').split(' ') if p.strip()]
                if len(parts) == 2:
                    if parts[0].startswith('Q') and parts[1].isdigit():
                        return pd.Period(f"{parts[1]}-{parts[0]}", freq=freq)
                    elif parts[1].startswith('Q') and parts[0].isdigit():
                        return pd.Period(f"{parts[0]}-{parts[1]}", freq=freq)
            # Fallback to default pandas parsing
            return pd.Period(clean, freq=freq)
        else:
            # Monthly: handle 'JAN 2025', '2025-01', etc.
            return pd.Period(clean, freq=freq)
    except Exception as e:
        raise ValueError(f"Could not parse period '{period_str}' for {granularity} granularity: {e}")

def format_period_str(dt: datetime.datetime, granularity: str) -> str:
    """Format a datetime into a human readable period string."""
    if granularity.lower() == 'annual':
        return str(dt.year)
    elif granularity.lower() == 'quarterly':
        quarter = (dt.month - 1) // 3 + 1
        return f"Q{quarter} {dt.year}"
    else:
        return dt.strftime("%B %Y")

def get_macro_comparison(indicator: str, period1: Optional[str] = None, period2: Optional[str] = None, comparison_type: str = "YoY", granularity: Optional[str] = None) -> Dict[str, Any]:
    """
    Calculate the percentage change for a macro indicator.
    If period1 and period2 are not provided, it defaults to the latest available vs previous based on comparison_type.
    Supports both 'monthly' and 'quarterly' granularity.
    """
    indicator = indicator.upper()
    
    # Auto-refresh stale data in background if needed
    trigger_auto_reingestion_if_stale()
    
    metadata = get_metadata()
    series_info = metadata.get("series_ids", {}).get(indicator)
    
    if not series_info:
        return {"error": f"Unknown indicator: {indicator}"}
        
    df = load_indicator_data(indicator)
    if df is None or df.empty:
        return {"error": f"No data available for {indicator}."}
        
    frequency = series_info.get("frequency", "monthly").lower()
    
    # Resolve 'native' granularity to the indicator's actual frequency
    if not granularity or granularity.lower() == 'native':
        # First, check if periods hint at a specific granularity
        is_monthly_period = False
        for p in [period1, period2]:
            if p:
                p_clean = p.strip().upper()
                if "Q" in p_clean:
                    continue
                has_letters = any(c.isalpha() for c in p_clean)
                is_yyyymm = len(p_clean.split('-')) == 2 and p_clean.split('-')[0].isdigit() and p_clean.split('-')[1].isdigit()
                if has_letters or is_yyyymm:
                    is_monthly_period = True
                    break
        if is_monthly_period:
            granularity = "monthly"
        else:
            # Use the indicator's native frequency from metadata
            granularity = frequency

    target_granularity = granularity.lower()
    
    # Fall back to quarterly if data only exists quarterly (e.g. GDP, ECI)
    if frequency == "quarterly" and target_granularity == "monthly":
        target_granularity = "quarterly"
        
    if frequency == "monthly" and target_granularity == "quarterly":
        df = aggregate_monthly_to_quarterly(df)
        
    # Convert dates to Period objects for easy math
    freq_code = 'Y' if target_granularity == 'annual' else ('Q' if target_granularity == 'quarterly' else 'M')
    df['period'] = df['date'].dt.to_period(freq_code)
    
    # Drop duplicate periods (keep last occurrence) to guarantee .loc returns a scalar.
    df = df.drop_duplicates(subset='period', keep='last')
    df = df.set_index('period').sort_index()
    
    if period1:
        try:
            p1 = parse_period_str(period1, target_granularity)
        except ValueError as e:
            return {"error": str(e)}
    else:
        # Default to latest complete period
        p1 = df.index[-1]
        # For monthly indicators at quarterly granularity, if the latest quarter is incomplete, fall back to previous
        if frequency == "monthly" and target_granularity == "quarterly" and 'count' in df.columns:
            if int(df.loc[p1, 'count']) < 3 and len(df.index) >= 2:
                p1 = df.index[-2]
        
    if period2:
        try:
            p2 = parse_period_str(period2, target_granularity)
        except ValueError as e:
            return {"error": str(e)}
    else:
        # Default based on comparison_type
        if comparison_type.upper() == "YOY":
            p2 = p1 - (1 if target_granularity == 'annual' else (4 if target_granularity == "quarterly" else 12))
        else: # QoQ / MoM
            p2 = p1 - 1
            
    # Ensure p1 is the more recent period
    if p2 > p1:
        p1, p2 = p2, p1
        
    fallback_applied = False
    fallback_message = None
    
    # Helper to check if a period is in index and is complete (has 3 months of data if monthly at quarterly granularity)
    def is_period_complete(p):
        if p not in df.index:
            return False
        if frequency == "monthly" and target_granularity == "quarterly" and 'count' in df.columns:
            return int(df.loc[p, 'count']) >= 3
        return True

    # 1. Adjust p1 if it's not complete or not in index
    if not is_period_complete(p1):
        requested_p1_str = format_period_str(p1.start_time, target_granularity) if hasattr(p1, 'start_time') else str(period1)
        # Find the latest complete period in index
        latest_complete = None
        for idx in reversed(df.index):
            if is_period_complete(idx):
                latest_complete = idx
                break
        if latest_complete is not None:
            p1 = latest_complete
            fallback_applied = True
            fallback_message = f"Requested data for {requested_p1_str} is not available. Showing the latest available data for {format_period_str(p1.start_time, target_granularity)}."
            # Recalculate p2 based on the new p1 to maintain structure if period2 wasn't explicitly requested
            if not period2:
                if comparison_type.upper() == "YOY":
                    p2 = p1 - (1 if target_granularity == 'annual' else (4 if target_granularity == "quarterly" else 12))
                else:
                    p2 = p1 - 1
        else:
            return {"error": f"No complete data available for {indicator}."}

    # 2. Adjust p2 if it's not complete or not in index
    if not is_period_complete(p2):
        requested_p2_str = format_period_str(p2.start_time, target_granularity) if hasattr(p2, 'start_time') else str(period2)
        # Try relative comparison first (YoY or QoQ/MoM)
        if comparison_type.upper() == "YOY":
            p2_candidate = p1 - (1 if target_granularity == 'annual' else (4 if target_granularity == "quarterly" else 12))
        else:
            p2_candidate = p1 - 1
            
        if is_period_complete(p2_candidate):
            p2 = p2_candidate
            fallback_applied = True
            if not fallback_message:
                fallback_message = f"Requested comparison data for {requested_p2_str} is not available. Showing comparison with {format_period_str(p2.start_time, target_granularity)}."
        else:
            # Find any other complete period in the index
            alternative_p2 = None
            for idx in reversed(df.index):
                if idx < p1 and is_period_complete(idx):
                    alternative_p2 = idx
                    break
            if alternative_p2 is not None:
                p2 = alternative_p2
                fallback_applied = True
                if not fallback_message:
                    fallback_message = f"Requested comparison data for {requested_p2_str} is not available. Showing comparison with {format_period_str(p2.start_time, target_granularity)}."
            else:
                return {"error": f"No complete comparison data available for {indicator}."}
            
    val1 = df.loc[p1, 'value']
    val2 = df.loc[p2, 'value']
    pct_change = ((val1 - val2) / val2) * 100
    absolute_change = val1 - val2
    unit = series_info.get("unit", "")
    
    result = {
        "indicator": indicator,
        "period1": format_period_str(p1.start_time, target_granularity),
        "period2": format_period_str(p2.start_time, target_granularity),
        "val1": round(float(val1), 2),
        "val2": round(float(val2), 2),
        "percentage_change": round(float(pct_change), 2),
        "absolute_change": round(float(absolute_change), 2),
        "unit": unit
    }
    
    # Calculate basis points change directly in Python if the unit is Percent
    if unit.lower() == "percent":
        result["basis_points_change"] = round(float(absolute_change * 100), 1)
        
    if fallback_applied:
        result["info"] = fallback_message
        
    if is_data_stale(metadata):
        last_sync = metadata.get('last_sync', 'Unknown')
        result["warning"] = f"Data was last updated on {last_sync}. Recent revisions may not be reflected."
        
    return result

def get_all_macro_latest(comparison_type: str = "YoY") -> Dict[str, Any]:
    """Convenience function to get the latest comparison for all registered indicators."""
    metadata = get_metadata()
    if not metadata:
        return {"error": "Macro data has not been initialized."}
        
    results = {}
    stale = is_data_stale(metadata)
    
    for indicator in metadata.get("series_ids", {}).keys():
        res = get_macro_comparison(indicator, comparison_type=comparison_type)
        if "error" not in res:
            results[indicator] = {
                "comparison": f"{res['period1']} vs {res['period2']}",
                "change": f"{res['percentage_change']:+.2f}%"
            }
            
    return {
        "data": results,
        "warning": f"Data was last updated on {metadata.get('last_sync')}. Recent revisions may not be reflected." if stale else None
    }


def calculate_yield_spread(res1: Dict[str, Any], res2: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Helper function to calculate the cross-sectional yield spread between two rate metrics."""
    if "val1" not in res1 or "val1" not in res2 or res1.get("unit") != "Percent" or res2.get("unit") != "Percent":
        return None

    # Maturity in months mapping
    maturities = {
        "FEDFUNDS": 0, "GS1M": 1, "GS3M": 3, "GS6M": 6, "GS1": 12,
        "GS2": 24, "GS3": 36, "GS5": 60, "GS7": 84, "GS10": 120,
        "GS20": 240, "GS30": 360
    }
    
    ind1, ind2 = res1.get("indicator", ""), res2.get("indicator", "")
    m1, m2 = maturities.get(ind1, -1), maturities.get(ind2, -1)
    
    if m1 < 0 or m2 < 0 or m1 == m2:
        return None
        
    long_res, short_res = (res1, res2) if m1 > m2 else (res2, res1)
    
    spread_val1 = long_res["val1"] - short_res["val1"]
    spread_bps1 = round(float(spread_val1 * 100), 1)
    
    spread_info = {
        "type": "yield_spread",
        "long_term_indicator": long_res["indicator"],
        "short_term_indicator": short_res["indicator"],
        "spread_val1": round(spread_val1, 2),
        "spread_bps1": spread_bps1
    }
    
    if "val2" in long_res and "val2" in short_res:
        spread_val2 = long_res["val2"] - short_res["val2"]
        spread_info["spread_val2"] = round(spread_val2, 2)
        spread_info["spread_bps2"] = round(float(spread_val2 * 100), 1)

    return spread_info

def build_source_attribution_context(calc_results: list) -> str:
    """Takes calculation results and returns a formatted source attribution string."""
    try:
        metadata = get_metadata()
        last_sync_raw = metadata.get("last_sync", "Unknown")
        sync_date = last_sync_raw.split("T")[0] if "T" in last_sync_raw else last_sync_raw
    except Exception:
        metadata = {}
        sync_date = "Unknown"

    seen_indicators = set()
    valid_indicators = []

    for item in calc_results:
        req = item.get("requested", {})
        res = item.get("result", {})

        if "error" in res or "special" in req:
            continue

        indicator = req.get("indicator")
        if not indicator or indicator in seen_indicators:
            continue
            
        seen_indicators.add(indicator)
        valid_indicators.append(indicator)

    if not valid_indicators:
        return ""
        
    # If there are many indicators, consolidate to keep UI clean
    if len(valid_indicators) > 3 or "ALL" in valid_indicators:
        return f"\n--- Source Attribution ---\n- All macroeconomic indicators are sourced from Federal Reserve Economic Data (FRED), Last updated: {sync_date}\n"

    source_lines = []
    for indicator in valid_indicators:
        if indicator == "ALL":
            continue
            
        series_info = metadata.get("series_ids", {}).get(indicator, {})
        series_id = series_info.get("series_id", indicator)
        display_name = SERIES_DISPLAY_NAMES.get(indicator, indicator)

        source_lines.append(
            f"- {display_name} ({series_id}): "
            f"Federal Reserve Economic Data (FRED), "
            f"https://fred.stlouisfed.org/series/{series_id}, "
            f"Last updated: {sync_date}"
        )
        
    return "\n--- Source Attribution ---\n" + "\n".join(source_lines) + "\n"
