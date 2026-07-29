import os
import json
import logging
import time
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.http import models

logger = logging.getLogger("rag.graph.semantic_cache")

load_dotenv()

class SemanticCache:
    """
    Semantic Cache system to reduce latency by returning cached responses
    for semantically similar queries.
    """
    
    def __init__(self, threshold: float = 0.95):
        """
        Initialize the semantic cache.
        
        Args:
            threshold: Similarity threshold (0.0 to 1.0) for cache hits. 
                       Higher means stricter matching.
        """
        self.threshold = threshold
        self.collection_name = "semantic_cache"
        self.embeddings = OpenAIEmbeddings()
        
        # Initialize Qdrant Client (same logic as load_dbs.py)
        self.qdrant_url = os.getenv("QDRANT_URL", "")
        self.qdrant_api_key = os.getenv("QDRANT_API_KEY", '')
        
        try:
            logger.info(f" SemanticCache: Connecting to Qdrant at {self.qdrant_url}...")
            self.client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key, timeout=10)
            self.client.get_collections() # Test connection
            logger.info(" SemanticCache: Connected to Cloud Qdrant")
        except Exception as e:
            logger.warning(f" SemanticCache: Cloud connection failed ({e}). Trying local...")
            self.qdrant_url = "http://localhost:6333"
            self.qdrant_api_key = ''
            try:
                self.client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key, timeout=5)
                logger.info("SemanticCache: Connected to Local Qdrant")
            except Exception as local_e:
                logger.error(f" SemanticCache: Failed to connect to Qdrant. Caching disabled. {local_e}")
                self.client = None
                
        # Ensure collection exists
        if self.client:
            self._ensure_collection()

    def _ensure_collection(self):
        """Ensure the cache collection exists with proper configuration."""
        try:
            collections = self.client.get_collections().collections
            exists = any(c.name == self.collection_name for c in collections)
            
            if not exists:
                logger.info(f" SemanticCache: Creating collection '{self.collection_name}'...")
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=1536,  # OpenAI embedding size
                        distance=models.Distance.COSINE
                    )
                )
                logger.info(f" SemanticCache: Collection created.")

            # Ensure payload index for thread_id (required for filtering)
            self.client.create_payload_index(
                collection_name=self.collection_name,
                field_name="thread_id",
                field_schema=models.PayloadSchemaType.KEYWORD
            )
            logger.info(" SemanticCache: Thread ID index ensured.")
        except Exception as e:
            logger.error(f" SemanticCache: Error ensuring collection: {e}")

    @staticmethod
    def _filter_signature(ticker: str = None, requested_years: list = None, filing_type: str = None) -> str:
        """
        Deterministic string encoding the resolved retrieval filters. Folded
        into the embedded cache key so two semantically-near-identical
        queries that resolve to DIFFERENT filters (e.g. "AAPL revenue" with
        filing_type=None vs. a follow-up scoped to filing_type='10-Q') don't
        collide on a cosine-similarity hit — the filter context changes the
        embedded text itself, not just metadata checked after the fact.
        """
        parts = []
        if ticker:
            parts.append(f"ticker={ticker.lower()}")
        if requested_years:
            parts.append(f"years={','.join(str(y) for y in sorted(requested_years))}")
        parts.append(f"filing_type={filing_type or 'any'}")
        return " | ".join(parts)

    def lookup(self, query: str, thread_id: str = None, ticker: str = None,
               requested_years: list = None, filing_type: str = None):
        """
        Look up a query in the cache.

        Args:
            query: The user query string.
            thread_id: Optional thread ID to scope the cache lookup.
            ticker: Resolved ticker for this query, if any — folded into the
                cache key so answers for different companies never collide.
            requested_years: Resolved year filter for this query, if any.
            filing_type: Resolved filing_type filter for this query, if any
                ("10-K"/"10-Q"/"8-K"/None). Folded into the cache key so a
                cached answer scoped to one filing type is never served for
                a query that resolves to a different filing type.

        Returns:
            dict: Cached response payload if hit, None if miss.
        """
        if not self.client:
            return None

        # BYPASS CACHE for context-dependent queries (HITL triggers)
        # Short queries or follow-up keywords should always hit the graph
        bypass_keywords = ["summarize", "recap", "elaborate", "more info", "tell me more", "explain that", "continue"]
        query_lower = query.lower()

        # Check for keywords
        if any(kw in query_lower for kw in bypass_keywords):
            logger.info(f" SemanticCache: Bypassing cache for context-dependent query: '{query}'")
            return None

        # Check for very short queries (likely follow-ups)
        if len(query.split()) < 3:
            logger.info(f" SemanticCache: Bypassing cache for short query: '{query}'")
            return None

        try:
            filter_sig = self._filter_signature(ticker, requested_years, filing_type)
            # Embed the query together with its resolved filter signature so
            # different filter contexts land in different embedding space —
            # not just different metadata on an otherwise-identical vector.
            vector = self.embeddings.embed_query(f"{query}\n[filters: {filter_sig}]")

            # Construct filter for thread scoping
            query_filter = None
            if thread_id:
                query_filter = models.Filter(
                    must=[
                        models.FieldCondition(
                            key="thread_id",
                            match=models.MatchValue(value=thread_id)
                        )
                    ]
                )

            # Search
            results = self.client.query_points(
                collection_name=self.collection_name,
                query=vector,
                limit=1,
                query_filter=query_filter,  # Apply filter
                score_threshold=self.threshold,
                with_payload=True
            ).points

            if results:
                hit = results[0]
                # Belt-and-suspenders: even if the embedded filter signature
                # scored above threshold, refuse a hit whose stored filters
                # don't exactly match this request's resolved filters.
                if hit.payload.get("filter_signature") != filter_sig:
                    logger.info(f" SemanticCache: MISS (filter signature mismatch — cached='{hit.payload.get('filter_signature')}' requested='{filter_sig}')")
                    return None
                logger.info(f" SemanticCache: HIT (Score: {hit.score:.4f}, Thread: {thread_id}, Filters: {filter_sig})")
                return hit.payload
            else:
                scope_msg = f"Thread: {thread_id}" if thread_id else "Global"
                logger.info(f" SemanticCache: MISS ({scope_msg})")
                return None

        except Exception as e:
            logger.error(f" SemanticCache: Lookup error: {e}")
            return None

    def update(self, query: str, response_data: dict, thread_id: str = None, ticker: str = None,
               requested_years: list = None, filing_type: str = None):
        """
        Update the cache with a new query-response pair. `ticker`,
        `requested_years`, and `filing_type` must match what was passed to
        `lookup()` for this query so future lookups can be scoped correctly.
        """
        if not self.client:
            return

        try:
            filter_sig = self._filter_signature(ticker, requested_years, filing_type)
            vector = self.embeddings.embed_query(f"{query}\n[filters: {filter_sig}]")

            # Create payload
            payload = {
                "query": query,
                "response": response_data,
                "timestamp": time.time(),
                "thread_id": thread_id,
                "filter_signature": filter_sig,
                "ticker": ticker,
                "requested_years": requested_years,
                "filing_type": filing_type,
            }

            # Upsert
            from uuid import uuid4
            point_id = str(uuid4())

            self.client.upsert(
                collection_name=self.collection_name,
                points=[
                    models.PointStruct(
                        id=point_id,
                        vector=vector,
                        payload=payload
                    )
                ]
            )
            logger.info(f" SemanticCache: Saved response for '{query[:30]}...' (filters: {filter_sig})")

        except Exception as e:
            logger.error(f" SemanticCache: Update error: {e}")
