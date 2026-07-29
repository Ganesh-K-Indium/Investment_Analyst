"""
Portfolio management endpoints
"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field, validator
from typing import List, Optional, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.connection import get_db_session
from app.services.portfolio import PortfolioService
from app.services.chat import ChatService
from app.services.vectordb_manager import get_vectordb_manager
from app.database.models import AgentType, User
from app.auth.deps import get_current_user, verify_user_id_matches, verify_owner
from datetime import datetime

logger = logging.getLogger("api.portfolios")
router = APIRouter(prefix="/portfolios", tags=["Portfolios"])


# Pydantic Models
class PortfolioCreate(BaseModel):
    user_id: str = Field(..., description="User identifier")
    name: str = Field(..., description="Portfolio name")
    tickers: List[str] = Field(..., description="List of stock tickers to include in portfolio")
    description: Optional[str] = Field(None, description="Portfolio description")


class PortfolioUpdate(BaseModel):
    name: Optional[str] = Field(None, description="Updated portfolio name")
    tickers: Optional[List[str]] = Field(None, description="Updated list of tickers")
    description: Optional[str] = Field(None, description="Updated portfolio description")


class PortfolioResponse(BaseModel):
    id: int
    user_id: str
    name: str
    tickers: List[str]
    description: Optional[str]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True
        
    @validator('tickers', pre=True, always=True)
    def map_company_names_to_tickers(cls, v, values):
        # If the input contains 'company_names' (from DB model), map it to tickers
        # Use simple attribute access check since we might get a dict or object
        if hasattr(values, 'company_names'):
            return values.company_names
        # If we are creating from dict and it has company_names
        if isinstance(v, list): 
            return v
        return []
    
    def __init__(self, **data):
        # Handle renaming from DB model 'company_names' to 'tickers'
        if 'company_names' in data:
            data['tickers'] = data.pop('company_names')
        elif hasattr(data.get('_orm_object'), 'company_names'):
             pass # Logic handled by validator or manual mapping in endpoint
        super().__init__(**data)

# Simplify Payload mapping approach:
# Manually map the DB object to this Pydantic model in the endpoint if auto-mapping fails for renamed fields.
# Actually, Pydantic V2 alias_generator might be complex. 
# Keep it simple: Use a static method or just map manually in the route if needed. 
# OR: Just use `company_names` field in Response but annotated as tickers? 
# No, user wants refactor.

class SessionCreateRequest(BaseModel):
    portfolio_id: int = Field(..., description="Portfolio ID to create session for")
    user_id: str = Field(..., description="User identifier")
    thread_id: Optional[str] = Field(None, description="Optional custom thread_id")
    agent_type: Optional[str] = Field("rag", description="Agent type: 'rag' or 'quant'")


class SessionResponse(BaseModel):
    thread_id: str
    portfolio_id: int
    user_id: str
    portfolio_name: str
    tickers: List[str]
    created_at: datetime
    last_accessed: datetime


@router.post("", response_model=PortfolioResponse)
async def create_portfolio(
    payload: PortfolioCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Create a new portfolio with specified tickers and initialize Vector DB"""
    verify_user_id_matches(payload.user_id, current_user)
    try:
        portfolio = await PortfolioService.create_portfolio(
            db=db,
            user_id=payload.user_id,
            name=payload.name,
            tickers=payload.tickers,
            description=payload.description
        )
        
        # Note: there is no per-portfolio vector DB to initialize — retrieval is
        # fully lazy, per-ticker (see VectorDBManager.get_instance()). Nothing
        # to do here at portfolio-creation time.

        # Manually map for response because of field rename
        return PortfolioResponse(
            id=portfolio.id,
            user_id=portfolio.user_id,
            name=portfolio.name,
            tickers=portfolio.company_names,
            description=portfolio.description,
            created_at=portfolio.created_at,
            updated_at=portfolio.updated_at
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create portfolio: {str(e)}")


@router.get("/{portfolio_id}", response_model=PortfolioResponse)
async def get_portfolio(
    portfolio_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Get portfolio by ID"""
    portfolio = await PortfolioService.get_portfolio(db, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    verify_owner(portfolio.user_id, current_user)

    return PortfolioResponse(
        id=portfolio.id,
        user_id=portfolio.user_id,
        name=portfolio.name,
        tickers=portfolio.company_names,
        description=portfolio.description,
        created_at=portfolio.created_at,
        updated_at=portfolio.updated_at
    )


@router.get("/user/{user_id}", response_model=List[PortfolioResponse])
async def get_user_portfolios(
    user_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Get all portfolios for a user"""
    verify_user_id_matches(user_id, current_user)
    portfolios = await PortfolioService.get_user_portfolios(db, user_id)
    return [
        PortfolioResponse(
            id=p.id,
            user_id=p.user_id,
            name=p.name,
            tickers=p.company_names,
            description=p.description,
            created_at=p.created_at,
            updated_at=p.updated_at
        ) for p in portfolios
    ]


@router.put("/{portfolio_id}", response_model=PortfolioResponse)
async def update_portfolio(
    portfolio_id: int,
    payload: PortfolioUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Update an existing portfolio"""
    existing = await PortfolioService.get_portfolio(db, portfolio_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    verify_owner(existing.user_id, current_user)

    portfolio = await PortfolioService.update_portfolio(
        db=db,
        portfolio_id=portfolio_id,
        name=payload.name,
        tickers=payload.tickers,
        description=payload.description
    )
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    # Retrieval is lazy per-ticker (VectorDBManager.get_instance()) — there is
    # no per-portfolio vector DB to re-initialize when tickers change. The one
    # real piece of state to refresh is the in-memory thread_id -> portfolio_id
    # map used by get_portfolio_id_for_session(), for this portfolio's sessions.
    if payload.tickers is not None:
        vectordb_mgr = get_vectordb_manager()
        from app.database.models import Session as SessionModel
        sessions_result = await db.execute(
            select(SessionModel).where(SessionModel.portfolio_id == portfolio_id)
        )
        sessions = sessions_result.scalars().all()
        for session in sessions:
            vectordb_mgr.register_session(session.id, portfolio_id)
        logger.info("Portfolio %s updated (tickers: %s); re-registered %d session(s)",
                    portfolio_id, portfolio.company_names, len(sessions))

    return PortfolioResponse(
        id=portfolio.id,
        user_id=portfolio.user_id,
        name=portfolio.name,
        tickers=portfolio.company_names,
        description=portfolio.description,
        created_at=portfolio.created_at,
        updated_at=portfolio.updated_at
    )


@router.delete("/{portfolio_id}")
async def delete_portfolio(
    portfolio_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Delete a portfolio"""
    existing = await PortfolioService.get_portfolio(db, portfolio_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    verify_owner(existing.user_id, current_user)

    success = await PortfolioService.delete_portfolio(db, portfolio_id)
    if not success:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    logger.info("Portfolio %s deleted", portfolio_id)

    return {"message": "Portfolio deleted successfully"}


@router.post("/sessions", response_model=SessionResponse)
async def create_session(
    payload: SessionCreateRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new session for a portfolio.
    Simply registers the session to the existing portfolio Vector DB.
    """
    verify_user_id_matches(payload.user_id, current_user)

    # Verify portfolio exists and belongs to this user
    portfolio = await PortfolioService.get_portfolio(db, payload.portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    verify_owner(portfolio.user_id, current_user)

    # Create session
    session = await PortfolioService.create_session(
        db=db,
        portfolio_id=payload.portfolio_id,
        user_id=payload.user_id,
        thread_id=payload.thread_id
    )

    # Resolve agent type from payload (default to RAG for backward-compatibility)
    try:
        agent_type_enum = AgentType((payload.agent_type or "rag").lower())
    except ValueError:
        agent_type_enum = AgentType.RAG

    title_prefix = "Quant" if agent_type_enum == AgentType.QUANT else "RAG"

    # Also register a ChatSession so the session is immediately deletable
    # via DELETE /chats/session/{session_id} even before any message is sent.
    await ChatService.create_or_get_chat_session(
        db=db,
        session_id=session.id,
        user_id=payload.user_id,
        agent_type=agent_type_enum,
        portfolio_id=payload.portfolio_id,
        title=f"{title_prefix}: {portfolio.name}"
    )

    # Register this session to the portfolio's Vector DB context
    vectordb_mgr = get_vectordb_manager()
    vectordb_mgr.register_session(
        thread_id=session.id,
        portfolio_id=portfolio.id
    )

    logger.info("Session %s created for portfolio %s (tickers: %s)",
                session.id, portfolio.id, portfolio.company_names)


    return SessionResponse(
        thread_id=session.id,
        portfolio_id=session.portfolio_id,
        user_id=session.user_id,
        portfolio_name=portfolio.name,
        tickers=portfolio.company_names,
        created_at=session.created_at,
        last_accessed=session.last_accessed
    )


@router.get("/sessions/{thread_id}", response_model=SessionResponse)
async def get_session(
    thread_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Get session and associated portfolio information"""
    session = await PortfolioService.get_session(db, thread_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    verify_owner(session.user_id, current_user)

    portfolio = session.portfolio
    
    return SessionResponse(
        thread_id=session.id,
        portfolio_id=session.portfolio_id,
        user_id=session.user_id,
        portfolio_name=portfolio.name,
        tickers=portfolio.company_names,
        created_at=session.created_at,
        last_accessed=session.last_accessed
    )
