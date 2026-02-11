"""
Service layer for portfolio management
"""
from sqlalchemy.orm import Session
from app.database.models import Portfolio, Session as SessionModel
from typing import List, Optional
from datetime import datetime
import uuid


class PortfolioService:
    """Business logic for portfolio operations"""
    
    @staticmethod
    def create_portfolio(
        db: Session,
        user_id: str,
        name: str,
        tickers: List[str],
        description: Optional[str] = None
    ) -> Portfolio:
        """Create a new portfolio"""
        # Normalize tickers to lowercase for consistency
        normalized_tickers = [t.strip().lower() for t in tickers if t.strip()]
        
        portfolio = Portfolio(
            user_id=user_id,
            name=name,
            company_names=normalized_tickers, # Storing tickers in company_names column
            description=description
        )
        db.add(portfolio)
        db.commit()
        db.refresh(portfolio)
        return portfolio
    
    @staticmethod
    def get_portfolio(db: Session, portfolio_id: int) -> Optional[Portfolio]:
        """Get portfolio by ID"""
        return db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    
    @staticmethod
    def get_user_portfolios(db: Session, user_id: str) -> List[Portfolio]:
        """Get all portfolios for a user"""
        return db.query(Portfolio).filter(Portfolio.user_id == user_id).all()
    
    @staticmethod
    def update_portfolio(
        db: Session,
        portfolio_id: int,
        name: Optional[str] = None,
        tickers: Optional[List[str]] = None,
        description: Optional[str] = None
    ) -> Optional[Portfolio]:
        """Update an existing portfolio"""
        portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
        if not portfolio:
            return None
        
        if name is not None:
            portfolio.name = name
        if tickers is not None:
             # Normalize tickers
            portfolio.company_names = [t.strip().lower() for t in tickers if t.strip()]
        if description is not None:
            portfolio.description = description
        
        portfolio.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(portfolio)
        return portfolio
    
    @staticmethod
    def delete_portfolio(db: Session, portfolio_id: int) -> bool:
        """Delete a portfolio"""
        portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
        if not portfolio:
            return False
        
        db.delete(portfolio)
        db.commit()
        return True
    
    @staticmethod
    def create_session(
        db: Session,
        portfolio_id: int,
        user_id: str,
        thread_id: Optional[str] = None
    ) -> SessionModel:
        """Create a new session for a portfolio"""
        if not thread_id:
            thread_id = f"portfolio_{portfolio_id}_{uuid.uuid4()}"
        
        # Check if session already exists
        existing_session = db.query(SessionModel).filter(SessionModel.id == thread_id).first()
        if existing_session:
            # Update last accessed time
            existing_session.last_accessed = datetime.utcnow()
            db.commit()
            db.refresh(existing_session)
            return existing_session
        
        session = SessionModel(
            id=thread_id,
            portfolio_id=portfolio_id,
            user_id=user_id
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session
    
    @staticmethod
    def get_session(db: Session, thread_id: str) -> Optional[SessionModel]:
        """Get session by thread_id"""
        session = db.query(SessionModel).filter(SessionModel.id == thread_id).first()
        if session:
            # Update last accessed time
            session.last_accessed = datetime.utcnow()
            db.commit()
            db.refresh(session)
        return session
    
    @staticmethod
    def get_session_portfolio(db: Session, thread_id: str) -> Optional[Portfolio]:
        """Get portfolio associated with a session"""
        session = db.query(SessionModel).filter(SessionModel.id == thread_id).first()
        return session.portfolio if session else None
