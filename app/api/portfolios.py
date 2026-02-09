"""
Portfolio management endpoints
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import List, Optional
from sqlalchemy.orm import Session
from app.database.connection import get_db_session
from app.services.portfolio import PortfolioService
from app.services.vectordb_manager import get_vectordb_manager
from datetime import datetime

router = APIRouter(prefix="/portfolios", tags=["Portfolios"])


# Pydantic Models
class PortfolioCreate(BaseModel):
    user_id: str = Field(..., description="User identifier")
    name: str = Field(..., description="Portfolio name")
    company_names: List[str] = Field(..., description="List of company names to include in portfolio")
    description: Optional[str] = Field(None, description="Portfolio description")


class PortfolioUpdate(BaseModel):
    name: Optional[str] = Field(None, description="Updated portfolio name")
    company_names: Optional[List[str]] = Field(None, description="Updated list of company names")
    description: Optional[str] = Field(None, description="Updated portfolio description")


class PortfolioResponse(BaseModel):
    id: int
    user_id: str
    name: str
    company_names: List[str]
    description: Optional[str]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class SessionCreateRequest(BaseModel):
    portfolio_id: int = Field(..., description="Portfolio ID to create session for")
    user_id: str = Field(..., description="User identifier")
    thread_id: Optional[str] = Field(None, description="Optional custom thread_id")


class SessionResponse(BaseModel):
    thread_id: str
    portfolio_id: int
    user_id: str
    portfolio_name: str
    company_names: List[str]
    created_at: datetime
    last_accessed: datetime


@router.post("/", response_model=PortfolioResponse)
def create_portfolio(
    payload: PortfolioCreate,
    db: Session = Depends(get_db_session)
):
    """Create a new portfolio with specified companies and initialize Vector DB"""
    try:
        portfolio = PortfolioService.create_portfolio(
            db=db,
            user_id=payload.user_id,
            name=payload.name,
            company_names=payload.company_names,
            description=payload.description
        )
        
        # CRITICAL: Initialize Vector DB ONCE at portfolio creation
        # All future sessions will reuse this same DB instance
        vectordb_mgr = get_vectordb_manager()
        try:
            vectordb_mgr.initialize_for_portfolio(
                portfolio_id=portfolio.id,
                company_names=portfolio.company_names
            )
            print(f"Portfolio created with Vector DB initialized")
            print(f"   Portfolio ID: {portfolio.id}")
            print(f"   Companies: {portfolio.company_names}")
        except Exception as e:
            print(f"Warning: Failed to initialize Vector DB: {e}")
            print("   Portfolio created but RAG queries may fail")
        
        return portfolio
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create portfolio: {str(e)}")


@router.get("/{portfolio_id}", response_model=PortfolioResponse)
def get_portfolio(
    portfolio_id: int,
    db: Session = Depends(get_db_session)
):
    """Get portfolio by ID"""
    portfolio = PortfolioService.get_portfolio(db, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return portfolio


@router.get("/user/{user_id}", response_model=List[PortfolioResponse])
def get_user_portfolios(
    user_id: str,
    db: Session = Depends(get_db_session)
):
    """Get all portfolios for a user"""
    portfolios = PortfolioService.get_user_portfolios(db, user_id)
    return portfolios


@router.put("/{portfolio_id}", response_model=PortfolioResponse)
def update_portfolio(
    portfolio_id: int,
    payload: PortfolioUpdate,
    db: Session = Depends(get_db_session)
):
    """Update an existing portfolio and re-initialize Vector DB if companies changed"""
    portfolio = PortfolioService.update_portfolio(
        db=db,
        portfolio_id=portfolio_id,
        name=payload.name,
        company_names=payload.company_names,
        description=payload.description
    )
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    
    # If company names were updated, re-initialize the Vector DB
    if payload.company_names is not None:
        vectordb_mgr = get_vectordb_manager()
        try:
            # Clean up old instance and create new one
            vectordb_mgr.cleanup_portfolio(portfolio_id)
            vectordb_mgr.initialize_for_portfolio(
                portfolio_id=portfolio.id,
                company_names=portfolio.company_names
            )
            
            # Re-register all existing sessions for this portfolio
            # (in case any were active before the update)
            from app.database.models import Session as SessionModel
            sessions = db.query(SessionModel).filter(
                SessionModel.portfolio_id == portfolio_id
            ).all()
            
            for session in sessions:
                vectordb_mgr.register_session(session.id, portfolio_id)
            
            print(f"Portfolio {portfolio_id} updated and Vector DB re-initialized")
            print(f"   New companies: {portfolio.company_names}")
            print(f"   Re-registered {len(sessions)} existing sessions")
        except Exception as e:
            print(f"Warning: Failed to re-initialize Vector DB: {e}")
    
    return portfolio


@router.delete("/{portfolio_id}")
def delete_portfolio(
    portfolio_id: int,
    db: Session = Depends(get_db_session)
):
    """Delete a portfolio and cleanup its Vector DB instance"""
    success = PortfolioService.delete_portfolio(db, portfolio_id)
    if not success:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    
    # Cleanup the Vector DB instance for this portfolio
    vectordb_mgr = get_vectordb_manager()
    vectordb_mgr.cleanup_portfolio(portfolio_id)
    print(f"Portfolio {portfolio_id} deleted and Vector DB cleaned up")
    
    return {"message": "Portfolio deleted successfully"}


@router.post("/sessions", response_model=SessionResponse)
def create_session(
    payload: SessionCreateRequest,
    db: Session = Depends(get_db_session)
):
    """
    Create a new session for a portfolio.
    Simply registers the session to the existing portfolio Vector DB.
    """
    # Verify portfolio exists
    portfolio = PortfolioService.get_portfolio(db, payload.portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    
    # Create session
    session = PortfolioService.create_session(
        db=db,
        portfolio_id=payload.portfolio_id,
        user_id=payload.user_id,
        thread_id=payload.thread_id
    )
    
    # Register this session to the portfolio's Vector DB
    # The Vector DB was initialized when the portfolio was created
    vectordb_mgr = get_vectordb_manager()
    vectordb_mgr.register_session(
        thread_id=session.id,
        portfolio_id=portfolio.id
    )
    
    # Verify the Vector DB exists for this portfolio
    vectordb_result = vectordb_mgr.get_for_portfolio(portfolio.id)
    if not vectordb_result:
        # Lazy initialization if DB not found (handles server restarts)
        print(f"Vector DB not found for portfolio {portfolio.id}, initializing...")
        try:
            vectordb_mgr.initialize_for_portfolio(
                portfolio_id=portfolio.id,
                company_names=portfolio.company_names
            )
            print(f"Successfully initialized Vector DB for portfolio {portfolio.id}")
        except Exception as e:
            print(f"Warning: Failed to initialize Vector DB: {e}")
            print("   Session created but RAG queries may fail")
    
    print(f"Session created and registered to portfolio Vector DB")
    print(f"   Session ID: {session.id}")
    print(f"   Portfolio ID: {portfolio.id}")
    print(f"   Companies: {portfolio.company_names}")
    
    return SessionResponse(
        thread_id=session.id,
        portfolio_id=session.portfolio_id,
        user_id=session.user_id,
        portfolio_name=portfolio.name,
        company_names=portfolio.company_names,
        created_at=session.created_at,
        last_accessed=session.last_accessed
    )


@router.get("/sessions/{thread_id}", response_model=SessionResponse)
def get_session(
    thread_id: str,
    db: Session = Depends(get_db_session)
):
    """Get session and associated portfolio information"""
    session = PortfolioService.get_session(db, thread_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    portfolio = session.portfolio
    
    return SessionResponse(
        thread_id=session.id,
        portfolio_id=session.portfolio_id,
        user_id=session.user_id,
        portfolio_name=portfolio.name,
        company_names=portfolio.company_names,
        created_at=session.created_at,
        last_accessed=session.last_accessed
    )
