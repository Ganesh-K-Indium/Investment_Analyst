import os
import json
import uuid
import re
import hashlib
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

def ingest_documents_with_hybrid_vectors(db_loader, documents, doc_ids):
    """
    Ingest documents with hybrid vectors (dense + sparse).
    
    Args:
        db_loader: The load_vector_database instance
        documents: List of LangChain Document objects
        doc_ids: List of document IDs (UUIDs)
    """
    # Extract text content from documents
    texts = [doc.page_content for doc in documents]
    
    # Generate all embeddings (dense + sparse)
    embeddings_dict = db_loader.generate_embeddings_for_ingestion(texts)
    
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
    
    # Upload to Qdrant using client directly
    db_loader.qdrant_client.upsert(
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
    
    # Remove common document type suffixes
    name = re.sub(r'\b(10[-\s]?[kq]|annual|quarterly|report)\b', '', name, flags=re.IGNORECASE)
    
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
        print(f"Error calculating content hash: {e}")
        return ""

def calculate_image_content_hash(image_data: bytes) -> str:
    """Calculate a deterministic hash of individual image content."""
    try:
        return hashlib.sha256(image_data).hexdigest()
    except Exception as e:
        print(f"Error calculating image content hash: {e}")
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

def check_document_exists(vectorstore, source_file_name: str, doc_type: str = "text", content_hash: str = None, image_hashes: dict = None) -> tuple[bool, list]:
    """
    Check if a document already exists in the vector store using metadata filters.
    
    Args:
        vectorstore: The vector store to check
        source_file_name: Name of the source file
        doc_type: Type of document ("text" or "image")
        content_hash: Hash of the document content for duplicate detection
    
    Returns:
        tuple[bool, list]: (exists, existing_points)
    """
    try:
        print(f"\n=== Checking existence of {source_file_name} ({doc_type}) ===")
        print(f"Collection name: {vectorstore.collection_name}")
        
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
                
                count_response = vectorstore.client.count(
                    collection_name=vectorstore.collection_name,
                    count_filter=individual_filter
                )
                
                if count_response.count > 0:
                    print(f"Found existing image with hash {img_info['hash'][:16]}...")
                    points = vectorstore.client.scroll(
                        collection_name=vectorstore.collection_name,
                        scroll_filter=individual_filter,
                        with_payload=True,
                        limit=count_response.count
                    )[0]
                    return True, points
            
            print("No individual image hashes found, checking by PDF content hash...")
        
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
        
        count_response = vectorstore.client.count(
            collection_name=vectorstore.collection_name,
            count_filter=search_filter
        )
        print(f"\nDebug: Found {count_response.count} matching points")
        
        if count_response.count > 0:
            points = vectorstore.client.scroll(
                collection_name=vectorstore.collection_name,
                scroll_filter=search_filter,
                with_payload=True,
                limit=count_response.count
            )[0]
            
            return True, points
            
        return False, []
        
    except Exception as e:
        print(f"Error checking document existence: {e}")
        return False, []

def process_pdf_and_get_result(uploaded_pdf_path: str, ticker: str = None) -> dict:
    """
    Process a PDF file and return a structured result.
    
    Args:
        uploaded_pdf_path: Path to the PDF file
        ticker: Ticker symbol (optional)
        
    Returns:
        dict: Processing result with status and details
    """
    result = {
        "success": False,
        "file_name": os.path.basename(uploaded_pdf_path),
        "ticker": ticker,
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
        for message in process_pdf_and_stream(uploaded_pdf_path, ticker):
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

def process_pdf_and_stream(uploaded_pdf_path: str, ticker: str = None):
    """
    Process a PDF file and stream progress updates.
    
    Args:
        uploaded_pdf_path: Path to the PDF file
        ticker: Ticker symbol (optional)
    """
    if not os.path.exists(uploaded_pdf_path):
        yield f"Error: File does not exist: {uploaded_pdf_path}"
        yield f"Failed to process {os.path.basename(uploaded_pdf_path)} - file not found"
        return

    try:
        yield f"Processing document: {uploaded_pdf_path}"
        pdf_document = fitz.open(uploaded_pdf_path)
        source_file_name = os.path.basename(uploaded_pdf_path)
        company_name = extract_company_name(source_file_name)

        # Derive ticker if not provided
        if not ticker:
            ticker = get_ticker(company_name)
            if ticker:
                yield f"Derived ticker '{ticker}' from company '{company_name}'"
            else:
                yield f"Warning: Could not derive ticker for company '{company_name}'. Using default unified collection."
        
        # Determine collection name
        if ticker:
            collection_name = f"ticker_{ticker.lower()}"
            yield f"Using collection: {collection_name}"
        else:
            collection_name = "unified_rag_db_hybrid"
            yield f"Using fallback collection: {collection_name}"

        # Initialize vector store with specific collection
        db_loader, unified_vectorstore = init_vector_stores(collection_name=collection_name)
        
        # Calculate content hash for duplicate detection
        content_hash = calculate_content_hash(uploaded_pdf_path)
        print(f"\nDebug: Content hash for {source_file_name}: {content_hash}")
        
        # --- Text ingestion ---
        text_already_exists = False
        exists, existing_points = check_document_exists(unified_vectorstore, source_file_name, "text", content_hash)
        
        if exists:
            text_already_exists = True
            yield f"{source_file_name} already ingested (text) with {len(existing_points)} chunks. Skipping text ingestion."

        if not text_already_exists:
            documents = []
            print(f"\n📄 Extracting text from {len(pdf_document)} pages...")
            for page_num, page in enumerate(tqdm(pdf_document, desc="Extracting text", unit="page")):
                text = page.get_text("text")
                if text.strip():
                    metadata = {
                        "source_file": source_file_name,
                        "page_num": page_num + 1,
                        "company": company_name,
                        "ticker": ticker if ticker else "unknown",
                        "content_type": "text",
                        "content_hash": content_hash,
                        "ingestion_timestamp": str(datetime.now()),
                    }
                    documents.append(Document(page_content=text, metadata=metadata))

            if documents:
                yield f"Extracted {len(documents)} text segments from PDF."
                print(f"\n✂️  Splitting text into chunks...")
                text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
                    chunk_size=1024, chunk_overlap=300
                )
                text_chunks = text_splitter.split_documents(documents)
                print(f"Created {len(text_chunks)} text chunks")

                # Generate deterministic UUIDs using the common function
                ids = [generate_doc_id(doc.metadata, i, "text") for i, doc in enumerate(text_chunks)]
                
                # Ingest with hybrid vectors
                num_ingested = ingest_documents_with_hybrid_vectors(db_loader, text_chunks, ids)
                
                yield f"Added {num_ingested} text chunks to collection '{collection_name}'."
            else:
                yield "No text extracted from PDF."

        # --- Image ingestion ---
        image_already_exists = False
        
        yield f"Extracting and hashing images from {source_file_name}..."
        img_processor = ImageDescription(uploaded_pdf_path)
        
        image_info, image_hashes = img_processor.get_image_information()
        
        if image_hashes:
            yield f"Found {len(image_hashes)} images to check for duplicates."
            
            exists, existing_img_points = check_document_exists(unified_vectorstore, source_file_name, "image", content_hash, image_hashes)
            
            if not exists:
                exists, existing_img_points = check_document_exists(unified_vectorstore, source_file_name, "image", content_hash)
            
            if not exists:
                exists, existing_img_points = check_document_exists(unified_vectorstore, source_file_name, "image")

            if exists:
                image_already_exists = True
                yield f"{source_file_name} already exists in image store. Skipping image ingestion."

        if not image_already_exists:
            if image_info:
                yield f"🤖 Analyzing {len(image_info)} images with GPT-4o..."
                image_descriptions = img_processor.get_image_description(image_info)
                
                metadata_path = f"metadata_{source_file_name}.json"
                metadata_to_save = image_descriptions
                
                with open(metadata_path, "w", encoding="utf-8") as f:
                    json.dump(metadata_to_save, f, indent=2)
                yield f"Saved detailed image analysis to {metadata_path}"

                image_documents = img_processor.getRetriever(
                    metadata_path, company_name, image_hashes)

                for i, doc in enumerate(image_documents):
                    doc.metadata.update({
                        "source_file": source_file_name,
                        "company": company_name,
                        "ticker": ticker if ticker else "unknown",
                        "content_type": "image",
                        "content_hash": content_hash,
                        "ingestion_timestamp": str(datetime.now())
                    })

                img_ids = [generate_doc_id(doc.metadata, i, "image") for i, doc in enumerate(image_documents)]
                
                num_img_ingested = ingest_documents_with_hybrid_vectors(db_loader, image_documents, img_ids)
                
                yield f"Added {num_img_ingested} image captions to collection '{collection_name}'."
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
