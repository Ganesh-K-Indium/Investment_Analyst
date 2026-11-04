"""
Re-chunk the tables of documents that are already ingested, without re-running ingestion.

Older ingestions stored lossy table markdown (row labels dropped, headers split) inside page chunks and let the
text splitter cut tables mid-row. For every page that has tables this rebuilds the page's chunks with the current
extractor + table-aware chunker (ingestion/table_extractor.py, ingestion/chunking.py), re-embeds them, upserts the
new chunks and deletes that page's old text chunks. Image chunks and pages without tables are not touched.

A page is skipped (never guessed at) unless:
  - the PDF exists locally under sec_filings/, and
  - the start of the page's indexed text equals the start of the PDF page text (the PDF is the ingested copy).

Usage (dry run by default):
    python scripts/reextract_tables.py --ticker GOOGL
    python scripts/reextract_tables.py --ticker GOOGL --apply
    python scripts/reextract_tables.py --all --apply
"""
import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ingestion"))

import fitz  # noqa: E402
from langchain_core.documents import Document  # noqa: E402
from qdrant_client import models  # noqa: E402

from table_extractor import extract_tables_markdown_for_page  # noqa: E402

MARKER = "\n\n[TABLES]\n"
SEC_DIR = ROOT / "sec_filings"
BACKUP_DIR = None  # set by --backup-dir


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def find_pdf(ticker: str, source_file: str):
    cands = [SEC_DIR / ticker.upper() / Path(source_file).name, SEC_DIR / Path(source_file)]
    return next((c for c in cands if c.exists()), None)


async def process_collection(ticker: str, apply: bool, force: bool = False) -> dict:
    import uuid

    from ingestion.pdf_processor1 import ingest_documents_with_hybrid_vectors, init_vector_stores
    from app.services.documents.pages import split_page_content
    from chunking import chunk_page_documents, make_text_splitter

    db, _ = init_vector_stores(collection_name=f"ticker_{ticker.lower()}")
    client = db.async_qdrant_client
    splitter = make_text_splitter()
    stats = dict(docs=0, pages_with_tables=0, rewritten=0, old_chunks=0, new_chunks=0, skipped_text=0, no_pdf=0)

    by_doc = {}
    offset = None
    while True:
        points, offset = await client.scroll(
            db.collection_name, limit=256, offset=offset, with_payload=True, with_vectors=False,
            scroll_filter=models.Filter(must=[models.FieldCondition(key="metadata.content_type", match=models.MatchValue(value="text"))]),
        )
        for p in points:
            m = (p.payload or {}).get("metadata") or {}
            by_doc.setdefault((m.get("content_hash"), m.get("source_file")), {}).setdefault(m.get("page_num"), []).append(p)
        if offset is None:
            break

    for (content_hash, source_file), pages in by_doc.items():
        pdf_path = find_pdf(ticker, source_file or "")
        if not pdf_path:
            stats["no_pdf"] += 1
            continue
        stats["docs"] += 1
        new_docs, new_ids, old_ids, old_points = [], [], [], []
        existing = {str(p.id): p.payload.get('page_content') for pts_ in pages.values() for p in pts_}
        with fitz.open(pdf_path) as pdf:
            for page_num, pts in pages.items():
                if not page_num or not 1 <= page_num <= len(pdf):
                    continue
                if not force and all((p.payload.get('metadata') or {}).get('chunk_kind') for p in pts):
                    continue  # already upgraded (--force redoes these with the current extractor)
                has_tables = any(split_page_content(p.payload.get("page_content") or "")[1] for p in pts)
                if not has_tables:
                    continue
                stats["pages_with_tables"] += 1
                page = pdf[page_num - 1]
                text = page.get_text("text")
                # Same fingerprint the Inspector uses: the PDF page must start where some indexed text chunk starts.
                old_starts = [_norm(split_page_content(p.payload.get("page_content") or "")[0])[:150] for p in pts]
                if _norm(text)[:150] not in [o for o in old_starts if o]:
                    stats["skipped_text"] += 1  # PDF is not the ingested copy for this page
                    continue
                tables_md = extract_tables_markdown_for_page(page, page_num)
                meta = dict((pts[0].payload.get("metadata") or {}))
                for k in ("chunk_kind", "chunk_index"):
                    meta.pop(k, None)
                content = text + (MARKER + tables_md if tables_md else "")
                chunks = chunk_page_documents([Document(page_content=content, metadata=meta)], splitter)
                stats["rewritten"] += 1
                stats["old_chunks"] += len(pts)
                stats["new_chunks"] += len(chunks)
                old_ids += [p.id for p in pts]
                old_points += [{"id": str(p.id), "payload": p.payload} for p in pts]
                for k, c in enumerate(chunks):
                    new_docs.append(c)
                    new_ids.append(str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{content_hash}_page{page_num}_tbl{k}")))
        new_set = set(new_ids)  # ids are deterministic: a chunk written under the same id is overwritten, never deleted
        old_ids = [i for i in old_ids if str(i) not in new_set]
        changed = [(d, i) for d, i in zip(new_docs, new_ids) if existing.get(i) != d.page_content]  # skip identical chunks
        new_docs, new_ids = [d for d, _ in changed], [i for _, i in changed]
        print(f"  {ticker} {Path(source_file).name}: {len(old_ids)} old chunk(s) removed, {len(new_docs)} written")
        if apply and (new_docs or old_ids):
            if BACKUP_DIR:  # old payloads (text only; vectors can be regenerated) so the upgrade can be compared / undone
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                (BACKUP_DIR / f"{ticker}_{Path(source_file).stem}.json").write_text(json.dumps(old_points))
            if new_docs:
                await ingest_documents_with_hybrid_vectors(db, new_docs, new_ids, label="re-chunked page chunk(s)")
            if old_ids:  # only after the new chunks are safely written; Qdrant rejects an empty delete
                await client.delete(db.collection_name, points_selector=models.PointIdsList(points=old_ids))
    return stats


def list_tickers():
    return sorted(p.name for p in SEC_DIR.iterdir() if p.is_dir())


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticker", action="append", help="ticker to process (repeatable)")
    ap.add_argument("--all", action="store_true", help="every ticker folder under sec_filings/")
    ap.add_argument("--backup-dir", help="with --apply: save the replaced chunks' payloads here as JSON")
    ap.add_argument("--force", action="store_true", help="also redo pages already upgraded (after an extractor fix)")
    ap.add_argument("--apply", action="store_true", help="re-embed and upsert (default is a dry run)")
    args = ap.parse_args()
    global BACKUP_DIR
    BACKUP_DIR = Path(args.backup_dir) if args.backup_dir else None
    tickers = list_tickers() if args.all else [t.upper() for t in (args.ticker or [])]
    if not tickers:
        ap.error("pass --ticker TICKER or --all")
    print(("APPLY" if args.apply else "DRY RUN") + f" — {len(tickers)} ticker(s)")
    total = {}
    for t in tickers:
        try:
            s = await process_collection(t, args.apply, args.force)
        except Exception as e:  # collection missing, etc.
            import traceback; traceback.print_exc() if os.getenv("REEXTRACT_DEBUG") else None
            print(f"{t}: skipped ({e})")
            continue
        print(f"{t}: {s}")
        for k, v in s.items():
            total[k] = total.get(k, 0) + v
    print("TOTAL", total)
    if not args.apply:
        print("Dry run only — re-run with --apply to write.")


if __name__ == "__main__":
    asyncio.run(main())
