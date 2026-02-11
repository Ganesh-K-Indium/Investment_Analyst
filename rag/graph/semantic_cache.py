import os
import json
import time
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.http import models

load_dotenv()

class SemanticCache:
    """
    Semantic Cache system to reduce latency by returning cached responses
    for semantically similar queries.
    """
    
    def __init__(self, threshold: float = 0.90):
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
            print(f" SemanticCache: Connecting to Qdrant at {self.qdrant_url}...")
            self.client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key, timeout=10)
            self.client.get_collections() # Test connection
            print(" SemanticCache: Connected to Cloud Qdrant")
        except Exception as e:
            print(f" SemanticCache: Cloud connection failed ({e}). Trying local...")
            self.qdrant_url = "http://localhost:6333"
            self.qdrant_api_key = ''
            try:
                self.client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key, timeout=5)
                print("SemanticCache: Connected to Local Qdrant")
            except Exception as local_e:
                print(f" SemanticCache: Failed to connect to Qdrant. Caching disabled. {local_e}")
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
                print(f" SemanticCache: Creating collection '{self.collection_name}'...")
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=1536,  # OpenAI embedding size
                        distance=models.Distance.COSINE
                    )
                )
                print(f" SemanticCache: Collection created.")
            
            # Ensure payload index for thread_id (required for filtering)
            self.client.create_payload_index(
                collection_name=self.collection_name,
                field_name="thread_id",
                field_schema=models.PayloadSchemaType.KEYWORD
            )
            print(" SemanticCache: Thread ID index ensured.")
        except Exception as e:
            print(f" SemanticCache: Error ensuring collection: {e}")

    def lookup(self, query: str, thread_id: str = None):
        """
        Look up a query in the cache.
        
        Args:
            query: The user query string.
            thread_id: Optional thread ID to scope the cache lookup.
        
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
            print(f" SemanticCache: Bypassing cache for context-dependent query: '{query}'")
            return None
            
        # Check for very short queries (likely follow-ups)
        if len(query.split()) < 3:
            print(f" SemanticCache: Bypassing cache for short query: '{query}'")
            return None
            
        try:
            # Generate embedding
            vector = self.embeddings.embed_query(query)
            
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
                print(f"⚡ SemanticCache: HIT (Score: {hit.score:.4f}, Thread: {thread_id})")
                return hit.payload
            else:
                scope_msg = f"Thread: {thread_id}" if thread_id else "Global"
                print(f" SemanticCache: MISS ({scope_msg})")
                return None
                
        except Exception as e:
            print(f" SemanticCache: Lookup error: {e}")
            return None

    def update(self, query: str, response_data: dict, thread_id: str = None):
        """
        Update the cache with a new query-response pair.
        """
        if not self.client:
            return
            
        try:
            # Generate embedding
            vector = self.embeddings.embed_query(query)
            
            # Create payload
            payload = {
                "query": query,
                "response": response_data,
                "timestamp": time.time(),
                "thread_id": thread_id
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
            print(f" SemanticCache: Saved response for '{query[:30]}...'")
            
        except Exception as e:
            print(f" SemanticCache: Update error: {e}")
