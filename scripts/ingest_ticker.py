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

When run interactively without --form-types, you first choose what to view:
    1) All filing types together
    2) 10-K only
    3) 10-Q only
    4) 8-K only
    b) Back / q) Quit
Picking a single type re-shows the table filtered to just that type. At the
selection prompt below it, 'b' (or 'back') returns to this menu instead of
quitting outright, so you can switch filing types without restarting.

At the selection prompt, mix and match any of:
    1,3,5           by row number
    1-4             by row range
    FY2023,FY2024   by fiscal year — selects that year's 10-K(s)
    2025Q1,2025Q3   by fiscal quarter — selects that quarter's 10-Q(s)
                    (quarter is the TICKER's OWN fiscal quarter, e.g. Apple's
                    Q1 covers Oct-Dec — see the "Available fiscal quarters"
                    line printed before the prompt)
    all             everything listed
    b               back to the filing-type menu
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


def _prompt_filing_type_menu() -> list | str | None:
    """
    Show the "what do you want to view" menu.
    Returns a list of form types to filter to, None to view everything
    together, "b" to go back and pick a different ticker, or "q" to quit
    entirely.
    """
    print("\nWhat would you like to view?")
    print("  1) All filing types together")
    print("  2) 10-K only")
    print("  3) 10-Q only")
    print("  4) 8-K only")
    print("  b) Back (choose a different ticker)")
    print("  q) Quit")

    choice = input("Choice: ").strip().lower()
    if choice in ("q", "quit"):
        return "q"
    if choice in ("b", "back"):
        return "b"
    if choice in ("2", "10-k", "10k"):
        return ["10-K"]
    if choice in ("3", "10-q", "10q"):
        return ["10-Q"]
    if choice in ("4", "8-k", "8k"):
        return ["8-K"]
    # "1", "all", empty input, or anything unrecognized -> show everything
    return None


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
    parser.add_argument("--form-types", nargs="+", default=None, choices=VALID_FORM_TYPES,
                         help="Which filing types to list (default: prompt interactively for all three)")
    parser.add_argument("--limit", type=int, default=25,
                         help="Max filings to list, most recent first (default: 25)")
    parser.add_argument("--all", action="store_true",
                         help="Ingest every listed filing without prompting for selection")
    parser.add_argument("--no-ingest", action="store_true",
                         help="Only download/render PDFs, skip vector ingestion")
    args = parser.parse_args()

    # Only show the filing-type menu when the caller didn't already pin down
    # form types on the command line — --form-types stays fully scriptable.
    explicit_form_types = args.form_types is not None
    fetch_types = args.form_types if explicit_form_types else list(VALID_FORM_TYPES)

    # args.ticker only seeds the very first loop iteration — after that
    # (or if omitted entirely) the user is prompted fresh each time, so
    # choosing "back" at the filing-type menu can land on a different ticker
    # without restarting the script.
    pending_ticker = args.ticker

    async with SecEdgarFetcher() as fetcher:
        while True:
            if pending_ticker:
                ticker = pending_ticker.strip().upper()
                pending_ticker = None
            else:
                ticker = input("\nTicker to ingest (e.g. AAPL, or 'q' to quit): ").strip().upper()

            if not ticker or ticker.lower() in ("q", "quit"):
                print("Cancelled." if ticker else "No ticker provided, exiting.")
                return

            print(f"\nFetching available filings for {ticker} from SEC EDGAR...")
            try:
                available_all = await fetcher.list_filings(ticker, form_types=fetch_types)
            except ValueError as e:
                print(f"CIK not found: {e}")
                continue  # back to ticker prompt

            if not available_all:
                print(f"No {'/'.join(fetch_types)} filings found for {ticker}.")
                continue  # back to ticker prompt

            # available_all is every fetched filing across all requested form
            # types, most recent first — NOT yet capped to --limit. Capping
            # here (before the interactive menu filters down to one type)
            # would let frequent 8-K/10-Q filings crowd out 10-Ks from the
            # window entirely, so --limit is applied per form-type selection
            # below instead, after filtering.

            filtered = None
            selected_indices = None
            labels = None
            change_ticker = False

            if explicit_form_types or args.all:
                # Non-interactive / scripted path — form_types was already
                # pinned by the caller, so capping here is equivalent to
                # capping post-filter.
                filtered = available_all[: args.limit]
                labels = [_label_filing(f, ticker) for f in filtered]
                _print_filing_table(filtered, labels)
                print(f"Showing {len(filtered)} most recent filing(s) (use --limit to see more).")

                if args.all:
                    selected_indices = list(range(1, len(filtered) + 1))
                else:
                    raw = input(
                        "Select filings to ingest (row numbers, 'FY2024', '2025Q1', 'all', or 'q' to quit): "
                    ).strip()
                    if raw.lower() == "q":
                        print("Cancelled.")
                        return
                    selected_indices = _parse_selection(raw, filtered, labels)
                    if not selected_indices:
                        print("No valid selection made, exiting.")
                        return
            else:
                # Interactive path — filing-type menu, with 'back'/'change ticker' support.
                while True:
                    menu_choice = _prompt_filing_type_menu()
                    if menu_choice == "q":
                        print("Cancelled.")
                        return
                    if menu_choice == "b":
                        change_ticker = True
                        break

                    chosen_types = menu_choice or fetch_types
                    filtered = [f for f in available_all if f["form"] in chosen_types]
                    if not filtered:
                        print(f"No {'/'.join(chosen_types)} filings found for {ticker}.")
                        continue

                    # Cap AFTER narrowing to the chosen type(s), so picking
                    # "10-K only" shows the most recent `limit` 10-Ks — not
                    # whatever 10-Ks happened to survive an earlier cap
                    # applied across all form types combined.
                    filtered = filtered[: args.limit]

                    labels = [_label_filing(f, ticker) for f in filtered]
                    _print_filing_table(filtered, labels)
                    print(f"Showing {len(filtered)} filing(s) (use --limit to see more).")

                    raw = input(
                        "Select filings to ingest (row numbers, 'FY2024', '2025Q1', 'all', "
                        "'b' for filing-type menu, 't' to change ticker, or 'q' to quit): "
                    ).strip()
                    if raw.lower() == "q":
                        print("Cancelled.")
                        return
                    if raw.lower() in ("b", "back"):
                        continue
                    if raw.lower() in ("t", "ticker"):
                        change_ticker = True
                        break

                    selected_indices = _parse_selection(raw, filtered, labels)
                    if not selected_indices:
                        print("No valid selection made — back to the filing-type menu.")
                        continue

                    break

            if change_ticker:
                continue  # back to the ticker prompt

            selected = [filtered[i - 1] for i in selected_indices]
            accession_filter = {f["accession"] for f in selected}

            print(f"\nIngesting {len(selected)} filing(s) for {ticker}:")
            for f, i in zip(selected, selected_indices):
                label = labels[i - 1]
                label_str = f" [{label}]" if label else ""
                print(f"  - {f['form']}{label_str} (period end: {f.get('period_end_date') or 'unknown'}, filed {f['filing_date']})")

            summary = await fetcher.fetch_filings(
                ticker=ticker,
                form_types=list({f["form"] for f in selected}),
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

            if explicit_form_types or args.all:
                # Scripted invocation — single-shot, exit after one ingestion.
                return

            again = input("\nIngest another ticker? (y/N): ").strip().lower()
            if again not in ("y", "yes"):
                return


if __name__ == "__main__":
    asyncio.run(main())
