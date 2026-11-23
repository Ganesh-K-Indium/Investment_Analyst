"""
Market-data providers a user connects (Settings is not needed: Data Sources > Add data source) and the quant agent queries.

Each provider is a small declarative entry: its fixed address, how its key is sent, and named operations that map to its endpoints. One
generic runner executes them with the user's own key, so adding a provider is data, not code. Operations share names across providers
(quote, history, profile, income_statement, balance_sheet, cash_flow, news, search) so the agent asks for the same thing everywhere.

`custom` is a user's own REST API (an in-house service, or a Bloomberg / Refinitiv / LSEG enterprise gateway they run): GET only, address
checked on every call, no redirects. Bloomberg and Reuters themselves have no open internet API, so they are reached this way.
"""
import asyncio
import datetime as dt
import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote as urlquote

import httpx

from app.services import user_mcp

logger = logging.getLogger("app.data_sources")

MAX_SOURCES_PER_USER = 10
MAX_OUTPUT_CHARS = 20_000
TIMEOUT = 25
SYMBOL = re.compile(r"^[A-Za-z0-9.\-^=:/ ]{1,30}$")


def _op(path: str, query: Optional[Dict[str, str]] = None, args: Optional[List[str]] = None, help: str = "") -> Dict[str, Any]:
    return {"path": path, "query": query or {}, "args": args or ["symbol"], "help": help}


# "$name" in a path or query value is an argument; anything else is a constant.
CATALOG: Dict[str, Dict[str, Any]] = {
    "alpha_vantage": {
        "label": "Alpha Vantage", "base": "https://www.alphavantage.co", "auth": {"in": "query", "name": "apikey"},
        "hint": "Free key at alphavantage.co (the key 'demo' works for IBM). Free keys are limited to a few calls per minute.",
        "ops": {
            "quote": _op("/query", {"function": "GLOBAL_QUOTE", "symbol": "$symbol"}, help="latest price, change, volume"),
            "history": _op("/query", {"function": "TIME_SERIES_DAILY", "symbol": "$symbol", "outputsize": "compact"}, help="daily prices, last 100 days"),
            "profile": _op("/query", {"function": "OVERVIEW", "symbol": "$symbol"}, help="company overview and key ratios"),
            "income_statement": _op("/query", {"function": "INCOME_STATEMENT", "symbol": "$symbol"}),
            "balance_sheet": _op("/query", {"function": "BALANCE_SHEET", "symbol": "$symbol"}),
            "cash_flow": _op("/query", {"function": "CASH_FLOW", "symbol": "$symbol"}),
            "news": _op("/query", {"function": "NEWS_SENTIMENT", "tickers": "$symbol", "limit": "20"}, help="news with sentiment"),
            "search": _op("/query", {"function": "SYMBOL_SEARCH", "keywords": "$query"}, ["query"], "find a ticker by name"),
        },
    },
    "polygon": {
        "label": "Polygon.io", "base": "https://api.polygon.io", "auth": {"in": "query", "name": "apiKey"},
        "hint": "Key from polygon.io (the free plan has end-of-day data).",
        "ops": {
            "quote": _op("/v2/aggs/ticker/$symbol/prev", help="previous day's bar"),
            "history": _op("/v2/aggs/ticker/$symbol/range/1/day/$from/$to", {"adjusted": "true", "sort": "desc", "limit": "120"}, ["symbol", "from?", "to?"], "daily bars; from/to as YYYY-MM-DD (default: the last year)"),
            "profile": _op("/v3/reference/tickers/$symbol"),
            "income_statement": _op("/vX/reference/financials", {"ticker": "$symbol", "limit": "4"}, help="reported financials (income, balance sheet, cash flow)"),
            "news": _op("/v2/reference/news", {"ticker": "$symbol", "limit": "20"}),
            "search": _op("/v3/reference/tickers", {"search": "$query", "limit": "10"}, ["query"]),
        },
    },
    "finnhub": {
        "label": "Finnhub", "base": "https://finnhub.io", "auth": {"in": "header", "name": "X-Finnhub-Token"},
        "hint": "Free key at finnhub.io.",
        "ops": {
            "quote": _op("/api/v1/quote", {"symbol": "$symbol"}),
            "profile": _op("/api/v1/stock/profile2", {"symbol": "$symbol"}),
            "income_statement": _op("/api/v1/stock/metric", {"symbol": "$symbol", "metric": "all"}, help="key financial metrics and ratios"),
            "news": _op("/api/v1/company-news", {"symbol": "$symbol", "from": "$from", "to": "$to"}, ["symbol", "from?", "to?"], "company news; dates YYYY-MM-DD (default: the last 7 days)"),
            "search": _op("/api/v1/search", {"q": "$query"}, ["query"]),
        },
    },
    "twelve_data": {
        "label": "Twelve Data", "base": "https://api.twelvedata.com", "auth": {"in": "query", "name": "apikey"},
        "hint": "Key from twelvedata.com (the free plan allows 8 calls a minute).",
        "ops": {
            "quote": _op("/quote", {"symbol": "$symbol"}),
            "history": _op("/time_series", {"symbol": "$symbol", "interval": "1day", "outputsize": "120"}),
            "profile": _op("/profile", {"symbol": "$symbol"}),
            "income_statement": _op("/income_statement", {"symbol": "$symbol"}),
            "balance_sheet": _op("/balance_sheet", {"symbol": "$symbol"}),
            "cash_flow": _op("/cash_flow", {"symbol": "$symbol"}),
            "search": _op("/symbol_search", {"symbol": "$query"}, ["query"]),
        },
    },
    "fmp": {
        "label": "Financial Modeling Prep", "base": "https://financialmodelingprep.com", "auth": {"in": "query", "name": "apikey"},
        "hint": "Key from financialmodelingprep.com.",
        "ops": {
            "quote": _op("/stable/quote", {"symbol": "$symbol"}),
            "history": _op("/stable/historical-price-eod/light", {"symbol": "$symbol"}),
            "profile": _op("/stable/profile", {"symbol": "$symbol"}),
            "income_statement": _op("/stable/income-statement", {"symbol": "$symbol", "limit": "4"}),
            "balance_sheet": _op("/stable/balance-sheet-statement", {"symbol": "$symbol", "limit": "4"}),
            "cash_flow": _op("/stable/cash-flow-statement", {"symbol": "$symbol", "limit": "4"}),
            "news": _op("/stable/news/stock", {"symbols": "$symbol", "limit": "20"}),
            "search": _op("/stable/search-symbol", {"query": "$query"}, ["query"]),
        },
    },
    "custom": {
        "label": "Custom API", "base": None, "auth": None,
        "hint": "Your own REST API or enterprise gateway (for example one that fronts Bloomberg or Refinitiv). Read-only GET requests.",
        "ops": {"get": {"path": "$path", "query": {}, "args": ["path", "query?"], "help": "GET a path on your API; query is a JSON object of parameters"}},
    },
}

_ERROR_KEYS = ("Error Message", "Information", "Note", "error", "errors", "message", "status")


class SourceError(Exception):
    pass


def _dates(args: Dict[str, Any], days: int) -> Dict[str, Any]:
    today = dt.date.today()
    out = dict(args)
    out.setdefault("to", today.isoformat())
    out.setdefault("from", (today - dt.timedelta(days=days)).isoformat())
    return out


def build_request(provider: str, op: str, args: Dict[str, Any], auth: Optional[Dict[str, str]], base_url: Optional[str]):
    """-> (url, params, headers). Raises SourceError for anything the user or the model got wrong, so nothing odd reaches the network."""
    spec = CATALOG.get(provider)
    if spec is None:
        raise SourceError(f"Unknown provider {provider!r}.")
    o = spec["ops"].get(op)
    if o is None:
        raise SourceError(f"{spec['label']} has no operation {op!r}. Available: {', '.join(spec['ops'])}.")
    args = {k: v for k, v in (args or {}).items() if v not in (None, "")}
    if "from" in o["path"] or "$from" in json.dumps(o["query"]):
        args = _dates(args, 365 if "range" in o["path"] else 7)
    for a in o["args"]:
        if not a.endswith("?") and a not in args:
            raise SourceError(f"{op} needs {a!r}.")
    for k in ("symbol", "query"):
        if k in args and not SYMBOL.match(str(args[k])) and k == "symbol":
            raise SourceError("That does not look like a ticker symbol.")

    def sub(v: str) -> str:
        return re.sub(r"\$(\w+)", lambda m: str(args.get(m.group(1), "")), v)
    path = sub(o["path"]) if provider != "custom" else str(args["path"])
    if provider == "custom":
        if not path.startswith("/") or ".." in path or "//" in path or "://" in path or "@" in path:
            raise SourceError("path must start with / and stay on the server (no .., no full addresses).")
        base = (base_url or "").rstrip("/")
        try:
            params = args.get("query") if isinstance(args.get("query"), dict) else json.loads(args["query"]) if args.get("query") else {}
        except ValueError:
            raise SourceError("query must be a JSON object.")
        if not isinstance(params, dict):
            raise SourceError("query must be a JSON object.")
    else:
        base = spec["base"]
        params = {k: sub(v) for k, v in o["query"].items()}
        path = "/".join(urlquote(seg, safe="") if i else seg for i, seg in enumerate(path.split("/"))) if "$" in o["path"] else path
    headers: Dict[str, str] = {"Accept": "application/json", "User-Agent": "investment-analyst/1.0"}
    a = (spec["auth"] or {}) if provider != "custom" else {"in": (auth or {}).get("in", "header"), "name": (auth or {}).get("name") or "Authorization"}
    if auth and auth.get("value"):
        (headers if a.get("in") == "header" else params)[a["name"]] = auth["value"]
    elif provider != "custom":
        raise SourceError("This source has no key saved.")
    return base + path, params, headers


def _shrink(obj: Any, keep: int = 60) -> Any:
    """Long time series: newest rows only. Everything else is passed through (the output cap handles the rest)."""
    if isinstance(obj, dict):
        if len(obj) > keep + 20 and all(re.match(r"^\d{4}-\d{2}-\d{2}", str(k)) for k in list(obj)[:5]):
            return {k: obj[k] for k in sorted(obj, reverse=True)[:keep]}
        return {k: _shrink(v, keep) for k, v in obj.items()}
    if isinstance(obj, list) and len(obj) > keep + 20:
        return obj[:keep]
    if isinstance(obj, list):
        return [_shrink(v, keep) for v in obj]
    return obj


def _problem(data: Any) -> Optional[str]:
    """Providers often answer 200 with a message instead of data (bad key, rate limit, premium endpoint)."""
    if isinstance(data, dict) and len(data) <= 3:
        for k in _ERROR_KEYS:
            if k in data and isinstance(data[k], (str, list, dict)) and k != "status":
                return str(data[k])[:300]
        if str(data.get("status", "")).lower() in ("error", "failed"):
            return str(data.get("message") or data)[:300]
    return None


async def run(provider: str, op: str, args: Dict[str, Any], auth: Optional[Dict[str, str]], base_url: Optional[str] = None) -> str:
    url, params, headers = build_request(provider, op, args, auth, base_url)
    if provider == "custom":
        user_mcp.check_url(url)                                    # at every call, not only when saved
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as c:
            r = await c.get(url, params=params, headers=headers)
    except httpx.HTTPError as e:
        raise SourceError(f"Could not reach the provider: {type(e).__name__}.")
    if r.status_code in (401, 403):
        raise SourceError("The provider rejected the key (check it, and that your plan includes this data).")
    if r.status_code == 429:
        raise SourceError("The provider's rate limit was reached. Wait a minute and try again.")
    if r.status_code >= 400:
        raise SourceError(f"The provider answered {r.status_code}.")
    try:
        data = r.json()
    except ValueError:
        text = r.text[:MAX_OUTPUT_CHARS]
        return text
    bad = _problem(data)
    if bad:
        raise SourceError(bad)
    out = json.dumps(_shrink(data), default=str)
    return out if len(out) <= MAX_OUTPUT_CHARS else out[:MAX_OUTPUT_CHARS] + f"\n[output cut at {MAX_OUTPUT_CHARS} characters]"


async def test(provider: str, auth: Optional[Dict[str, str]], base_url: Optional[str], test_path: str = "/") -> None:
    """One small call that proves the key works. SourceError when it does not."""
    if provider == "custom":
        await run("custom", "get", {"path": test_path or "/"}, auth, base_url)
    else:
        await run(provider, "quote" if "quote" in CATALOG[provider]["ops"] else "search", {"symbol": "IBM", "query": "IBM"}, auth)


def catalog_view() -> List[Dict[str, Any]]:
    return [{"id": k, "label": v["label"], "hint": v["hint"], "needs_url": k == "custom",
             "operations": [{"name": n, "args": o["args"], "help": o.get("help", "")} for n, o in v["ops"].items()]} for k, v in CATALOG.items()]
