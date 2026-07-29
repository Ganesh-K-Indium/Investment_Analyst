"""
SEC EDGAR filing fetcher — downloads 10-K / 10-Q / 8-K filings for a ticker,
renders each to PDF, and ingests them into the RAG vector store with the
correct filing_type tag.

Async throughout: HTTP fetches use httpx.AsyncClient, PDF rendering uses
Playwright's async API, and ingestion itself (pdf_processor1.py) is async —
embeddings via OpenAI's async client, upserts via Qdrant's AsyncQdrantClient.
Filings ingest concurrently (bounded by a semaphore) without blocking the
event loop or needing a thread-offload.

Supersedes the old root-level download_10q_pdfs.py (10-Q-only, hardcoded to
AAPL/2025, no ingestion hookup).
"""
import asyncio
import os
import time
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence

import httpx
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "Indium Capital contact@indium.com")
SEC_REQUEST_RATE_LIMIT = 10  # max 10 requests/sec (SEC guideline)

VALID_FORM_TYPES = ("10-K", "10-Q", "8-K")


class SecEdgarFetcher:
    """Fetches SEC filings (10-K/10-Q/8-K), converts them to PDF, and ingests them."""

    def __init__(self):
        self.headers = {
            "User-Agent": SEC_USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
            "Connection": "keep-alive",
        }
        self._last_request_time = 0.0
        self._rate_limit_delay = 1.0 / SEC_REQUEST_RATE_LIMIT
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()

    async def _rate_limited_request(self, url: str) -> httpx.Response:
        """Ensure SEC rate limits are respected."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self._rate_limit_delay:
            await asyncio.sleep(self._rate_limit_delay - elapsed)

        response = await self._client.get(url, headers=self.headers)
        self._last_request_time = time.monotonic()

        response.raise_for_status()
        return response

    async def get_cik(self, ticker: str) -> str:
        """Convert ticker to CIK."""
        url = "https://www.sec.gov/files/company_tickers.json"
        data = (await self._rate_limited_request(url)).json()

        for entry in data.values():
            if entry["ticker"].lower() == ticker.lower():
                return str(entry["cik_str"]).zfill(10)

        raise ValueError(f"CIK not found for ticker {ticker}")

    async def convert_url_to_pdf(self, url: str, output_path: str):
        """Render a SEC HTML filing page to PDF.

        SEC.gov's WAF flags headless Chromium *navigating directly* to
        sec.gov as an "undeclared automated tool" and serves a block page —
        even with a declared User-Agent — while a plain httpx GET with the
        same UA succeeds fine. So: fetch the HTML via httpx (already proven
        to work, same client used for the JSON API calls), then hand that
        content to Playwright via set_content() purely for local rendering —
        the browser never makes a request to sec.gov itself, sidestepping
        the block entirely.
        """
        html_response = await self._rate_limited_request(url)
        html_content = html_response.text

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(user_agent=SEC_USER_AGENT)
                page = await context.new_page()
                # Sub-resources (images/CSS) would still hit sec.gov and could
                # trip the same block — not needed for text extraction, so drop them.
                await page.route("**/*", lambda route: (
                    route.continue_() if route.request.url == "about:blank" or route.request.resource_type == "document"
                    else route.abort()
                ))
                await page.set_content(html_content, wait_until="domcontentloaded", timeout=30000)
                await page.pdf(path=output_path, format="A4", print_background=True)
            finally:
                await browser.close()

    async def fetch_filings(
        self,
        ticker: str,
        form_types: Sequence[str] = VALID_FORM_TYPES,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        output_dir: str = "sec_filings",
        ingest: bool = True,
        max_concurrent_ingests: int = 3,
    ) -> Dict:
        """
        Fetch, download, and (optionally) ingest filings for a ticker.

        Args:
            ticker: Stock ticker symbol.
            form_types: SEC form types to fetch (subset of "10-K", "10-Q", "8-K").
            start_date: Only include filings on/after this date. Defaults to no lower bound.
            end_date: Only include filings on/before this date. Defaults to today.
            output_dir: Directory to save rendered PDFs under (one subfolder per ticker).
            ingest: If True, ingest each downloaded PDF into the vector store.
            max_concurrent_ingests: Bound on concurrent ingestion (embedding/Qdrant calls).

        Returns:
            Summary dict with per-filing results and aggregate counts.
        """
        invalid = [f for f in form_types if f not in VALID_FORM_TYPES]
        if invalid:
            raise ValueError(f"Invalid form_types {invalid}. Must be a subset of {VALID_FORM_TYPES}.")

        end_date = end_date or date.today()

        cik = await self.get_cik(ticker)

        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        data = (await self._rate_limited_request(url)).json()
        filings = data["filings"]["recent"]

        ticker_dir = os.path.join(output_dir, ticker.upper())
        os.makedirs(ticker_dir, exist_ok=True)

        to_fetch = []
        for i in range(len(filings["form"])):
            form = filings["form"][i]
            if form not in form_types:
                continue

            filing_date_str = filings["filingDate"][i]
            filing_date = datetime.strptime(filing_date_str, "%Y-%m-%d").date()
            if start_date and filing_date < start_date:
                continue
            if filing_date > end_date:
                continue

            accession_raw = filings["accessionNumber"][i]
            accession = accession_raw.replace("-", "")
            primary_doc = filings["primaryDocument"][i]

            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik)}/{accession}/{primary_doc}"
            )
            pdf_path = os.path.join(ticker_dir, f"{ticker.upper()}_{form.replace('/', '-')}_{filing_date_str}_{accession}.pdf")

            to_fetch.append({"form": form, "filing_date": filing_date_str, "url": filing_url, "pdf_path": pdf_path})

        results = []
        semaphore = asyncio.Semaphore(max_concurrent_ingests)

        async def _process_one(item: Dict) -> Dict:
            result = {**item, "status": "pending", "error": None, "chunks_added": None}
            try:
                if not os.path.exists(item["pdf_path"]):
                    await self.convert_url_to_pdf(item["url"], item["pdf_path"])
                    result["message"] = "Rendered PDF"
                else:
                    result["message"] = "PDF already exists locally"

                if ingest:
                    async with semaphore:
                        from ingestion.ingest_pdf import ingest_pdf
                        ingest_result = await ingest_pdf(item["pdf_path"], ticker=ticker.upper(), filing_type=item["form"])
                    if ingest_result.get("success"):
                        result["status"] = "ingested"
                        result["chunks_added"] = ingest_result.get("text_chunks", 0)
                    else:
                        result["status"] = "ingest_failed"
                        result["error"] = ingest_result.get("error", "Unknown ingestion error")
                else:
                    result["status"] = "downloaded"

            except Exception as e:
                result["status"] = "failed"
                result["error"] = str(e)

            return result

        results = await asyncio.gather(*(_process_one(item) for item in to_fetch))

        summary = {
            "ticker": ticker.upper(),
            "form_types": list(form_types),
            "date_range": {"start": str(start_date) if start_date else None, "end": str(end_date)},
            "total_filings_found": len(to_fetch),
            "ingested": sum(1 for r in results if r["status"] == "ingested"),
            "downloaded_only": sum(1 for r in results if r["status"] == "downloaded"),
            "failed": sum(1 for r in results if r["status"] in ("failed", "ingest_failed")),
            "filings": list(results),
        }
        return summary


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch and ingest SEC filings for a ticker.")
    parser.add_argument("ticker")
    parser.add_argument("--form-types", nargs="+", default=list(VALID_FORM_TYPES), choices=VALID_FORM_TYPES)
    parser.add_argument("--start-date", type=date.fromisoformat, default=None)
    parser.add_argument("--end-date", type=date.fromisoformat, default=None)
    parser.add_argument("--no-ingest", action="store_true", help="Download PDFs only, skip vector ingestion.")
    parser.add_argument("--output-dir", default="sec_filings")
    args = parser.parse_args()

    async def _main():
        async with SecEdgarFetcher() as fetcher:
            summary = await fetcher.fetch_filings(
                ticker=args.ticker,
                form_types=args.form_types,
                start_date=args.start_date,
                end_date=args.end_date,
                output_dir=args.output_dir,
                ingest=not args.no_ingest,
            )
            print(summary)

    asyncio.run(_main())
