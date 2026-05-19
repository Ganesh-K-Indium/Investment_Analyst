"""
Options Intelligence MCP Server — port 8568.

All analytics are computed by OptionsAnalytics (deterministic Python).
The LLM sub-agent receives only the structured JSON output for narration.
"""
import asyncio
import traceback
from typing import Optional

from dotenv import load_dotenv
from fastmcp import FastMCP

from analytics import OptionsAnalytics
from visualization import build_oi_chart

load_dotenv()

options_server = FastMCP(
    "options_intelligence",
    instructions="""
    # Options Intelligence MCP Server

    Provides rule-based options chain analytics. All pattern detection is deterministic Python —
    no LLM reasoning over raw option tables.

    ## Tools:
    - `analyze_options_chain`: Full analytics pipeline (OI concentration, max pain, smart money,
      unusual activity, support/resistance). Use this FIRST for any options query.
    - `get_oi_chart`: Generates a grouped-bar Open Interest chart (calls vs puts) and returns
      a Cloudinary URL. Always call this after analyze_options_chain for visual context.
    - `get_options_expiration_dates`: Lists all available expirations with DTE buckets.
    """,
)

_analytics = OptionsAnalytics()


# ─── Tool 1: Full analytics pipeline ───────────────────────────────────────

@options_server.tool(
    name="analyze_options_chain",
    description=(
        "Run the full rule-based options analytics pipeline for a ticker. "
        "Returns structured JSON with: aggregate put/call ratio and sentiment, "
        "OI concentration zones (bullish call clusters, bearish put clusters), "
        "support and resistance levels derived from OI, max pain per expiration, "
        "smart money signals (long-dated unusual OI), and unusual volume activity. "
        "Pass an optional expiration_date (YYYY-MM-DD) to focus on a single expiry; "
        "omit it to analyze the nearest 4 near-term + 1 long-dated expirations. "
        "Use this as the primary tool — never reason from raw option tables."
    ),
)
async def analyze_options_chain(
    ticker: str,
    expiration_date: Optional[str] = None,
) -> dict:
    """Wrapper that runs the analytics synchronously in a thread executor."""
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _analytics.analyze(ticker, expiration_date),
        )
        return result
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


# ─── Tool 2: OI distribution chart ─────────────────────────────────────────

@options_server.tool(
    name="get_oi_chart",
    description=(
        "Generate an Open Interest distribution chart for a specific expiration date. "
        "Produces a grouped bar chart (Call OI in green vs Put OI in red) across strike "
        "prices, with vertical lines marking current price and max pain. "
        "Uploads to Cloudinary and returns the image URL. "
        "Args: ticker (str), expiration_date (YYYY-MM-DD string). "
        "Call this after analyze_options_chain to give users a visual."
    ),
)
async def get_oi_chart(
    ticker: str,
    expiration_date: str,
) -> dict:
    """Fetch chain data for the chart window and render the OI bar chart."""
    try:
        loop = asyncio.get_event_loop()
        chain_data = await loop.run_in_executor(
            None,
            lambda: _analytics.get_chain_for_chart(ticker, expiration_date),
        )

        if "error" in chain_data:
            return chain_data

        result = await build_oi_chart(chain_data)
        return result

    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


# ─── Tool 3: Expiration date listing ────────────────────────────────────────

@options_server.tool(
    name="get_options_expiration_dates",
    description=(
        "List all available options expiration dates for a ticker, bucketed by "
        "days-to-expiration: near_term_le30 (≤30 DTE), mid_term_31_90 (31–90 DTE), "
        "long_dated_gt90 (>90 DTE). Each entry includes the expiration date string "
        "and exact DTE count. Use this to let users select a specific expiration "
        "before calling get_oi_chart."
    ),
)
async def get_options_expiration_dates(ticker: str) -> dict:
    """Return all expirations with DTE buckets."""
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _analytics.get_expiration_dates_with_dte(ticker),
        )
        return result
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


# ─── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    options_server.run(transport="streamable-http", host="0.0.0.0", port=8568)
