"""
this module is used for loading the unified RAG database with hybrid search capabilities
"""

import asyncio
import logging
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import QdrantVectorStore, RetrievalMode
from qdrant_client import QdrantClient, AsyncQdrantClient
from qdrant_client import models
from qdrant_client.http.models import PayloadSchemaType
from tqdm import tqdm
import os

logger = logging.getLogger("rag.vectordb.client")

load_dotenv()

# Try to import FastEmbed for sparse embeddings
try:
    from fastembed import SparseTextEmbedding
    SPARSE_EMBEDDING_AVAILABLE = True
except ImportError:
    SPARSE_EMBEDDING_AVAILABLE = False
    logger.warning("Warning: fastembed not available. Install with: pip install fastembed")

class load_vector_database():
    """Unified vector database loader with advanced hybrid search capabilities"""
    
    def __init__(self, use_hybrid_search: bool = True, collection_name: str = None, create_if_missing: bool = True):
        """
        Initialize unified vector database loader with hybrid search.
        
        Args:
            use_hybrid_search: If True, use hybrid search with dense, sparse (BM25), and ColBERT vectors.
            collection_name: Name of the collection to use. If None, uses default unified collection.
            create_if_missing: If True, creates the collection if it doesn't exist.
        """
        # Use unified collection for both text and images
        self.collection_name = collection_name if collection_name else "unified_rag_db_hybrid"
        self.use_hybrid_search = use_hybrid_search
        self.create_if_missing = create_if_missing
        
        # text-embedding-3-large for financial phrase directionality (3072-dim vectors)
        self.embeddings = OpenAIEmbeddings(model="text-embedding-3-large")
        self.qdrant_url = os.getenv("QDRANT_URL", "")
        self.qdrant_api_key = os.getenv("QDRANT_API_KEY", '')
        
        # Initialize sparse embeddings for BM25 if available
        self.sparse_model = None
        if use_hybrid_search and SPARSE_EMBEDDING_AVAILABLE:
            try:
                self.sparse_model = SparseTextEmbedding(model_name="Qdrant/bm25")
                # print("BM25 sparse embeddings initialized") # Reduced log noise
            except Exception as e:
                logger.warning(f"Warning: Failed to initialize sparse embeddings: {e}")
        
        # Try cloud Qdrant first, fallback to local
        try:
            # print(f"Attempting to connect to Qdrant at: {self.qdrant_url}") # Reduced log noise
            self.qdrant_client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key, timeout=60)
            # self.qdrant_client.get_collections() # Avoid extra call if not needed
            # print(f"Successfully connected to Qdrant at {self.qdrant_url}")
        except Exception as e:
            logger.error(f"Failed to connect to cloud Qdrant: {e}")
            logger.warning("Falling back to local Qdrant at http://localhost:6333")
            self.qdrant_url = "http://localhost:6333"
            self.qdrant_api_key = ''
            try:
                self.qdrant_client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key, timeout=60)
                # self.qdrant_client.get_collections()
                logger.info(f"Successfully connected to local Qdrant")
            except Exception as local_error:
                logger.error(f"Failed to connect to local Qdrant: {local_error}")
                raise ConnectionError("Unable to connect to Qdrant instances.")

        # Ensure collection exists with correct config
        if self.create_if_missing:
            self.ensure_collection_exists()
        else:
            # check existence without creating
            self._check_exists()

        # Async client for the query-time hot path (hybrid_search) — constructing
        # it does no I/O, so this is safe outside an event loop / async context.
        self.async_qdrant_client = AsyncQdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key, timeout=60)

    def ensure_collection_exists(self):
        """
        Ensure the collection exists with the required configuration.
        If not, create it with hybrid search support (dense + sparse) and payload indexes.
        """
        try:
            collections = self.qdrant_client.get_collections().collections
            exists = any(c.name == self.collection_name for c in collections)
            
            # Payload indexes required on every collection, new or pre-existing.
            payload_fields = {
                "metadata.source_file": PayloadSchemaType.KEYWORD,
                "metadata.company": PayloadSchemaType.KEYWORD,
                "metadata.content_type": PayloadSchemaType.KEYWORD,
                "metadata.content_hash": PayloadSchemaType.KEYWORD,
                "metadata.image_content_hash": PayloadSchemaType.KEYWORD,
                "metadata.page_num": PayloadSchemaType.INTEGER,
                "metadata.year": PayloadSchemaType.INTEGER,
                "metadata.ingestion_timestamp": PayloadSchemaType.KEYWORD,
                "metadata.filing_type": PayloadSchemaType.KEYWORD,
                "metadata.period_end_date": PayloadSchemaType.KEYWORD,
                "metadata.fiscal_quarter": PayloadSchemaType.INTEGER,
            }

            if not exists:
                logger.info(f"Collection '{self.collection_name}' does not exist. Creating with hybrid config...")

                # Dense embedding size (OpenAI text-embedding-3-large)
                dense_size = 3072

                # Create collection with hybrid config
                self.qdrant_client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config={
                        "dense": models.VectorParams(
                            size=dense_size,
                            distance=models.Distance.COSINE,
                        ),
                    },
                    sparse_vectors_config={
                        "bm25": models.SparseVectorParams(
                            modifier=models.Modifier.IDF,
                        )
                    }
                )

                for field_name, schema in payload_fields.items():
                    logger.info(f"Creating index for {field_name} ({schema})...")
                    self.qdrant_client.create_payload_index(
                        collection_name=self.collection_name,
                        field_name=field_name,
                        field_schema=schema,
                    )

                logger.info(f" Collection '{self.collection_name}' created successfully with hybrid search and indexes.")
            else:
                # Collection already exists (e.g. created before a new payload field was
                # introduced, such as metadata.filing_type) — backfill any missing indexes.
                existing_indexes = set(
                    self.qdrant_client.get_collection(self.collection_name).payload_schema.keys()
                )
                for field_name, schema in payload_fields.items():
                    if field_name not in existing_indexes:
                        logger.info(f"Backfilling missing index for {field_name} ({schema}) on '{self.collection_name}'...")
                        self.qdrant_client.create_payload_index(
                            collection_name=self.collection_name,
                            field_name=field_name,
                            field_schema=schema,
                        )

        except Exception as e:
            logger.error(f"Error ensuring collection exists: {e}")
            # Don't raise, might interfere with read-only operations if strict permissions logic

    def _check_exists(self):
        """Check if collection exists without creating it."""
        try:
            collections = self.qdrant_client.get_collections().collections
            exists = any(c.name == self.collection_name for c in collections)
            if not exists:
                logger.warning(f" Collection '{self.collection_name}' does not exist (read-only mode).")
        except Exception as e:
            logger.error(f"Error checking collection existence: {e}")

    
    def get_unified_vectorstore(self):
        """
        Get the unified vector store for both text and images.
        Uses LangChain's QdrantVectorStore for compatibility.
        """
        vector_store_kwargs = {
            "client": self.qdrant_client,
            "collection_name": self.collection_name,
            "embedding": self.embeddings,
            "vector_name": "dense"  # Specify the dense vector name
        }
        
        vectorstore = QdrantVectorStore(**vector_store_kwargs)
        return vectorstore
    
    async def hybrid_search(self, query: str, content_type: str = None, company: str = None,
                     years: list = None, filing_type: str = None, period_end_date: str = None,
                     fiscal_quarter: int = None,
                     limit: int = 10, dense_limit: int = 100, sparse_limit: int = 100):
        """
        Advanced hybrid search using prefetch and fusion queries (RRF).

        Args:
            query: Search query text
            content_type: Filter by content type ("text" or "image"), None for both
            company: Filter by company name
            years: Filter by list of years
            filing_type: Filter by SEC filing type ("10-K", "10-Q", "8-K"), None to search
                all filing types in the collection (default — safe for collections that
                predate filing_type tagging, and for queries where filing type wasn't inferred)
            period_end_date: Filter by exact period-end date (ISO "YYYY-MM-DD" — fiscal
                year end for a 10-K, fiscal quarter end for a 10-Q). None to search all
                periods (default — most chunks don't have a caller-resolved exact date to
                filter on, so this stays inert unless a caller has one)
            fiscal_quarter: Filter by fiscal quarter number (1-4), only meaningful for
                10-Q chunks (10-K/8-K chunks have this unset). None to search all quarters
                (default). Safe to combine with filing_type="10-Q" and years.
            limit: Final number of results to return
            dense_limit: Number of results from dense vector search
            sparse_limit: Number of results from sparse (BM25) search

        Returns:
            List of search results with payloads
        """
        # Generate dense embeddings (OpenAI)
        dense_vector = await self.embeddings.aembed_query(query)
        
        # Generate sparse vector if available
        sparse_vector = None
        if self.sparse_model:
            try:
                sparse_embeddings = list(self.sparse_model.embed([query]))
                if sparse_embeddings:
                    # Convert to Qdrant sparse vector format
                    sparse_emb = sparse_embeddings[0]
                    sparse_vector = models.SparseVector(
                        indices=sparse_emb.indices.tolist(),
                        values=sparse_emb.values.tolist()
                    )
            except Exception as e:
                logger.warning(f"Warning: Failed to generate sparse embedding: {e}")
        
        # Build filter conditions
        filter_conditions = []
        if content_type:
            filter_conditions.append(
                models.FieldCondition(
                    key="metadata.content_type",
                    match=models.MatchValue(value=content_type)
                )
            )
        if company:
            # Handle both string and list company filters
            if isinstance(company, list):
                if len(company) == 1:
                    # Single company in list - use MatchValue
                    filter_conditions.append(
                        models.FieldCondition(
                            key="metadata.company",
                            match=models.MatchValue(value=company[0].lower())
                        )
                    )
                elif len(company) > 1:
                    # Multiple companies - use MatchAny
                    filter_conditions.append(
                        models.FieldCondition(
                            key="metadata.company",
                            match=models.MatchAny(any=[c.lower() for c in company])
                        )
                    )
            else:
                # String company - use MatchValue
                filter_conditions.append(
                    models.FieldCondition(
                        key="metadata.company",
                        match=models.MatchValue(value=company.lower())
                    )
                )
        if years:
            filter_conditions.append(
                models.FieldCondition(
                    key="metadata.year",
                    match=models.MatchAny(any=years)
                )
            )
        if filing_type:
            filter_conditions.append(
                models.FieldCondition(
                    key="metadata.filing_type",
                    match=models.MatchValue(value=filing_type)
                )
            )
        if period_end_date:
            filter_conditions.append(
                models.FieldCondition(
                    key="metadata.period_end_date",
                    match=models.MatchValue(value=period_end_date)
                )
            )
        if fiscal_quarter:
            filter_conditions.append(
                models.FieldCondition(
                    key="metadata.fiscal_quarter",
                    match=models.MatchValue(value=fiscal_quarter)
                )
            )

        global_filter = models.Filter(must=filter_conditions) if filter_conditions else None
        
        # Build hybrid query with prefetch and fusion
        prefetch_queries = []
        
        # Dense retrieval: semantic understanding
        prefetch_queries.append(
            models.Prefetch(
                query=dense_vector,
                using="dense",
                filter=global_filter,
                limit=dense_limit
            )
        )
        
        # Sparse retrieval: exact term matching with BM25
        if sparse_vector:
            prefetch_queries.append(
                models.Prefetch(
                    query=sparse_vector,
                    using="bm25",
                    filter=global_filter,
                    limit=sparse_limit
                )
            )
        
        # Fusion query combining dense and sparse results with RRF
        fusion_prefetch = models.Prefetch(
            prefetch=prefetch_queries,
            query=models.FusionQuery(fusion=models.Fusion.RRF),  # Reciprocal Rank Fusion
            limit=limit
        )
        
        # Final query — use RRF fusion result directly (not dense re-rank which discards BM25)
        try:
            response = await self.async_qdrant_client.query_points(
                collection_name=self.collection_name,
                prefetch=fusion_prefetch,
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                query_filter=global_filter,
                limit=limit,
                with_payload=True,
            )

            return response.points

        except Exception as e:
            logger.error(f"Error in hybrid search: {e}")
            # Fallback to simple dense search
            return await self._fallback_search(dense_vector, global_filter, limit)

    async def _fallback_search(self, query_vector, query_filter, limit):
        """Fallback to simple dense vector search if hybrid search fails."""
        try:
            response = await self.async_qdrant_client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                using="dense",
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
            return response.points
        except Exception as e:
            logger.error(f"Error in fallback search: {e}")
            return []
    
    async def generate_embeddings_for_ingestion(self, texts: list[str], label: str = "documents") -> dict:
        """
        Generate all required embeddings (dense, sparse) for document ingestion.

        Args:
            texts: List of text strings to embed
            label: Human-facing noun for what's being embedded (e.g. "text
                chunk(s)" or "image caption(s)") — this function is shared by
                both the text-chunk and image-caption ingestion paths, so the
                stage banners it logs need to say which one is running.

        Returns:
            dict with 'dense' and 'sparse' embedding lists
        """
        result = {
            'dense': [],
            'sparse': []
        }

        # Generate dense embeddings in batches of 100 (OpenAI limit is 2048, stay well under),
        # all batches concurrently rather than one at a time.
        BATCH_SIZE = 100
        batches = [texts[i:i + BATCH_SIZE] for i in range(0, len(texts), BATCH_SIZE)]
        logger.info(
            "Generating dense embeddings for %d %s (%d batch(es) of %d, concurrent)...",
            len(texts), label, len(batches), BATCH_SIZE,
        )
        batch_results = await asyncio.gather(*(self.embeddings.aembed_documents(batch) for batch in batches))
        for batch_embeddings in batch_results:
            result['dense'].extend(batch_embeddings)

        # Generate sparse embeddings (BM25)
        if self.sparse_model:
            logger.info("Generating sparse (BM25) embeddings for %d %s...", len(texts), label)
            try:
                # Use tqdm with the generator
                sparse_embeddings = []
                for sparse_emb in tqdm(self.sparse_model.embed(texts), 
                                      desc="Sparse embeddings (BM25)", 
                                      total=len(texts), 
                                      unit="doc"):
                    sparse_embeddings.append(sparse_emb)
                
                for sparse_emb in sparse_embeddings:
                    result['sparse'].append(
                        models.SparseVector(
                            indices=sparse_emb.indices.tolist(),
                            values=sparse_emb.values.tolist()
                        )
                    )
            except Exception as e:
                logger.warning(f"Warning: Failed to generate sparse embeddings: {e}")
                result['sparse'] = [None] * len(texts)
        else:
            result['sparse'] = [None] * len(texts)

        logger.info(f"Generated embeddings: {len(result['dense'])} dense, {len([s for s in result['sparse'] if s is not None])} sparse")
        return result
    def get_collection_files(self):
        """Get all unique source files in the unified collection."""
        doc_list = set()

        points, _ = self.qdrant_client.scroll(
            collection_name=self.collection_name,
            with_payload=True,
            limit=1000
        )

        for point in points:
            payload = point.payload
            metadata = payload.get("metadata", {})
            doc_list.add(metadata.get("source_file", "Unknown"))

        return ', '.join(sorted(doc_list))

    def get_collection_companies(self):
        """Get all unique companies in the unified collection."""
        company_list = set()

        points, _ = self.qdrant_client.scroll(
            collection_name=self.collection_name,
            with_payload=True,
            limit=1000
        )

        for point in points:
            payload = point.payload
            metadata = payload.get("metadata", {})
            company_list.add(metadata.get("company", "Unknown"))

        return ', '.join(sorted(company_list))
    
    def get_collection_stats(self):
        """Get statistics about the unified collection."""
        text_count = 0
        image_count = 0
        companies = set()
        sources = set()

        points, _ = self.qdrant_client.scroll(
            collection_name=self.collection_name,
            with_payload=True,
            limit=1000
        )

        for point in points:
            payload = point.payload
            metadata = payload.get("metadata", {})
            
            content_type = metadata.get("content_type", "text")
            if content_type == "image":
                image_count += 1
            else:
                text_count += 1
            
            companies.add(metadata.get("company", "Unknown"))
            sources.add(metadata.get("source_file", "Unknown"))

        return {
            "total": text_count + image_count,
            "text": text_count,
            "images": image_count,
            "companies": sorted(companies),
            "sources": sorted(sources)
        }



