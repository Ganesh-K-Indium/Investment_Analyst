"""
Vector Database Manager
Manages ticker-specific vector database instances
"""
from rag.vectordb.client import load_vector_database
from typing import Dict, Optional, Tuple
import logging

logger = logging.getLogger("app.services.vectordb_manager")


class VectorDBManager:
    """
    Manages vector database instances per ticker.
    Lazy loads instances as needed.
    """
    
    def __init__(self):
        # Store DB instances keyed by TICKER (or special keys like 'legacy_unified')
        # Key: ticker (lowercase) or 'legacy_unified', Value: load_vector_database instance
        self._instances: Dict[str, load_vector_database] = {}
        
        # Keep track of active sessions/portfolios for management
        # mapping thread_id -> portfolio_id (still useful for context)
        self._session_to_portfolio: Dict[str, int] = {}
    
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
            
        ticker_key = ticker.lower()
        
        if ticker_key in self._instances:
            return self._instances[ticker_key]
        
        logger.info(f"Initializing Vector DB for ticker: {ticker} (create_if_missing={create_if_missing})")
        collection_name = f"ticker_{ticker_key}"
        
        # Create DB instance
        # We need to update load_vector_database to respect a 'create' flag 
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
            
        logger.info("Initializing Legacy Unified Vector DB")
        inst = load_vector_database(use_hybrid_search=True, collection_name="unified_rag_db_hybrid")
        self._instances["legacy_unified"] = inst
        return inst

    def initialize_for_portfolio(self, portfolio_id: int, company_names: list):
        """
        Deliberate no-op. Retrieval is fully lazy per-ticker via get_instance(ticker)
        — there is no per-portfolio vector DB to eagerly initialize. Kept only so
        existing call sites (app/api/portfolios.py) don't need a signature change;
        those call sites' comments should NOT describe this as doing real work.
        """

    def register_session(self, thread_id: str, portfolio_id: int):
        """
        Register a session to portfolio mapping.
        """
        self._session_to_portfolio[thread_id] = portfolio_id
        logger.info(f"Registered session {thread_id} to portfolio {portfolio_id}")
    
    def get_portfolio_id_for_session(self, thread_id: str) -> Optional[int]:
        """Get portfolio ID for a session."""
        return self._session_to_portfolio.get(thread_id)
        
    def get_for_session(self, thread_id: str) -> Optional[tuple]:
        """
        Legacy shim, not part of the active retrieval path. `retrieve()` in
        rag/graph/nodes.py calls get_instance(ticker) directly per company —
        this exists only so old call sites don't crash if still referenced.
        """
        return (self._get_legacy_instance(), [])

    def create_temporary(self, thread_id: str, company_names: list) -> tuple:
        """
        Legacy shim, not part of the active retrieval path. Comparison queries
        call get_instance(ticker) directly for each company — this exists only
        so old call sites don't crash if still referenced.
        """
        return (self._get_legacy_instance(), company_names)

    def cleanup_session(self, thread_id: str) -> bool:
        """Clean up session mapping."""
        if thread_id in self._session_to_portfolio:
            del self._session_to_portfolio[thread_id]
            return True
        return False
    
    def cleanup_portfolio(self, portfolio_id: int) -> bool:
        """
        Deliberate no-op. Cached per-ticker instances are shared across all
        portfolios that reference the same ticker, so there is nothing
        portfolio-specific to evict here. Kept only for call-site compatibility.
        """
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
