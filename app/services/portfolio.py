"""
Service layer for portfolio management
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.database.models import Portfolio, Session as SessionModel
from typing import List, Optional
from datetime import datetime
import uuid


class PortfolioService:
    """Business logic for portfolio operations"""

    @staticmethod
    async def create_portfolio(
        db: AsyncSession,
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
        await db.commit()
        await db.refresh(portfolio)
        return portfolio

    @staticmethod
    async def get_portfolio(db: AsyncSession, portfolio_id: int) -> Optional[Portfolio]:
        """Get portfolio by ID"""
        result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_user_portfolios(db: AsyncSession, user_id: str) -> List[Portfolio]:
        """Get all portfolios for a user"""
        result = await db.execute(select(Portfolio).where(Portfolio.user_id == user_id))
        return list(result.scalars().all())

    @staticmethod
    async def update_portfolio(
        db: AsyncSession,
        portfolio_id: int,
        name: Optional[str] = None,
        tickers: Optional[List[str]] = None,
        description: Optional[str] = None
    ) -> Optional[Portfolio]:
        """Update an existing portfolio"""
        result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
        portfolio = result.scalar_one_or_none()
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
        await db.commit()
        await db.refresh(portfolio)
        return portfolio

    @staticmethod
    async def delete_portfolio(db: AsyncSession, portfolio_id: int) -> bool:
        """Delete a portfolio"""
        result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
        portfolio = result.scalar_one_or_none()
        if not portfolio:
            return False

        await db.delete(portfolio)
        await db.commit()
        return True

    @staticmethod
    async def create_session(
        db: AsyncSession,
        portfolio_id: int,
        user_id: str,
        thread_id: Optional[str] = None
    ) -> SessionModel:
        """Create a new session for a portfolio"""
        if not thread_id:
            thread_id = f"portfolio_{portfolio_id}_{uuid.uuid4()}"

        # Check if session already exists
        result = await db.execute(select(SessionModel).where(SessionModel.id == thread_id))
        existing_session = result.scalar_one_or_none()
        if existing_session:
            # Update last accessed time
            existing_session.last_accessed = datetime.utcnow()
            await db.commit()
            await db.refresh(existing_session)
            return existing_session

        session = SessionModel(
            id=thread_id,
            portfolio_id=portfolio_id,
            user_id=user_id
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session

    @staticmethod
    async def get_session(db: AsyncSession, thread_id: str) -> Optional[SessionModel]:
        """Get session by thread_id (eager-loads .portfolio — callers rely on it being accessible)"""
        result = await db.execute(
            select(SessionModel)
            .where(SessionModel.id == thread_id)
            .options(selectinload(SessionModel.portfolio))
        )
        session = result.scalar_one_or_none()
        if session:
            # Update last accessed time
            session.last_accessed = datetime.utcnow()
            await db.commit()
            await db.refresh(session)
        return session

    @staticmethod
    async def get_session_portfolio(db: AsyncSession, thread_id: str) -> Optional[Portfolio]:
        """Get portfolio associated with a session"""
        result = await db.execute(
            select(SessionModel)
            .where(SessionModel.id == thread_id)
            .options(selectinload(SessionModel.portfolio))
        )
        session = result.scalar_one_or_none()
        return session.portfolio if session else None
