"""
Vector Database Manager
Manages portfolio-scoped vector database instances
"""
from rag.vectordb.client import load_vector_database
from typing import Dict, Optional


class VectorDBManager:
    """
    Manages vector database instances per portfolio.
    Each portfolio gets ONE pre-filtered DB instance shared across all sessions.
    """
    
    def __init__(self):
        # Store DB instances per PORTFOLIO (not per session!)
        # Key: portfolio_id, Value: (load_vector_database instance, company_filter)
        self._instances: Dict[int, tuple] = {}
        
        # Map thread_id to portfolio_id for quick lookup
        self._session_to_portfolio: Dict[str, int] = {}
    
    def initialize_for_portfolio(self, portfolio_id: int, company_names: list) -> load_vector_database:
        """
        Initialize a vector DB instance pre-filtered for portfolio companies.
        This happens ONCE at portfolio creation.
        
        Args:
            portfolio_id: Portfolio ID
            company_names: List of company names for this portfolio
            
        Returns:
            Initialized and filtered load_vector_database instance
        """
        # Check if already initialized for this portfolio
        if portfolio_id in self._instances:
            print(f"Vector DB already initialized for portfolio {portfolio_id}, reusing...")
            return self._instances[portfolio_id][0]
        
        print(f"Initializing Vector DB for portfolio")
        print(f"   Portfolio ID: {portfolio_id}")
        print(f"   Companies: {company_names}")
        
        # Create DB instance with hybrid search
        db_instance = load_vector_database(use_hybrid_search=True)
        
        # Store the instance and company filter by PORTFOLIO_ID
        self._instances[portfolio_id] = (db_instance, company_names)
        
        print(f"Vector DB initialized and cached for portfolio: {portfolio_id}")
        return db_instance
    
    def register_session(self, thread_id: str, portfolio_id: int):
        """
        Register a session to portfolio mapping.
        This allows quick lookup of portfolio DB by thread_id.
        
        Args:
            thread_id: Session thread ID
            portfolio_id: Portfolio ID this session belongs to
        """
        self._session_to_portfolio[thread_id] = portfolio_id
        print(f"Registered session {thread_id} to portfolio {portfolio_id}")
    
    def get_for_session(self, thread_id: str) -> Optional[tuple]:
        """
        Get the pre-initialized DB instance for a session.
        Looks up portfolio via thread_id mapping.
        
        Args:
            thread_id: Session thread ID
            
        Returns:
            Tuple of (db_instance, company_filter) or None if not found
        """
        # Get portfolio_id from session mapping
        portfolio_id = self._session_to_portfolio.get(thread_id)
        if not portfolio_id:
            print(f"No portfolio mapping found for thread: {thread_id}")
            return None
        
        # Get DB instance for this portfolio
        result = self._instances.get(portfolio_id)
        if result:
            print(f"Using cached Vector DB for thread: {thread_id}")
            print(f"   Portfolio ID: {portfolio_id}")
            print(f"   Companies: {result[1]}")
        return result
    
    def get_for_portfolio(self, portfolio_id: int) -> Optional[tuple]:
        """
        Get the DB instance directly by portfolio ID.
        
        Args:
            portfolio_id: Portfolio ID
            
        Returns:
            Tuple of (db_instance, company_filter) or None if not found
        """
        result = self._instances.get(portfolio_id)
        if result:
            print(f"Using cached Vector DB for portfolio: {portfolio_id}")
            print(f"   Companies: {result[1]}")
        return result
    
    def create_temporary(self, thread_id: str, company_names: list) -> tuple:
        """
        Create a temporary DB instance for ad-hoc queries (like compare).
        Uses negative portfolio IDs to avoid conflicts with real portfolios.
        
        Args:
            thread_id: Thread ID for this temporary session
            company_names: List of companies for this temporary query
            
        Returns:
            Tuple of (db_instance, company_filter)
        """
        print(f"Creating temporary Vector DB instance")
        print(f"   Thread ID: {thread_id}")
        print(f"   Companies: {company_names}")
        
        db_instance = load_vector_database(use_hybrid_search=True)
        
        # Use a negative portfolio_id for temporary instances to avoid conflicts
        temp_portfolio_id = -hash(thread_id) % (10 ** 8)
        
        # Store for retrieval by graph nodes
        self._instances[temp_portfolio_id] = (db_instance, company_names)
        self._session_to_portfolio[thread_id] = temp_portfolio_id
        
        print(f"Temporary Vector DB created and cached for thread: {thread_id}")
        return (db_instance, company_names)
    
    def cleanup_session(self, thread_id: str) -> bool:
        """
        Clean up session mapping (DB instances are portfolio-level, not session-level).
        
        Args:
            thread_id: Session thread ID
            
        Returns:
            True if cleaned up, False if not found
        """
        if thread_id in self._session_to_portfolio:
            portfolio_id = self._session_to_portfolio[thread_id]
            del self._session_to_portfolio[thread_id]
            
            # Only cleanup temporary instances (negative IDs)
            if portfolio_id < 0 and portfolio_id in self._instances:
                del self._instances[portfolio_id]
                print(f"Cleaned up temporary Vector DB for thread: {thread_id}")
            else:
                print(f"Unregistered session mapping for thread: {thread_id}")
            return True
        return False
    
    def cleanup_portfolio(self, portfolio_id: int) -> bool:
        """
        Clean up DB instance for a portfolio (e.g., when portfolio is deleted).
        
        Args:
            portfolio_id: Portfolio ID
            
        Returns:
            True if cleaned up, False if not found
        """
        if portfolio_id in self._instances:
            del self._instances[portfolio_id]
            # Also clean up any session mappings
            sessions_to_remove = [
                tid for tid, pid in self._session_to_portfolio.items() 
                if pid == portfolio_id
            ]
            for tid in sessions_to_remove:
                del self._session_to_portfolio[tid]
            print(f"Cleaned up Vector DB for portfolio: {portfolio_id}")
            return True
        return False
    
    def get_stats(self) -> dict:
        """Get statistics about cached instances"""
        return {
            "total_portfolio_instances": len(self._instances),
            "total_session_mappings": len(self._session_to_portfolio),
            "portfolio_ids": list(self._instances.keys()),
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
