#!/usr/bin/env python3
"""
Resumable batch PDF ingestion into Qdrant.

Ingests many PDFs (10-K / 10-Q / 8-K) in one run with a persistent progress
file, so a crash or an error on one file never loses progress on the rest —
re-running the same command skips everything already marked "success" and
picks up exactly where it left off.

Input modes (pick one):

  --manifest manifest.json
      Explicit list of files with per-file ticker/filing_type, e.g.:
      [
        {"path": "/data/AAPL_10K_2024.pdf", "ticker": "AAPL", "filing_type": "10-K"},
        {"path": "/data/AAPL_10Q_2024Q3.pdf", "ticker": "AAPL", "filing_type": "10-Q"}
      ]
      "filing_type" is optional per-entry (auto-detected from filename if omitted).

  --dir /path/to/pdfs --ticker AAPL --filing-type 10-Q
      Recursively ingests every *.pdf under the directory, applying the same
      ticker/filing_type to all of them. Omit --filing-type to auto-detect
      per file from its filename instead.

Progress file (default: <manifest_or_dir_name>.progress.json next to the
input) records, per file: status (success/failed), error, timestamp, and
ingestion result. Use --retry-failed to re-attempt files marked "failed"
instead of skipping them; --stop-on-error aborts the whole run on the first
failure instead of continuing to the next file.

Examples:
    python ingestion/batch_ingest.py --manifest q3_10q_batch.json
    python ingestion/batch_ingest.py --manifest q3_10q_batch.json --retry-failed
    python ingestion/batch_ingest.py --dir ./10q_filings/AAPL --ticker AAPL --filing-type 10-Q
"""

import argparse
import json
import os
import sys
import traceback
from datetime import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.insert(0, project_root)
sys.path.insert(0, current_dir)

from tqdm import tqdm
from ingestion.ingest_pdf import ingest_pdf

VALID_FILING_TYPES = ("10-K", "10-Q", "8-K")


def load_manifest(manifest_path: str) -> list:
    with open(manifest_path, "r", encoding="utf-8") as f:
        entries = json.load(f)
    for entry in entries:
        if "path" not in entry:
            raise ValueError(f"Manifest entry missing 'path': {entry}")
        entry["path"] = os.path.abspath(entry["path"])
    return entries


def build_entries_from_dir(dir_path: str, ticker: str, filing_type: str) -> list:
    entries = []
    for root, _, files in os.walk(dir_path):
        for name in sorted(files):
            if name.lower().endswith(".pdf"):
                entries.append({
                    "path": os.path.abspath(os.path.join(root, name)),
                    "ticker": ticker,
                    "filing_type": filing_type,
                })
    return entries


def default_progress_path(manifest: str, directory: str) -> str:
    source = manifest or directory
    base = os.path.splitext(os.path.basename(source.rstrip("/")))[0]
    return os.path.join(os.path.dirname(os.path.abspath(source)), f"{base}.progress.json")


def load_progress(progress_path: str) -> dict:
    if os.path.exists(progress_path):
        with open(progress_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_progress(progress_path: str, progress: dict):
    tmp_path = progress_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2)
    os.replace(tmp_path, progress_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest", help="Path to a JSON manifest of {path, ticker, filing_type} entries.")
    source.add_argument("--dir", help="Directory to recursively scan for PDFs.")

    parser.add_argument("--ticker", default=None, help="Ticker to apply to all files (required with --dir).")
    parser.add_argument("--filing-type", choices=VALID_FILING_TYPES, default=None,
                        help="Filing type to apply to all files (--dir mode). Omit to auto-detect per filename.")
    parser.add_argument("--progress-file", default=None, help="Path to the progress JSON file (default: derived from input).")
    parser.add_argument("--retry-failed", action="store_true", help="Re-attempt files previously marked 'failed'.")
    parser.add_argument("--stop-on-error", action="store_true", help="Abort the whole run on the first failure.")
    args = parser.parse_args()

    if args.dir and not args.ticker:
        parser.error("--ticker is required when using --dir")

    if args.manifest:
        entries = load_manifest(args.manifest)
    else:
        entries = build_entries_from_dir(args.dir, args.ticker, args.filing_type)

    if not entries:
        print("No PDF files found to ingest.")
        sys.exit(0)

    progress_path = args.progress_file or default_progress_path(args.manifest, args.dir)
    progress = load_progress(progress_path)
    print(f"Progress file: {progress_path}")
    if progress:
        done = sum(1 for v in progress.values() if v.get("status") == "success")
        failed = sum(1 for v in progress.values() if v.get("status") == "failed")
        print(f"Resuming: {done} already succeeded, {failed} previously failed.")

    to_process = []
    for entry in entries:
        path = entry["path"]
        prior = progress.get(path)
        if prior and prior.get("status") == "success":
            continue
        if prior and prior.get("status") == "failed" and not args.retry_failed:
            continue
        to_process.append(entry)

    skipped = len(entries) - len(to_process)
    print(f"{len(entries)} total file(s), {skipped} skipped (already done), {len(to_process)} to process.\n")

    if not to_process:
        print("Nothing to do — all files already ingested successfully. Use --retry-failed to reattempt failures.")
        sys.exit(0)

    newly_failed = []
    newly_succeeded = []

    for entry in tqdm(to_process, desc="Ingesting", unit="file"):
        path = entry["path"]
        ticker = entry.get("ticker")
        filing_type = entry.get("filing_type")

        if not os.path.exists(path):
            progress[path] = {
                "status": "failed",
                "error": f"File not found: {path}",
                "timestamp": str(datetime.now()),
            }
            save_progress(progress_path, progress)
            newly_failed.append((path, "File not found"))
            print(f"\n[FAILED] {path}\n  File not found")
            if args.stop_on_error:
                break
            continue

        try:
            result = ingest_pdf(path, ticker=ticker, filing_type=filing_type)
        except Exception:
            tb = traceback.format_exc()
            progress[path] = {
                "status": "failed",
                "error": tb,
                "timestamp": str(datetime.now()),
            }
            save_progress(progress_path, progress)
            newly_failed.append((path, tb.strip().splitlines()[-1]))
            print(f"\n[FAILED] {path}\n{tb}")
            if args.stop_on_error:
                break
            continue

        if result.get("success"):
            progress[path] = {
                "status": "success",
                "ticker": result.get("ticker"),
                "filing_type": result.get("filing_type"),
                "text_chunks": result.get("text_chunks", 0),
                "image_count": result.get("image_count", 0),
                "timestamp": str(datetime.now()),
            }
            save_progress(progress_path, progress)
            newly_succeeded.append(path)
        else:
            error = result.get("error", "Unknown ingestion failure")
            progress[path] = {
                "status": "failed",
                "error": error,
                "timestamp": str(datetime.now()),
            }
            save_progress(progress_path, progress)
            newly_failed.append((path, error))
            print(f"\n[FAILED] {path}\n  {error}")
            if args.stop_on_error:
                break

    print("\n" + "=" * 60)
    print("BATCH INGESTION SUMMARY")
    print("=" * 60)
    print(f"Newly succeeded: {len(newly_succeeded)}")
    print(f"Newly failed:    {len(newly_failed)}")
    print(f"Skipped (already done): {skipped}")
    print(f"Progress file:   {progress_path}")

    if newly_failed:
        print("\nFailures (re-run the same command to retry, add --retry-failed):")
        for path, error in newly_failed:
            first_line = error.strip().splitlines()[0] if error else "Unknown error"
            print(f"  - {path}\n      {first_line}")
        sys.exit(1)


if __name__ == "__main__":
    main()
