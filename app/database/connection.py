"""
Database connection and session management (async, Postgres)
"""
import os
from contextlib import asynccontextmanager

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from .models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://investment_analyst:investment_analyst@localhost:5432/investment_analyst")


def _to_async_url(url: str) -> str:
    """Translate a plain postgresql:// URL into the asyncpg-driver form SQLAlchemy's async engine needs."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def _to_sync_url(url: str) -> str:
    """Translate to the sync psycopg2-driver form, used only by the create_all() fallback below."""
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return url


# Runtime engine — async, used by every route/service via get_db_session().
engine = create_async_engine(_to_async_url(DATABASE_URL), pool_pre_ping=True)

# Session factory
SessionLocal = async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db():
    """Initialize database tables using Alembic migrations to ensure schema version tracking"""
    from alembic.config import Config
    from alembic import command
    from sqlalchemy import inspect

    alembic_ini_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "alembic.ini")

    # If alembic.ini is missing, fallback to create_all as a safeguard (sync engine, one-off).
    if not os.path.exists(alembic_ini_path):
        print(f"Warning: alembic.ini not found at {alembic_ini_path}. Falling back to metadata.create_all()")
        sync_engine = create_engine(_to_sync_url(DATABASE_URL))
        Base.metadata.create_all(bind=sync_engine)
        sync_engine.dispose()
    else:
        print("Running Alembic migrations to initialize/upgrade database...")
        alembic_cfg = Config(alembic_ini_path)
        # Alembic runs synchronously — always give it the sync (psycopg2) URL,
        # regardless of what driver scheme DATABASE_URL was set to.
        alembic_cfg.set_main_option("sqlalchemy.url", _to_sync_url(DATABASE_URL))

        sync_engine = create_engine(_to_sync_url(DATABASE_URL))
        inspector = inspect(sync_engine)
        tables = inspector.get_table_names()

        if "portfolios" in tables and "alembic_version" not in tables:
            print("Existing database detected without alembic_version. Stamping head...")
            command.stamp(alembic_cfg, "head")
        sync_engine.dispose()

        # Upgrade to head (this sets up tables if empty, or applies pending migrations)
        command.upgrade(alembic_cfg, "head")

    print("Database initialized successfully")


@asynccontextmanager
async def get_db():
    """Get an async database session with context manager semantics."""
    async with SessionLocal() as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise


async def get_db_session() -> AsyncSession:
    """Get an async database session (for FastAPI dependency injection)"""
    async with SessionLocal() as db:
        yield db
