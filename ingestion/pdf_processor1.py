import os
import json
import uuid
import re
import hashlib
import asyncio
import logging
import traceback
from datetime import datetime
import fitz  # PyMuPDF
from tqdm import tqdm
from qdrant_client import models
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from rag.vectordb.client import load_vector_database
from image_data_prep import ImageDescription
from table_extractor import extract_tables_markdown_for_page
from dotenv import load_dotenv

# Import company mapping utility
import sys
# Ensure app/utils is importable
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app.utils.company_mapping import TICKER_TO_COMPANY, get_company_name, get_ticker

load_dotenv()

logger = logging.getLogger("ingestion.pdf_processor1")


def init_vector_stores(collection_name: str = None, use_hybrid_search: bool = None):
    """
    Initialize and return both the database loader and vector store.
    
    Args:
        collection_name: Name of the collection to use. If None, uses default unified collection.
        use_hybrid_search: If True, use hybrid collections with BM25. If None, 
                          auto-detect based on USE_HYBRID_SEARCH env var (default: True)
    
    Returns:
        tuple: (db_loader, vectorstore)
    """
    # Auto-detect hybrid search mode from environment variable if not specified
    if use_hybrid_search is None:
        use_hybrid_search = os.getenv("USE_HYBRID_SEARCH", "true").lower() == "true"
    
    # Initialize with specific collection name if provided
    db_init = load_vector_database(
        use_hybrid_search=use_hybrid_search, 
        collection_name=collection_name
    )
    
    # Get vector store for this collection
    vectorstore = db_init.get_unified_vectorstore()
    
    return db_init, vectorstore

def _banner(tag: str, message: str):
    """
    Clearly-marked stage banner. The ingestion pipeline mixes tqdm progress
    bars (text extraction, sparse embeddings) with plain single-line logs
    (dense embeddings, Qdrant upload) — without a visual break, a real
    multi-second network wait (e.g. the OpenAI embeddings call) reads as a
    silent gap between two unrelated-looking progress bars. This makes every
    major stage transition visually obvious in the terminal.
    """
    logger.info("-" * 70)
    logger.info("[%s] %s", tag, message)


async def ingest_documents_with_hybrid_vectors(db_loader, documents, doc_ids, label: str = "documents"):
    """
    Ingest documents with hybrid vectors (dense + sparse).

    Args:
        db_loader: The load_vector_database instance
        documents: List of LangChain Document objects
        doc_ids: List of document IDs (UUIDs)
        label: Human-facing noun for the stage banners/embedding logs (e.g.
            "text chunk(s)" or "image caption(s)") — this function is shared
            by both the text and image ingestion paths.
    """
    # Extract text content from documents
    texts = [doc.page_content for doc in documents]

    # Generate all embeddings (dense + sparse)
    embeddings_dict = await db_loader.generate_embeddings_for_ingestion(texts, label=label)

    # Build points for Qdrant
    points = []
    for i, doc in enumerate(documents):
        # Build vector dict with dense and sparse embeddings
        vector_dict = {"dense": embeddings_dict['dense'][i]}
        
        # Add sparse vector if available
        if embeddings_dict['sparse'][i] is not None:
            vector_dict["bm25"] = embeddings_dict['sparse'][i]
        
        # Create point
        point = models.PointStruct(
            id=doc_ids[i],
            vector=vector_dict,
            payload={
                "page_content": doc.page_content,
                "metadata": doc.metadata
            }
        )
        points.append(point)
    
    # Upload to Qdrant using the async client directly
    logger.info("Uploading %d %s to collection '%s'...", len(points), label, db_loader.collection_name)
    await db_loader.async_qdrant_client.upsert(
        collection_name=db_loader.collection_name,
        points=points
    )

    return len(points)

def extract_company_name(file_name: str) -> str:
    """
    Extract company name from a file name, handling various patterns.
    Uses centralized mapping utility.
    
    Args:
        file_name: The file name to extract company name from
    
    Returns:
        str: The extracted company name in lowercase
    """
    # Remove file extension
    name_without_ext = os.path.splitext(file_name)[0]
    
    # Replace common separators (-, _, .) with space
    name = re.sub(r'[-_.]', ' ', name_without_ext)
    
    # Remove common year patterns (e.g., 2020, 2021, etc.)
    name = re.sub(r'\b(19|20)\d{2}\b', '', name)

    # Remove SEC accession numbers (long digit runs, e.g. "000032019326000011")
    name = re.sub(r'\b\d{6,}\b', '', name)

    # Remove common document type suffixes (10-K, 10-Q, 8-K, annual/quarterly report)
    name = re.sub(r'\b(10[-\s]?[kq]|8[-\s]?k|annual|quarterly|report)\b', '', name, flags=re.IGNORECASE)
    
    # Remove leftover 1-2 digit fragments (month/day pieces from EDGAR-style
    # filenames like "..._2026-04-30_...", after the year itself was stripped above)
    name = re.sub(r'\b\d{1,2}\b', '', name)

    # Remove any trailing numbers
    name = re.sub(r'\s+\d+\s*$', '', name)
    
    # Clean up extra whitespace
    name = ' '.join(name.split())
    
    if not name:
        return name_without_ext.lower()
    
    company = name.lower()
    
    # Check if the first word is a known ticker symbol
    first_word = company.split()[0]
    mapped_name = get_company_name(first_word)
    
    # If get_company_name returned something different than input (meaning it mapped)
    if mapped_name != first_word:
        remaining_words = ' '.join(company.split()[1:])
        company = f"{mapped_name} {remaining_words}".strip() if remaining_words else mapped_name
    
    return company

VALID_FILING_TYPES = ("10-K", "10-Q", "8-K")


def extract_filing_type_from_filename(file_name: str):
    """
    Best-effort detection of SEC filing type from a file name alone.

    Returns None if no recognizable token is found — callers must not treat
    a missing filename token as "must be a 10-K". Filenames are the weakest
    of the three signals (explicit param > document cover-page text >
    filename), since a user can name an uploaded file anything.
    """
    # Normalize separators to spaces first — \b treats "_" as a word character,
    # so "AAPL_10Q_2024.pdf" would otherwise never match a \b...\b token boundary
    # right after the underscore.
    normalized = re.sub(r'[-_.]', ' ', file_name)

    if re.search(r'\b10\s?k\b', normalized, flags=re.IGNORECASE):
        return "10-K"
    if re.search(r'\b10\s?q\b', normalized, flags=re.IGNORECASE):
        return "10-Q"
    if re.search(r'\b8\s?k\b', normalized, flags=re.IGNORECASE):
        return "8-K"
    return None


def extract_cover_page_info(pdf_document, max_pages: int = 3) -> dict:
    """
    Extract filing_type and period_end_date directly from the document's own
    cover-page text — reliable regardless of how the file was named, since
    SEC filings use a standardized cover page:
      - 10-K:  "FORM 10-K" ... "for the fiscal year ended <date>"
      - 10-Q:  "FORM 10-Q" ... "for the quarterly period ended <date>"
      - 8-K:   "FORM 8-K"  ... "Date of Report (Date of earliest event reported): <date>"

    Returns:
        dict: {"filing_type": Optional[str], "period_end_date": Optional[str] (ISO date)}
        Both None if the cover page doesn't match the standard pattern (e.g. a
        non-SEC or non-standard document) — callers fall back to filename
        heuristics, never to a silent guess.
    """
    result = {"filing_type": None, "period_end_date": None}

    try:
        cover_text = ""
        for i, page in enumerate(pdf_document):
            if i >= max_pages:
                break
            cover_text += page.get_text("text") + "\n"
    except Exception as e:
        logger.warning("Could not read cover-page text for filing_type/period_end_date detection: %s", e)
        return result

    if not cover_text.strip():
        return result

    if re.search(r'\bform\s*10[-\s]?k\b', cover_text, re.IGNORECASE):
        result["filing_type"] = "10-K"
    elif re.search(r'\bform\s*10[-\s]?q\b', cover_text, re.IGNORECASE):
        result["filing_type"] = "10-Q"
    elif re.search(r'\bform\s*8[-\s]?k\b', cover_text, re.IGNORECASE):
        result["filing_type"] = "8-K"

    date_capture = r'([A-Za-z]+\s+\d{1,2},?\s+\d{4})'
    date_match = (
        re.search(r'for\s+the\s+fiscal\s+year\s+ended\s+' + date_capture, cover_text, re.IGNORECASE)
        or re.search(r'for\s+the\s+year\s+ended\s+' + date_capture, cover_text, re.IGNORECASE)
        or re.search(r'for\s+the\s+quarterly\s+period\s+ended\s+' + date_capture, cover_text, re.IGNORECASE)
        or re.search(r'date\s+of\s+report[^:]*:\s*' + date_capture, cover_text, re.IGNORECASE)
        or re.search(r'for\s+the\s+transition\s+period\s+from.*?to\s+' + date_capture, cover_text, re.IGNORECASE | re.DOTALL)
    )

    if date_match:
        try:
            from dateutil import parser as date_parser
            parsed_date = date_parser.parse(date_match.group(1), fuzzy=True)
            result["period_end_date"] = parsed_date.date().isoformat()
        except Exception as e:
            logger.warning("Found a period-end date phrase but couldn't parse '%s': %s", date_match.group(1), e)

    return result


def extract_year_from_filename(file_name: str) -> int:
    """
    Extract year from a file name. Default to current year if not found.

    Handles both a free-standing 4-digit year (e.g. "AAPL_10K_2024.pdf") and
    EDGAR-style filenames where the year is glued to a full YYYYMMDD date with
    no separators (e.g. "aapl-20250628.pdf") — the latter has no word boundary
    after the year digits, so a plain \\b(19|20)\\d{2}\\b match misses it.
    """
    current_year = datetime.now().year
    min_year, max_year = 1995, current_year + 1

    # Free-standing 4-digit year, not adjacent to other digits. Uses digit
    # lookaround rather than \b — \b treats "_" as a word character, so
    # "..._2024.pdf" would otherwise fail to match right after the underscore.
    # Checked FIRST: it has clear boundaries and won't false-match inside an
    # unrelated long digit run (e.g. an accession number containing "2019").
    match = re.search(r'(?<!\d)(19|20)\d{2}(?!\d)', file_name)
    if match:
        year = int(match.group(0))
        if min_year <= year <= max_year:
            return year

    # EDGAR-style contiguous YYYYMMDD date (8 digits, no separators) — take the
    # leading 4 as the year. Only reached if no free-standing year was found.
    for match in re.finditer(r'(19|20)\d{6}', file_name):
        year = int(match.group(0)[:4])
        if min_year <= year <= max_year:
            return year

    return current_year if current_year > 2000 else 2024


def _extract_text_documents(pdf_document, source_file_name, company_name, ticker, content_hash,
                             resolved_year, filing_type, period_end_date, fiscal_quarter) -> list:
    """
    Synchronous, CPU-bound page-by-page text + native-table extraction.

    Called via asyncio.to_thread() from process_pdf_and_stream() — this is
    the single heaviest step in ingestion (full-document PyMuPDF text
    extraction plus per-page table detection), and running it directly on
    the event loop would freeze every other in-flight request (chat, RAG
    queries, other concurrent ingestions) on this same server process for
    however long a large filing takes to parse.
    """
    documents = []
    _banner("TEXT 1/3", f"Extracting text from {len(pdf_document)} pages...")
    for page_num, page in enumerate(tqdm(pdf_document, desc="Extracting text", unit="page")):
        text = page.get_text("text")

        # Native PDF tables (not image-embedded) lose column alignment
        # when flattened to plain text — critical for financial tables
        # where the columns ARE the data (e.g. "2024 | 2023 | 2022").
        # Append a structured markdown rendering when a table is
        # actually detected on this page; a no-op for the common case
        # of pure narrative-text pages.
        try:
            tables_md = extract_tables_markdown_for_page(page, page_num + 1)
            if tables_md:
                text = text + "\n\n[TABLES]\n" + tables_md
        except Exception as e:
            logger.warning("Table extraction failed on page %d: %s", page_num + 1, e)

        if text.strip():
            metadata = {
                "source_file": source_file_name,
                "page_num": page_num + 1,
                "company": company_name,
                "ticker": ticker if ticker else "unknown",
                "content_type": "text",
                "content_hash": content_hash,
                "year": resolved_year,
                "filing_type": filing_type,
                "period_end_date": period_end_date,
                "fiscal_quarter": fiscal_quarter,
                "ingestion_timestamp": str(datetime.now()),
            }
            documents.append(Document(page_content=text, metadata=metadata))
    return documents


def calculate_content_hash(pdf_path: str) -> str:
    """Calculate a deterministic hash of the PDF content."""
    try:
        pdf_document = fitz.open(pdf_path)
        content_hash = hashlib.sha256()
        
        # Include text content from each page
        for page in pdf_document:
            text = page.get_text("text").encode('utf-8')
            content_hash.update(text)
            
        return content_hash.hexdigest()
    except Exception as e:
        logger.error("Error calculating content hash: %s", e)
        return ""

def calculate_image_content_hash(image_data: bytes) -> str:
    """Calculate a deterministic hash of individual image content."""
    try:
        return hashlib.sha256(image_data).hexdigest()
    except Exception as e:
        logger.error("Error calculating image content hash: %s", e)
        return ""

def generate_doc_id(doc_metadata: dict, index: int, doc_type: str = "text") -> str:
    """Generate a deterministic UUID for a document."""
    if doc_type == "text":
        # Include content_hash in the ID generation if available
        content_hash = doc_metadata.get('content_hash', '')
        return str(uuid.uuid5(uuid.NAMESPACE_DNS,
                           f"{content_hash}_page{doc_metadata['page_num']}_{index}"))
    else:  # image
        return str(uuid.uuid5(uuid.NAMESPACE_DNS,
                           f"{doc_metadata.get('company', 'NA')}_{doc_metadata['source_file']}_{index}"))

async def check_document_exists(db_loader, source_file_name: str, doc_type: str = "text", content_hash: str = None, image_hashes: dict = None) -> tuple[bool, list]:
    """
    Check if a document already exists in the vector store using metadata filters.

    Args:
        db_loader: The load_vector_database instance (uses its async_qdrant_client)
        source_file_name: Name of the source file
        doc_type: Type of document ("text" or "image")
        content_hash: Hash of the document content for duplicate detection

    Returns:
        tuple[bool, list]: (exists, existing_points)
    """
    try:
        logger.info("\n=== Checking existence of %s (%s) ===", source_file_name, doc_type)
        logger.info("Collection name: %s", db_loader.collection_name)

        client = db_loader.async_qdrant_client
        collection_name = db_loader.collection_name

        # Build the filter based on content hash if available, otherwise fallback to filename
        filter_conditions = [
            models.FieldCondition(
                key="metadata.content_type",
                match=models.MatchValue(value=doc_type)
            )
        ]

        # For images, check individual image hashes first if available
        if doc_type == "image" and image_hashes:
            # Check if any individual image hash already exists
            for img_id, img_info in image_hashes.items():
                individual_filter = models.Filter(
                    must=[
                        models.FieldCondition(
                            key="metadata.content_type",
                            match=models.MatchValue(value="image")
                        ),
                        models.FieldCondition(
                            key="metadata.image_content_hash",
                            match=models.MatchValue(value=img_info["hash"])
                        )
                    ]
                )

                count_response = await client.count(
                    collection_name=collection_name,
                    count_filter=individual_filter
                )

                if count_response.count > 0:
                    logger.info("Found existing image with hash %s...", img_info['hash'][:16])
                    points = (await client.scroll(
                        collection_name=collection_name,
                        scroll_filter=individual_filter,
                        with_payload=True,
                        limit=count_response.count
                    ))[0]
                    return True, points

            logger.info("No individual image hashes found, checking by PDF content hash...")

        if content_hash:
            filter_conditions.append(
                models.FieldCondition(
                    key="metadata.content_hash",
                    match=models.MatchValue(value=content_hash)
                )
            )
        else:
            filter_conditions.append(
                models.FieldCondition(
                    key="metadata.source_file",
                    match=models.MatchValue(value=source_file_name)
                )
            )

        search_filter = models.Filter(must=filter_conditions)

        count_response = await client.count(
            collection_name=collection_name,
            count_filter=search_filter
        )
        logger.info("\nDebug: Found %d matching points", count_response.count)

        if count_response.count > 0:
            points = (await client.scroll(
                collection_name=collection_name,
                scroll_filter=search_filter,
                with_payload=True,
                limit=count_response.count
            ))[0]

            return True, points

        return False, []

    except Exception as e:
        logger.error("Error checking document existence: %s", e)
        return False, []

async def find_new_image_hashes(db_loader, image_hashes: dict) -> dict:
    """
    Given this document's freshly-computed image_hashes ({img_id: {hash, path, ...}}),
    return the subset whose hash does NOT already exist in the collection — the
    genuinely new images to analyze/ingest.

    Checks each image's hash individually (concurrently) rather than short-circuiting
    on the first match: a single shared image (e.g. a repeated company logo or
    boilerplate chart appearing in an earlier filing too) must not cause the ENTIRE
    document's images to be skipped — only that specific image should be treated as
    a duplicate, while every genuinely new chart/table still gets ingested.
    """
    semaphore = asyncio.Semaphore(10)

    async def _check_one(img_id, img_info):
        async with semaphore:
            try:
                count_response = await db_loader.async_qdrant_client.count(
                    collection_name=db_loader.collection_name,
                    count_filter=models.Filter(must=[
                        models.FieldCondition(key="metadata.content_type", match=models.MatchValue(value="image")),
                        models.FieldCondition(key="metadata.image_content_hash", match=models.MatchValue(value=img_info["hash"])),
                    ])
                )
                return img_id, count_response.count == 0
            except Exception as e:
                logger.warning("Could not check existence of image hash %s...: %s; treating as new", img_info['hash'][:16], e)
                return img_id, True

    results = await asyncio.gather(*(_check_one(img_id, info) for img_id, info in image_hashes.items()))
    is_new = dict(results)
    return {img_id: info for img_id, info in image_hashes.items() if is_new.get(img_id)}


async def process_pdf_and_get_result(uploaded_pdf_path: str, ticker: str = None, filing_type: str = None,
                                      period_end_date: str = None, year: int = None) -> dict:
    """
    Process a PDF file and return a structured result.

    Args:
        uploaded_pdf_path: Path to the PDF file
        ticker: Ticker symbol (optional)
        filing_type: SEC filing type - "10-K", "10-Q", or "8-K" (optional). Resolution
            order: this explicit value > detected from the document's own cover-page
            text > detected from the filename. Falls back to "10-K" with a loud
            warning only if none of the three resolve — never a silent guess.
        period_end_date: ISO date (YYYY-MM-DD) of the exact period this filing covers
            (fiscal year end for a 10-K, fiscal quarter end for a 10-Q, event date for
            an 8-K) — optional; pass this when the caller has an authoritative value
            (e.g. SEC EDGAR's reportDate). Otherwise detected from cover-page text.
        year: Fiscal/Report year (optional; explicit year override for metadata tagging).

    Returns:
        dict: Processing result with status and details
    """
    result = {
        "success": False,
        "file_name": os.path.basename(uploaded_pdf_path),
        "ticker": ticker,
        "filing_type": filing_type,
        "period_end_date": period_end_date,
        "year": year,
        "fiscal_quarter": None,
        "text_processed": False,
        "text_already_existed": False,
        "text_chunks": 0,
        "images_processed": False,
        "images_already_existed": False,
        "image_count": 0,
        "messages": [],
        "error": None
    }

    try:
        # Collect all progress messages
        async for message in process_pdf_and_stream(uploaded_pdf_path, ticker, filing_type, period_end_date, year):
            result["messages"].append(message)

            # Parse key information from messages
            if "already ingested (text)" in message:
                result["text_already_existed"] = True
            elif "Added" in message and "text chunks" in message:
                result["text_processed"] = True
                match = re.search(r'Added (\d+) text chunks', message)
                if match:
                    result["text_chunks"] = int(match.group(1))
            elif "already exists in image store" in message:
                result["images_already_existed"] = True
            elif "Added" in message and "image captions" in message:
                result["images_processed"] = True
                match = re.search(r'Added (\d+) image captions', message)
                if match:
                    result["image_count"] = int(match.group(1))
            elif message.startswith("Resolved filing_type:"):
                m = re.search(r"Resolved filing_type:\s*'?([\w-]+)'?", message)
                if m:
                    result["filing_type"] = m.group(1)
            elif message.startswith("Resolved period_end_date:"):
                m = re.search(r"Resolved period_end_date:\s*'?([\d-]+)'?", message)
                if m:
                    result["period_end_date"] = m.group(1)
            elif message.startswith("Resolved year:"):
                m = re.search(r"Resolved year:\s*(\d+)", message)
                if m:
                    result["year"] = int(m.group(1))
            elif "Derived fiscal_quarter" in message:
                m = re.search(r"Derived fiscal_quarter Q(\d)", message)
                if m:
                    result["fiscal_quarter"] = int(m.group(1))
            elif "Error" in message:
                result["error"] = message

        # Determine overall success
        result["success"] = not result["error"] and (
            result["text_processed"] or result["text_already_existed"] or
            result["images_processed"] or result["images_already_existed"]
        )

    except Exception as e:
        result["error"] = f"Processing failed: {str(e)}"
        result["messages"].append(result["error"])

    return result

async def process_pdf_and_stream(uploaded_pdf_path: str, ticker: str = None, filing_type: str = None,
                                  period_end_date: str = None, year: int = None):
    """
    Process a PDF file and stream progress updates.

    Args:
        uploaded_pdf_path: Path to the PDF file
        ticker: Ticker symbol (optional)
        filing_type: SEC filing type - "10-K", "10-Q", or "8-K" (optional). Resolution
            order: this explicit value > document cover-page text > filename. Falls
            back to "10-K" with a loud warning only if all three are inconclusive.
        period_end_date: ISO date (YYYY-MM-DD) this filing covers (optional, explicit
            override). Resolution order: this explicit value > cover-page text. Left
            as None (never guessed) if neither resolves.
        year: Fiscal/Report year (optional, explicit override).
    """
    if not os.path.exists(uploaded_pdf_path):
        yield f"Error: File does not exist: {uploaded_pdf_path}"
        yield f"Failed to process {os.path.basename(uploaded_pdf_path)} - file not found"
        return

    try:
        yield f"Processing document: {uploaded_pdf_path}"
        # fitz.open() and cover-page text extraction are blocking C-extension
        # calls — offload to a worker thread so they don't stall the event
        # loop (and every other in-flight request on this process) while a
        # large filing is being parsed.
        pdf_document = await asyncio.to_thread(fitz.open, uploaded_pdf_path)
        source_file_name = os.path.basename(uploaded_pdf_path)
        company_name = extract_company_name(source_file_name)

        # Resolve filing_type and period_end_date with a clear, non-silent
        # priority order: explicit caller value > document cover-page text
        # (reliable regardless of filename) > filename token (weakest signal,
        # fails for arbitrary user-chosen filenames).
        cover_info = await asyncio.to_thread(extract_cover_page_info, pdf_document)

        if filing_type:
            if filing_type not in VALID_FILING_TYPES:
                yield f"Error: Invalid filing_type '{filing_type}'. Must be one of {VALID_FILING_TYPES}."
                return
            yield f"Using explicitly provided filing_type '{filing_type}'"
        elif cover_info["filing_type"]:
            filing_type = cover_info["filing_type"]
            yield f"Detected filing_type '{filing_type}' from document cover page (FORM {filing_type})"
        else:
            filename_filing_type = extract_filing_type_from_filename(source_file_name)
            if filename_filing_type:
                filing_type = filename_filing_type
                yield f"Auto-detected filing_type '{filing_type}' from filename '{source_file_name}' (cover page didn't match a recognizable pattern)"
            else:
                filing_type = "10-K"
                yield (
                    f"WARNING: Could not determine filing_type from document cover page or filename "
                    f"'{source_file_name}' — defaulting to '10-K'. Verify this is correct; pass filing_type "
                    f"explicitly if not."
                )
        yield f"Resolved filing_type: '{filing_type}'"

        if not period_end_date:
            if cover_info["period_end_date"]:
                period_end_date = cover_info["period_end_date"]
                yield f"Detected period_end_date '{period_end_date}' from document cover page"
            else:
                yield (
                    f"WARNING: Could not determine period_end_date from document cover page "
                    f"for '{source_file_name}'. Leaving unset rather than guessing — retrieval "
                    f"filters/comparisons that rely on an exact period will not have this filing's precise date."
                )
        yield f"Resolved period_end_date: '{period_end_date}'"

        # Derive ticker if not provided
        if not ticker:
            ticker = get_ticker(company_name)
            if ticker:
                yield f"Derived ticker '{ticker}' from company '{company_name}'"
            else:
                yield f"Warning: Could not derive ticker for company '{company_name}'. Using default unified collection."

        # Derive fiscal_quarter (1-4) for 10-Q filings ONLY — this is ground-truth
        # derivation from the real period_end_date + the ticker's actual fiscal
        # calendar, not a guess. 10-Ks cover the whole fiscal year (no single
        # quarter) and 8-Ks are single events, so fiscal_quarter stays None for
        # both — tagging either would be misleading.
        fiscal_quarter = None
        if filing_type == "10-Q" and period_end_date and ticker:
            from app.utils.company_mapping import get_fiscal_quarter
            fiscal_quarter = get_fiscal_quarter(period_end_date, ticker)
            if fiscal_quarter:
                yield f"Derived fiscal_quarter Q{fiscal_quarter} from period_end_date '{period_end_date}' and {ticker}'s fiscal calendar"

        # Resolve the "year" tag used for retrieval filtering. MUST prefer
        # explicit year > period_end_date's year > filename-derived year.
        if year:
            resolved_year = int(year)
            yield f"Resolved year: {resolved_year} (from explicit parameter)"
        elif period_end_date:
            resolved_year = int(period_end_date[:4])
            yield f"Resolved year: {resolved_year} (from period_end_date)"
        else:
            resolved_year = extract_year_from_filename(source_file_name)
            yield f"Resolved year: {resolved_year} (from filename, no period_end_date available)"

        # Determine collection name
        if ticker:
            collection_name = f"ticker_{ticker.lower()}"
            yield f"Using collection: {collection_name}"
        else:
            collection_name = "unified_rag_db_hybrid"
            yield f"Using fallback collection: {collection_name}"

        # Initialize vector store with specific collection
        # init_vector_stores() constructs a sync QdrantClient whose __init__
        # makes live blocking network calls (get_collections/create_collection/
        # create_payload_index) — offload so it doesn't stall the event loop.
        db_loader, _ = await asyncio.to_thread(init_vector_stores, collection_name=collection_name)
        
        # Calculate content hash for duplicate detection (re-reads + hashes
        # every page's text — blocking, offload to a thread)
        content_hash = await asyncio.to_thread(calculate_content_hash, uploaded_pdf_path)
        logger.info("\nDebug: Content hash for %s: %s", source_file_name, content_hash)
        
        # --- Text ingestion ---
        text_already_exists = False
        exists, existing_points = await check_document_exists(db_loader, source_file_name, "text", content_hash)
        
        if exists:
            text_already_exists = True
            yield f"{source_file_name} already ingested (text) with {len(existing_points)} chunks. Skipping text ingestion."

        if not text_already_exists:
            documents = await asyncio.to_thread(
                _extract_text_documents, pdf_document, source_file_name, company_name, ticker,
                content_hash, resolved_year, filing_type, period_end_date, fiscal_quarter,
            )

            if documents:
                yield f"Extracted {len(documents)} text segments from PDF."
                _banner("TEXT 2/3", "Splitting extracted text into chunks...")
                text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
                    chunk_size=1024, chunk_overlap=300
                )
                # tiktoken encoding of the full document is CPU-bound — offload.
                text_chunks = await asyncio.to_thread(text_splitter.split_documents, documents)
                logger.info("Created %d text chunks", len(text_chunks))

                # Generate deterministic UUIDs using the common function
                ids = [generate_doc_id(doc.metadata, i, "text") for i, doc in enumerate(text_chunks)]

                # Ingest with hybrid vectors
                _banner("TEXT 3/3", f"Generating embeddings & uploading {len(text_chunks)} chunk(s)...")
                num_ingested = await ingest_documents_with_hybrid_vectors(
                    db_loader, text_chunks, ids, label="text chunk(s)")

                yield f"Added {num_ingested} text chunks to collection '{collection_name}'."
            else:
                yield "No text extracted from PDF."

        # --- Image ingestion ---
        image_already_exists = False

        _banner("IMAGE 1/3", f"Extracting & hashing images from {source_file_name}...")
        yield f"Extracting and hashing images from {source_file_name}..."
        img_processor = ImageDescription(uploaded_pdf_path, filing_type=filing_type)

        # Blocking PyMuPDF image extraction + hashing over every page — offload.
        image_info, image_hashes = await asyncio.to_thread(img_processor.get_image_information)

        if image_hashes:
            yield f"Found {len(image_hashes)} images; checking which are already ingested..."

            new_image_hashes = await find_new_image_hashes(db_loader, image_hashes)
            already_existing_count = len(image_hashes) - len(new_image_hashes)

            if already_existing_count:
                yield (
                    f"{already_existing_count}/{len(image_hashes)} image(s) already ingested "
                    f"(matched by content hash) — skipping those, keeping {len(new_image_hashes)} new."
                )

            # Narrow image_info (path -> context_text) down to only the genuinely
            # new images, so a single repeated image (e.g. a logo) never causes
            # every other new chart/table in this document to be skipped.
            new_paths = {info["path"] for info in new_image_hashes.values()}
            image_info = {path: ctx for path, ctx in image_info.items() if path in new_paths}
            image_hashes = new_image_hashes

            if not image_info:
                image_already_exists = True
                yield f"{source_file_name}: all images already ingested. Skipping image ingestion."

        if not image_already_exists:
            if image_info:
                _banner("IMAGE 2/3", f"Analyzing {len(image_info)} image(s) with GPT-4o...")
                yield f" Analyzing {len(image_info)} images with GPT-4o..."
                image_descriptions = await img_processor.get_image_description(image_info)
                
                metadata_path = f"metadata_{source_file_name}.json"
                metadata_to_save = image_descriptions
                
                with open(metadata_path, "w", encoding="utf-8") as f:
                    json.dump(metadata_to_save, f, indent=2)
                yield f"Saved detailed image analysis to {metadata_path}"

                image_documents = await asyncio.to_thread(
                    img_processor.getRetriever, metadata_path, company_name, image_hashes)

                for i, doc in enumerate(image_documents):
                    doc.metadata.update({
                        "source_file": source_file_name,
                        "company": company_name,
                        "ticker": ticker if ticker else "unknown",
                        "content_type": "image",
                        "content_hash": content_hash,
                        "year": resolved_year,
                        "filing_type": filing_type,
                        "period_end_date": period_end_date,
                        "fiscal_quarter": fiscal_quarter,
                        "ingestion_timestamp": str(datetime.now())
                    })

                if image_documents:
                    img_ids = [generate_doc_id(doc.metadata, i, "image") for i, doc in enumerate(image_documents)]

                    _banner("IMAGE 3/3", f"Generating embeddings & uploading {len(image_documents)} image caption(s)...")
                    num_img_ingested = await ingest_documents_with_hybrid_vectors(
                        db_loader, image_documents, img_ids, label="image caption(s)")

                    yield f"Added {num_img_ingested} image captions to collection '{collection_name}'."
                else:
                    # All candidate images were classified as decorative/invalid by
                    # the vision model — an empty upsert to Qdrant is a 400 error,
                    # not a no-op, so this must be a distinct guarded branch.
                    yield "All candidate images were classified as decorative/invalid — no image captions added."
            else:
                yield "No images found in PDF."

        # Final completion status
        if text_already_exists and image_already_exists:
            yield f"Completed processing for {source_file_name} - file already existed, no new ingestion needed"
        elif text_already_exists:
            yield f"Completed processing for {source_file_name} - text already existed, images processed"
        elif image_already_exists:
            yield f"Completed processing for {source_file_name} - images already existed, text processed"
        else:
            yield f"Completed ingestion for {source_file_name}"

    except Exception as e:
        yield f"Error while processing PDF {uploaded_pdf_path}: {str(e)}"
        import traceback
        yield f"Traceback: {traceback.format_exc()}"

    except Exception as e:
        yield f"Error while processing PDF {uploaded_pdf_path}: {str(e)}"
