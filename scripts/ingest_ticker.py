#!/usr/bin/env python3
"""
Interactive SEC filing ingestion — pick a ticker, see what's available on
EDGAR (10-K / 10-Q / 8-K), choose exactly which filings to pull in, and
ingest them into the RAG vector store.

Usage:
    python scripts/ingest_ticker.py                  # fully interactive
    python scripts/ingest_ticker.py AAPL             # skip the ticker prompt
    python scripts/ingest_ticker.py AAPL --form-types 10-K 10-Q
    python scripts/ingest_ticker.py AAPL --all       # ingest everything listed, no prompt

At the selection prompt, mix and match any of:
    1,3,5           by row number
    1-4             by row range
    FY2023,FY2024   by fiscal year — selects that year's 10-K(s)
    2025Q1,2025Q3   by fiscal quarter — selects that quarter's 10-Q(s)
                    (quarter is the TICKER's OWN fiscal quarter, e.g. Apple's
                    Q1 covers Oct-Dec — see the "Available fiscal quarters"
                    line printed before the prompt)
    all             everything listed
    q               quit without ingesting anything
"""

import argparse
import asyncio
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.insert(0, project_root)

from ingestion.edgar_fetcher import SecEdgarFetcher, VALID_FORM_TYPES  # noqa: E402
from app.utils.company_mapping import get_fiscal_quarter  # noqa: E402


def _label_filing(f: dict, ticker: str) -> str:
    """
    Human-facing period label for a filing:
      - 10-K -> "FY2025" (fiscal year = period_end_date's calendar year, same
        convention used for the ingested `year` metadata field)
      - 10-Q -> "2025Q3" (fiscal quarter derived from period_end_date + the
        ticker's OWN fiscal calendar — ground truth, not a guess)
      - 8-K  -> "" (a single event has no year/quarter grouping concept)
    """
    period_end_date = f.get("period_end_date")
    if not period_end_date:
        return ""

    year = int(period_end_date[:4])

    if f["form"] == "10-K":
        return f"FY{year}"
    if f["form"] == "10-Q":
        quarter = get_fiscal_quarter(period_end_date, ticker)
        return f"{year}Q{quarter}" if quarter else ""
    return ""


def _parse_selection(raw: str, filings: list, labels: list) -> list:
    """
    Parse a selection string into a sorted list of 1-based row indices.
    Accepts, in any comma-separated mix:
      - plain row numbers ("3")
      - row ranges ("1-4")
      - period labels, case-insensitive ("fy2024", "2025q1")
      - "all" / "*"
    """
    raw = raw.strip().lower()
    if raw in ("all", "*"):
        return list(range(1, len(filings) + 1))

    label_to_indices = {}
    for i, label in enumerate(labels, 1):
        if label:
            label_to_indices.setdefault(label.lower(), []).append(i)

    indices = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue

        if part in label_to_indices:
            indices.update(label_to_indices[part])
            continue

        if "-" in part and part.replace("-", "").isdigit():
            start_s, _, end_s = part.partition("-")
            try:
                start, end = int(start_s), int(end_s)
                indices.update(range(start, end + 1))
                continue
            except ValueError:
                pass

        if part.isdigit():
            indices.add(int(part))

    return sorted(i for i in indices if 1 <= i <= len(filings))


def _print_filing_table(filings: list, labels: list):
    print(f"\n{'#':>3}  {'Form':<6} {'Period':<8} {'Filed':<12} {'Period End':<12} {'Accession'}")
    print("-" * 70)
    for i, (f, label) in enumerate(zip(filings, labels), 1):
        period_end = f.get("period_end_date") or "unknown"
        print(f"{i:>3}  {f['form']:<6} {label:<8} {f['filing_date']:<12} {period_end:<12} {f['accession']}")
    print()

    fiscal_years = sorted({label for f, label in zip(filings, labels) if f["form"] == "10-K" and label}, reverse=True)
    fiscal_quarters = sorted({label for f, label in zip(filings, labels) if f["form"] == "10-Q" and label}, reverse=True)

    if fiscal_years:
        print(f"Available fiscal years (10-K): {', '.join(fiscal_years)}")
    if fiscal_quarters:
        print(f"Available fiscal quarters (10-Q): {', '.join(fiscal_quarters)}")
    if fiscal_years or fiscal_quarters:
        print()


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticker", nargs="?", help="Ticker symbol (prompted if omitted)")
    parser.add_argument("--form-types", nargs="+", default=list(VALID_FORM_TYPES), choices=VALID_FORM_TYPES,
                         help="Which filing types to list (default: all three)")
    parser.add_argument("--limit", type=int, default=25,
                         help="Max filings to list, most recent first (default: 25)")
    parser.add_argument("--all", action="store_true",
                         help="Ingest every listed filing without prompting for selection")
    parser.add_argument("--no-ingest", action="store_true",
                         help="Only download/render PDFs, skip vector ingestion")
    args = parser.parse_args()

    ticker = args.ticker or input("Ticker to ingest (e.g. AAPL): ").strip().upper()
    if not ticker:
        print("No ticker provided, exiting.")
        return

    print(f"\nFetching available filings for {ticker} from SEC EDGAR...")

    async with SecEdgarFetcher() as fetcher:
        try:
            available = await fetcher.list_filings(ticker, form_types=args.form_types)
        except ValueError as e:
            print(f"CIK not found: {e}")
            return

        if not available:
            print(f"No {'/'.join(args.form_types)} filings found for {ticker}.")
            return

        # Most recent first (EDGAR's own order), capped to --limit
        available = available[: args.limit]
        labels = [_label_filing(f, ticker) for f in available]

        _print_filing_table(available, labels)
        print(f"Showing {len(available)} most recent filing(s) (use --limit to see more).")

        if args.all:
            selected_indices = list(range(1, len(available) + 1))
        else:
            raw = input(
                "Select filings to ingest (row numbers, 'FY2024', '2025Q1', 'all', or 'q' to quit): "
            ).strip()
            if raw.lower() == "q":
                print("Cancelled.")
                return
            selected_indices = _parse_selection(raw, available, labels)
            if not selected_indices:
                print("No valid selection made, exiting.")
                return

        selected = [available[i - 1] for i in selected_indices]
        accession_filter = {f["accession"] for f in selected}

        print(f"\nIngesting {len(selected)} filing(s) for {ticker}:")
        for f, i in zip(selected, selected_indices):
            label = labels[i - 1]
            label_str = f" [{label}]" if label else ""
            print(f"  - {f['form']}{label_str} (period end: {f.get('period_end_date') or 'unknown'}, filed {f['filing_date']})")

        summary = await fetcher.fetch_filings(
            ticker=ticker,
            form_types=args.form_types,
            accession_filter=accession_filter,
            ingest=not args.no_ingest,
        )

        print("\n" + "=" * 60)
        print("INGESTION SUMMARY")
        print("=" * 60)
        print(f"Ticker:    {summary['ticker']}")
        print(f"Ingested:  {summary['ingested']}")
        print(f"Failed:    {summary['failed']}")
        if not args.no_ingest:
            for f in summary["filings"]:
                status_marker = "OK" if f["status"] == "ingested" else "FAIL"
                print(f"  [{status_marker}] {f['form']} ({f.get('period_end_date') or 'unknown'}) — "
                      f"{f.get('chunks_added', 0)} chunks" + (f" — {f['error']}" if f.get("error") else ""))


if __name__ == "__main__":
    asyncio.run(main())
