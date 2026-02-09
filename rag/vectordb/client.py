"""
this module is used for loading the unified RAG database with hybrid search capabilities
"""

from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import QdrantVectorStore, RetrievalMode
from qdrant_client import QdrantClient
from qdrant_client import models
from tqdm import tqdm
import os

load_dotenv()

# Try to import FastEmbed for sparse embeddings
try:
    from fastembed import SparseTextEmbedding
    SPARSE_EMBEDDING_AVAILABLE = True
except ImportError:
    SPARSE_EMBEDDING_AVAILABLE = False
    print("Warning: fastembed not available. Install with: pip install fastembed")

class load_vector_database():
    """Unified vector database loader with advanced hybrid search capabilities"""
    
    def __init__(self, use_hybrid_search: bool = True):
        """
        Initialize unified vector database loader with hybrid search.
        
        Args:
            use_hybrid_search: If True, use hybrid search with dense, sparse (BM25), and ColBERT vectors.
        """
        # Use unified collection for both text and images
        self.collection_name = "unified_rag_db_hybrid"
        self.use_hybrid_search = use_hybrid_search
        
        # Initialize embeddings
        self.embeddings = OpenAIEmbeddings()
        self.qdrant_url = os.getenv("QDRANT_URL", "")
        self.qdrant_api_key = os.getenv("QDRANT_API_KEY", '')
        
        # Initialize sparse embeddings for BM25 if available
        self.sparse_model = None
        if use_hybrid_search and SPARSE_EMBEDDING_AVAILABLE:
            try:
                self.sparse_model = SparseTextEmbedding(model_name="Qdrant/bm25")
                print("BM25 sparse embeddings initialized")
            except Exception as e:
                print(f"Warning: Failed to initialize sparse embeddings: {e}")
        
        # Try cloud Qdrant first, fallback to local
        try:
            print(f"Attempting to connect to Qdrant at: {self.qdrant_url}")
            self.qdrant_client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key, timeout=60)
            self.qdrant_client.get_collections()
            print(f"Successfully connected to Qdrant at {self.qdrant_url}")
        except Exception as e:
            print(f"Failed to connect to cloud Qdrant: {e}")
            print("Falling back to local Qdrant at http://localhost:6333")
            self.qdrant_url = "http://localhost:6333"
            self.qdrant_api_key = ''
            try:
                self.qdrant_client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key, timeout=60)
                self.qdrant_client.get_collections()
                print(f"Successfully connected to local Qdrant")
            except Exception as local_error:
                print(f"Failed to connect to local Qdrant: {local_error}")
                raise ConnectionError("Unable to connect to Qdrant instances.")
    
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
    
    def hybrid_search(self, query: str, content_type: str = None, company: str = None, 
                     limit: int = 10, dense_limit: int = 100, sparse_limit: int = 100):
        """
        Advanced hybrid search using prefetch and fusion queries (RRF).
        
        Args:
            query: Search query text
            content_type: Filter by content type ("text" or "image"), None for both
            company: Filter by company name
            limit: Final number of results to return
            dense_limit: Number of results from dense vector search
            sparse_limit: Number of results from sparse (BM25) search
            
        Returns:
            List of search results with payloads
        """
        # Generate dense embeddings (OpenAI)
        dense_vector = self.embeddings.embed_query(query)
        
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
                print(f"Warning: Failed to generate sparse embedding: {e}")
        
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
        
        global_filter = models.Filter(must=filter_conditions) if filter_conditions else None
        
        # Build hybrid query with prefetch and fusion
        prefetch_queries = []
        
        # Dense retrieval: semantic understanding
        prefetch_queries.append(
            models.Prefetch(
                query=dense_vector,
                using="dense",
                limit=dense_limit
            )
        )
        
        # Sparse retrieval: exact term matching with BM25
        if sparse_vector:
            prefetch_queries.append(
                models.Prefetch(
                    query=sparse_vector,
                    using="bm25",
                    limit=sparse_limit
                )
            )
        
        # Fusion query combining dense and sparse results with RRF
        fusion_prefetch = models.Prefetch(
            prefetch=prefetch_queries,
            query=models.FusionQuery(fusion=models.Fusion.RRF),  # Reciprocal Rank Fusion
            limit=limit
        )
        
        # Final query using RRF fusion results
        try:
            response = self.qdrant_client.query_points(
                collection_name=self.collection_name,
                prefetch=fusion_prefetch,
                query=dense_vector,  # Use dense for final scoring
                using="dense",
                query_filter=global_filter,
                limit=limit,
                with_payload=True,
            )
            
            return response.points
            
        except Exception as e:
            print(f"Error in hybrid search: {e}")
            # Fallback to simple dense search
            return self._fallback_search(dense_vector, global_filter, limit)
    
    def _fallback_search(self, query_vector, query_filter, limit):
        """Fallback to simple dense vector search if hybrid search fails."""
        try:
            response = self.qdrant_client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                using="dense",
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
            return response.points
        except Exception as e:
            print(f"Error in fallback search: {e}")
            return []
    
    def generate_embeddings_for_ingestion(self, texts: list[str]) -> dict:
        """
        Generate all required embeddings (dense, sparse) for document ingestion.
        
        Args:
            texts: List of text strings to embed
            
        Returns:
            dict with 'dense' and 'sparse' embedding lists
        """
        result = {
            'dense': [],
            'sparse': []
        }
        
        # Generate dense embeddings (OpenAI)
        print(f"\nGenerating dense embeddings for {len(texts)} documents...")
        for text in tqdm(texts, desc="Dense embeddings (OpenAI)", unit="doc"):
            dense_emb = self.embeddings.embed_query(text)
            result['dense'].append(dense_emb)
        
        # Generate sparse embeddings (BM25)
        if self.sparse_model:
            print(f"\nGenerating sparse embeddings for {len(texts)} documents...")
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
                print(f"Warning: Failed to generate sparse embeddings: {e}")
                result['sparse'] = [None] * len(texts)
        else:
            result['sparse'] = [None] * len(texts)
        
        print(f"Generated embeddings: {len(result['dense'])} dense, {len([s for s in result['sparse'] if s is not None])} sparse")
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



