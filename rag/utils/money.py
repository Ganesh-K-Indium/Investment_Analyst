"""
Currency and scale handling for comparison tables and charts.

A filing states figures in its reporting currency and scale (e.g. Sanofi in EUR
billions, Embraer in USD millions). Comparing those raw strings is wrong: a
"€43.6B" cell next to a "$94.2B" cell is not a like-for-like comparison, and a
"$402,836 million" cell next to a "$94.2B" cell differs by 1000x in the raw
number. Everything that compares companies goes through this module so all
monetary values end up as USD billions.

Rate basis (the convention used by most analysts):
- Flow items (revenue, income, expenses, R&D, cash flow) convert at the fiscal
  year's AVERAGE rate, because they accumulate over the year.
- Balance-sheet items (assets, liabilities, debt, equity, cash) convert at the
  PERIOD-END rate, because they are a point-in-time position.

Each company's period-end is its own fiscal year end (see
app.utils.company_mapping.get_fiscal_period_end), not a shared 31 Dec.

FX source: ECB euro reference rates via rag/utils/fx_rates.py. Rates are looked
up, not hard-coded: the ECB daily fixing on or before the period-end date, and
the ECB annual average for flow items. If a rate can't be fetched, the value is
marked "not comparable" rather than converted with a guess.
"""
import re
from datetime import date
from typing import Optional, Tuple

from rag.utils.fx_rates import usd_per_unit_avg, usd_per_unit_on

FX_SOURCE = "ECB euro reference rates"

FLOW = "average"
POINT_IN_TIME = "period_end"

# Currency detection, checked in order. Symbols are matched before codes so
# "US$" is USD, not treated as a bare "$" in another currency.
_CURRENCY_PATTERNS = [
    # Prefixed "$" symbols first, so "C$" is not read as a bare "$" (USD).
    (r"US\$|\$\s?US|\bUSD\b", "USD"),
    (r"C\$|\bCAD\b|\bCA\$", "CAD"),
    (r"A\$|\bAUD\b", "AUD"),
    (r"NZ\$|\bNZD\b", "NZD"),
    (r"HK\$|\bHKD\b", "HKD"),
    (r"S\$|\bSGD\b", "SGD"),
    (r"MX\$|\bMXN\b", "MXN"),
    (r"NT\$|\bTWD\b", "TWD"),       # not published by the ECB: shown as not comparable
    # Other currencies by symbol or code.
    (r"€|\bEUR\b", "EUR"),
    (r"£|\bGBP\b", "GBP"),
    (r"R\$|\bBRL\b", "BRL"),
    (r"₹|\bINR\b", "INR"),
    (r"₩|\bKRW\b", "KRW"),
    (r"₺|\bTRY\b", "TRY"),
    (r"₪|\bILS\b", "ILS"),
    (r"฿|\bTHB\b", "THB"),
    (r"₱|\bPHP\b", "PHP"),
    (r"zł|\bPLN\b", "PLN"),
    (r"₽|\bRUB\b", "RUB"),           # not published by the ECB: shown as not comparable
    (r"\bZAR\b", "ZAR"),
    (r"\bSEK\b", "SEK"),
    (r"\bNOK\b", "NOK"),
    (r"\bDKK\b", "DKK"),
    (r"\bCZK\b", "CZK"),
    (r"\bHUF\b", "HUF"),
    (r"\bRON\b", "RON"),
    (r"\bBGN\b", "BGN"),
    (r"\bISK\b", "ISK"),
    (r"\bIDR\b", "IDR"),
    (r"\bMYR\b", "MYR"),
    (r"\bSAR\b", "SAR"),             # not published by the ECB: shown as not comparable
    (r"\bAED\b", "AED"),             # not published by the ECB: shown as not comparable
    (r"\bRMB\b|\bCNY\b", "CNY"),
    (r"\bCHF\b", "CHF"),
    (r"¥|\bJPY\b", "JPY"),
    (r"\$", "USD"),  # bare "$" last: the default for US filers
]

# Scale word -> multiplier to billions.
_SCALE_TO_BILLIONS = [
    (r"\btrillion\b|\btn\b", 1000.0),
    (r"\bbillion\b|\bbn\b|(?<=\d)\s?B\b", 1.0),
    (r"\bmillion\b|\bmn\b|(?<=\d)\s?M\b", 0.001),
    (r"\bthousand\b|(?<=\d)\s?K\b", 0.000001),
]

# Metric-name keywords for point-in-time (balance-sheet) items. "cash flow" is
# a flow item and is excluded before these are checked.
_BALANCE_SHEET_KEYWORDS = (
    "asset", "liabilit", "debt", "borrowing", "equity", "net worth",
    "cash and", "cash position", "cash balance", "inventor", "receivable",
    "payable", "property", "goodwill", "capital employed",
)

_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*|-?\.\d+")


def basis_for_metric(metric_name: str) -> str:
    """Return FLOW or POINT_IN_TIME for a table metric name."""
    name = (metric_name or "").lower()
    if "cash flow" in name:
        return FLOW
    if any(k in name for k in _BALANCE_SHEET_KEYWORDS):
        return POINT_IN_TIME
    return FLOW


def detect_currency(text: str) -> Optional[str]:
    """Return an ISO-like currency code from a cell, or None if none is stated."""
    for pattern, code in _CURRENCY_PATTERNS:
        if re.search(pattern, text, re.I if code != "$" else 0):
            return code
    return None


def parse_money(cell: str) -> Optional[Tuple[float, Optional[str]]]:
    """
    Parse a monetary table cell into (value_in_billions_of_reporting_currency, currency).

    Returns None when the cell is not a monetary amount, or when it has no stated
    currency or no scale word (e.g. "402,836"), because it can't be placed on a
    USD-billions scale without guessing.
    """
    if not cell or "%" in cell:
        return None
    text = cell.strip()
    currency = detect_currency(text)
    scale = None
    for pattern, mult in _SCALE_TO_BILLIONS:
        if re.search(pattern, text, re.I):
            scale = mult
            break
    if currency is None or scale is None:
        return None

    m = _NUMBER.search(text)
    if not m:
        return None
    try:
        value = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    return value * scale, currency


def fx_rate(currency: str, fiscal_year: int, basis: str = FLOW, period_end: Optional[date] = None) -> Optional[float]:
    """
    USD per 1 unit of `currency`, or None if the rate can't be obtained.
    FLOW uses the fiscal year's average; POINT_IN_TIME uses the rate on
    `period_end` (the company's own fiscal year end), defaulting to 31 Dec.
    """
    if basis == POINT_IN_TIME:
        return usd_per_unit_on(currency, period_end or date(fiscal_year, 12, 31))
    return usd_per_unit_avg(currency, fiscal_year)


def to_usd_billions(value_billions: float, currency: Optional[str], fiscal_year: int,
                    basis: str = FLOW, period_end: Optional[date] = None) -> Optional[float]:
    """
    Convert a billions amount in `currency` to USD billions.
    Returns None when the currency is unknown or no rate is available, so the
    caller excludes the value instead of comparing it unconverted.
    """
    if currency is None:
        return None
    if currency == "USD":
        return value_billions
    rate = fx_rate(currency, fiscal_year, basis, period_end)
    if rate is None:
        return None
    return value_billions * rate


def monetary_usd_billions(cell: str, fiscal_year: int, basis: str = FLOW,
                          period_end: Optional[date] = None) -> Optional[float]:
    """Parse a table cell and return its USD-billions value, or None if not usable."""
    parsed = parse_money(cell)
    if parsed is None:
        return None
    value_b, currency = parsed
    return to_usd_billions(value_b, currency, fiscal_year, basis, period_end)


def _basis_label(basis: str, fiscal_year: int, period_end: date, currency: str, rate: float) -> str:
    if basis == POINT_IN_TIME:
        return f"{currency}→USD {rate:.4f} ({period_end:%d %b %Y} period-end)"
    return f"{currency}→USD {rate:.4f} (FY{fiscal_year} annual average)"


def normalize_comparison_table(text: str, company_periods: dict) -> str:
    """
    Rewrite the company columns of a markdown comparison table so every monetary
    cell reads as USD billions, e.g. "€43.6B" -> "$49.3B (reported €43.6B)" and
    "$402,836 million" -> "$402.8B". Balance-sheet rows convert at each company's
    own period-end rate, all other money rows at the fiscal-year average. Non-
    monetary cells, the metric column and extra columns (e.g. Investment Insight)
    are left as written.

    If any conversion happened, a footnote lists each rate used, with its date.

    company_periods: {1: (fiscal_year, period_end_date), 2: ..., 3: ...} for
    company columns 1..N.
    A monetary cell that can't be converted is marked rather than dropped.
    """
    n_companies = len(company_periods)
    out_lines = []
    conversions = {}  # (basis, currency, fy, period_end) -> rate actually used

    for line in text.split("\n"):
        if line.count("|") < 3 or line.strip().startswith("|---"):
            out_lines.append(line)
            continue
        raw = line.strip()
        if raw.startswith("|"):
            raw = raw[1:]
        if raw.endswith("|"):
            raw = raw[:-1]
        cells = [c.strip() for c in raw.split("|")]
        if len(cells) < n_companies + 1:
            out_lines.append(line)
            continue

        metric = cells[0].replace("**", "")
        basis = basis_for_metric(metric)
        for idx in range(1, n_companies + 1):
            cell = cells[idx]
            parsed = parse_money(cell)
            if parsed is None:
                continue
            _, currency = parsed
            fy, period_end = company_periods[idx]
            usd = monetary_usd_billions(cell, fy, basis, period_end)
            if usd is None:
                cells[idx] = f"{cell} (not comparable: no FX rate on file)"
            elif currency == "USD":
                cells[idx] = f"${usd:,.1f}B"
            else:
                cells[idx] = f"${usd:,.1f}B (reported {cell.strip()})"
                rate = fx_rate(currency, fy, basis, period_end)
                conversions[(basis, currency, fy, period_end)] = rate
        out_lines.append("| " + " | ".join(cells) + " |")

    result = "\n".join(out_lines)
    if conversions:
        notes = "; ".join(
            _basis_label(b, fy, pe, cur, rate) for (b, cur, fy, pe), rate in conversions.items()
        )
        result += (
            f"\n\n*Currency conversions used: {notes}. Source: {FX_SOURCE}. "
            f"Flow items (income, cash flow) use each fiscal year's average; balance-sheet "
            f"items (assets, debt) use each company's own fiscal-year-end rate.*"
        )
    return result
