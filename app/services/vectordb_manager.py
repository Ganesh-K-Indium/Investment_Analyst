"""
Vector Database Manager
Manages ticker-specific vector database instances
"""
from rag.vectordb.client import load_vector_database
from typing import Dict, Optional, Tuple


class VectorDBManager:
    """
    Manages vector database instances per ticker.
    Lazy loads instances as needed.
    """
    
    # Ticker aliases: map share-class variants to the base ticker used in Qdrant
    # e.g. BRK-A, BRK-B, BRK.A, BRK.B all map to BRK (collection = ticker_brk)
    TICKER_ALIASES = {
        'brk-a': 'brk',
        'brk-b': 'brk',
        'brk.a': 'brk',
        'brk.b': 'brk',
    }
    
    def __init__(self):
        # Store DB instances keyed by TICKER (or special keys like 'legacy_unified')
        # Key: ticker (lowercase) or 'legacy_unified', Value: load_vector_database instance
        self._instances: Dict[str, load_vector_database] = {}
        
        # Keep track of active sessions/portfolios for management
        # mapping thread_id -> portfolio_id (still useful for context)
        self._session_to_portfolio: Dict[str, int] = {}
    
    @classmethod
    def normalize_ticker(cls, ticker: str) -> str:
        """
        Normalize a ticker to its base form (e.g. BRK-B → BRK, BRK.A → BRK).
        Handles both dot and hyphen variants by converting dots to hyphens first.
        """
        lowered = ticker.lower()
        # Check direct alias first (handles both dot and hyphen variants)
        if lowered in cls.TICKER_ALIASES:
            return cls.TICKER_ALIASES[lowered]
        # Convert dots to hyphens and check again
        hyphenated = lowered.replace('.', '-')
        if hyphenated in cls.TICKER_ALIASES:
            return cls.TICKER_ALIASES[hyphenated]
        return lowered
    
    def get_instance(self, ticker: str, create_if_missing: bool = False) -> load_vector_database:
        """
        Get or create a vector DB instance for a specific ticker.
        
        Args:
            ticker: Stock ticker symbol
            create_if_missing: If True, creates collection if it doesn't exist. 
                             If False, it might still create if load_vector_database logic forces it,
                             so we should update load_vector_database too, but for now this signals intent.
            
        Returns:
            Initialized load_vector_database instance for that ticker
        """
        if not ticker:
            # Fallback to unified DB if no ticker provided (legacy support)
            return self._get_legacy_instance()
            
        ticker_key = self.normalize_ticker(ticker)
        
        if ticker_key in self._instances:
            return self._instances[ticker_key]
        
        print(f"Initializing Vector DB for ticker: {ticker} (normalized: {ticker_key}, create_if_missing={create_if_missing})")
        collection_name = f"ticker_{ticker_key}"
        
        # Create DB instance
        # Note: We need to update load_vector_database to respect a 'create' flag 
        # or we rely on it checking existence. 
        # For now, we instantiate it, but we'll modify load_vector_database next to not auto-create.
        db_instance = load_vector_database(
            use_hybrid_search=True,
            collection_name=collection_name,
            create_if_missing=create_if_missing
        )
        
        self._instances[ticker_key] = db_instance
        return db_instance
    
    def _get_legacy_instance(self):
        """Get the legacy unified instance."""
        if "legacy_unified" in self._instances:
            return self._instances["legacy_unified"]
            
        print("Initializing Legacy Unified Vector DB")
        inst = load_vector_database(use_hybrid_search=True, collection_name="unified_rag_db_hybrid")
        self._instances["legacy_unified"] = inst
        return inst

    def initialize_for_portfolio(self, portfolio_id: int, company_names: list):
        """
        No-op for ticker-based system, but kept for compatibility.
        We lazy-load based on query ticker now.
        """
        print(f"Portfolio {portfolio_id} initialized (using dynamic ticker loading)")
    
    def register_session(self, thread_id: str, portfolio_id: int):
        """
        Register a session to portfolio mapping.
        """
        self._session_to_portfolio[thread_id] = portfolio_id
        print(f"Registered session {thread_id} to portfolio {portfolio_id}")
    
    def get_portfolio_id_for_session(self, thread_id: str) -> Optional[int]:
        """Get portfolio ID for a session."""
        return self._session_to_portfolio.get(thread_id)
        
    def get_for_session(self, thread_id: str) -> Optional[tuple]:
        """
        Legacy support: Returns (legacy_instance, []) to prevent crashes.
        The retrieve node should now use get_instance(ticker).
        """
        return (self._get_legacy_instance(), [])
    
    def create_temporary(self, thread_id: str, company_names: list) -> tuple:
        """
        Legacy support for comparison. Return dummy values.
        Comparisons should also use get_instance(ticker) for each company.
        """
        print(f"Temporary DB request for {company_names} - using dynamic retrieval instead")
        return (self._get_legacy_instance(), company_names)

    def cleanup_session(self, thread_id: str) -> bool:
        """Clean up session mapping."""
        if thread_id in self._session_to_portfolio:
            del self._session_to_portfolio[thread_id]
            return True
        return False
    
    def cleanup_portfolio(self, portfolio_id: int) -> bool:
        """Cleanup portfolio related resources."""
        # For ticker-based instances, we might want to keep frequent tickers cached?
        # Or we could iterate and clear unused ones?
        # For now, keep it simple.
        return True
    
    def get_stats(self) -> dict:
        """Get manager stats."""
        return {
            "cached_tickers": list(self._instances.keys()),
            "active_sessions": len(self._session_to_portfolio)
        }


# Global singleton instance
_vectordb_manager = None


def get_vectordb_manager() -> VectorDBManager:
    """Get the global VectorDB manager instance"""
    global _vectordb_manager
    if _vectordb_manager is None:
        _vectordb_manager = VectorDBManager()
    return _vectordb_manager
