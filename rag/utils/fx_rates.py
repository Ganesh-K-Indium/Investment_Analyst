"""
FX rate lookup from the ECB euro reference rates.

The ECB publishes, for each currency, rates quoted as units of that currency
per 1 EUR, both as daily fixings and as annual averages. A USD-per-unit rate
for any currency is a cross rate: USD-per-EUR divided by CCY-per-EUR.

Series used (fetched from https://data-api.ecb.europa.eu, CSV format):
- Daily:   EXR.D.{CCY}.EUR.SP00.A   (last published fixing on or before a date)
- Annual:  EXR.A.{CCY}.EUR.SP00.A   (annual average of the fixings)

Fetched series are cached in data/fx_ecb_cache.json so each currency is
downloaded once and refreshed only when a requested date is past the cached data.
"""
import csv
import io
import json
import logging
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("rag.utils.fx_rates")

_ECB_BASE = "https://data-api.ecb.europa.eu/service/data/EXR"
_CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "fx_ecb_cache.json"
_HTTP_TIMEOUT = 30
_STALE_AFTER_DAYS = 7
_HISTORY_YEARS = 5

# In-process copy of the cache, so repeated lookups don't re-read the file.
_cache: Optional[Dict[str, Dict[str, float]]] = None


def _load_cache() -> Dict[str, Dict[str, float]]:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_CACHE_PATH.read_text()) if _CACHE_PATH.exists() else {}
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("FX cache unreadable (%s); starting empty", e)
            _cache = {}
    return _cache


def _save_cache() -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(_cache, indent=1, sort_keys=True))
    except OSError as e:
        logger.warning("Could not write FX cache (%s)", e)


def _history_start() -> str:
    """First date kept in the cache: five years back. Older rates are not needed for FY comparisons."""
    return (date.today().replace(year=date.today().year - _HISTORY_YEARS)).isoformat()


def _fetch_series(freq: str, currency: str) -> Dict[str, float]:
    """Download one ECB series as {ISO date or year: value}. Empty dict on failure."""
    key = f"EXR/{freq}.{currency}.EUR.SP00.A"
    url = f"{_ECB_BASE}/{freq}.{currency}.EUR.SP00.A?startPeriod={_history_start()}&format=csvdata"
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
    except Exception as e:  # network or HTTP error: the caller treats the rate as unavailable
        logger.warning("ECB fetch failed for %s: %s", key, e)
        return {}
    rows = {}
    for row in csv.DictReader(io.StringIO(body)):
        value = row.get("OBS_VALUE")
        period = row.get("TIME_PERIOD")
        if value and period:
            try:
                rows[period] = float(value)
            except ValueError:
                continue
    return rows


def _series(freq: str, currency: str, needed: Optional[date] = None) -> Dict[str, float]:
    """Return the cached series, refreshing it if it is missing or older than `needed`."""
    cache = _load_cache()
    key = f"{freq}.{currency}"
    data = cache.get(key)
    stale = data is None or not data
    if not stale and needed is not None and freq == "D":
        latest = max(data)
        latest_date = datetime.strptime(latest, "%Y-%m-%d").date()
        stale = needed > latest_date + timedelta(days=_STALE_AFTER_DAYS)
    if stale:
        fetched = _fetch_series(freq, currency)
        if fetched:
            cache[key] = fetched
            _save_cache()
            data = fetched
    return data or {}



def usd_per_unit_on(currency: str, on: date) -> Optional[float]:
    """
    USD per 1 unit of `currency` on the last ECB fixing on or before `on`.
    Falls back up to 10 days back to skip weekends and holidays.
    """
    if currency == "USD":
        return 1.0
    usd_series = _series("D", "USD", on)
    # EUR is the ECB's base currency: its USD-per-unit is the USD-per-EUR series itself.
    series = None if currency == "EUR" else _series("D", currency, on)
    for back in range(11):
        d = (on - timedelta(days=back)).isoformat()
        if d not in usd_series or not usd_series[d]:
            continue
        if currency == "EUR":
            return usd_series[d]
        if series and series.get(d):
            return usd_series[d] / series[d]
    return None


def usd_per_unit_avg(currency: str, year: int) -> Optional[float]:
    """USD per 1 unit of `currency` for the calendar year's ECB annual average."""
    if currency == "USD":
        return 1.0
    period = str(year)
    usd = _series("A", "USD").get(period)
    if currency == "EUR":
        return usd or None
    ccy = _series("A", currency).get(period)
    if not ccy or not usd:
        return None
    return usd / ccy
