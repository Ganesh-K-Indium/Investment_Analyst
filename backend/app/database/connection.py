"""
Database connection and session management (async, Postgres)
"""
import os
import logging
from contextlib import asynccontextmanager

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from .models import Base
from app.core.config import get_settings

logger = logging.getLogger("app.database.connection")

DATABASE_URL = get_settings().database_url


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
# Pool sized per process (see DB_POOL_* in app/core/config.py): the API and each worker process get their own pool.
engine = create_async_engine(
    _to_async_url(DATABASE_URL),
    pool_pre_ping=True,
    pool_size=get_settings().db_pool_size,
    max_overflow=get_settings().db_max_overflow,
    pool_timeout=get_settings().db_pool_timeout,
    pool_recycle=get_settings().db_pool_recycle,
)

# Session factory
SessionLocal = async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# Any constant shared by every process of this app: the key of the Postgres advisory lock that serialises migrations.
_MIGRATION_LOCK_KEY = 7310524001


def init_db():
    """Bring the database schema up to date (Alembic). The API and the workers all call this when they start, often at the
    same moment; a Postgres advisory lock makes them migrate one at a time, so a fresh database is never created twice
    concurrently (which fails with duplicate-table errors). The first process migrates, the others then find it current."""
    from sqlalchemy import text
    from sqlalchemy.pool import NullPool

    url = _to_sync_url(DATABASE_URL)
    if not url.startswith("postgresql"):
        _init_db_unlocked()
        return
    lock_engine = create_engine(url, poolclass=NullPool)
    try:
        with lock_engine.connect() as conn:
            conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _MIGRATION_LOCK_KEY})
            try:
                _init_db_unlocked()
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _MIGRATION_LOCK_KEY})
    finally:
        lock_engine.dispose()


def _init_db_unlocked():
    """Initialize database tables using Alembic migrations to ensure schema version tracking"""
    from alembic.config import Config
    from alembic import command
    from sqlalchemy import inspect

    alembic_ini_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "alembic.ini")

    # If alembic.ini is missing, fallback to create_all as a safeguard (sync engine, one-off).
    if not os.path.exists(alembic_ini_path):
        logger.warning(f"Warning: alembic.ini not found at {alembic_ini_path}. Falling back to metadata.create_all()")
        sync_engine = create_engine(_to_sync_url(DATABASE_URL))
        Base.metadata.create_all(bind=sync_engine)
        sync_engine.dispose()
    else:
        logger.info("Running Alembic migrations to initialize/upgrade database...")
        alembic_cfg = Config(alembic_ini_path)
        # Alembic runs synchronously — always give it the sync (psycopg2) URL,
        # regardless of what driver scheme DATABASE_URL was set to.
        alembic_cfg.set_main_option("sqlalchemy.url", _to_sync_url(DATABASE_URL))

        sync_engine = create_engine(_to_sync_url(DATABASE_URL))
        inspector = inspect(sync_engine)
        tables = inspector.get_table_names()

        # alembic/env.py calls logging.config.fileConfig(alembic.ini) as part of
        # command.stamp()/command.upgrade() below. alembic.ini's [logger_root]
        # is `level = WARN`, and fileConfig() unconditionally applies that to
        # the real root logger (and swaps in its own handler) — regardless of
        # disable_existing_loggers. Since migrations run in-process here, that
        # silently raises the whole app's effective log level to WARNING for
        # the rest of the process, swallowing every INFO log everywhere
        # (RequestLoggingMiddleware included). Snapshot + restore around the
        # calls so the app's own logging.basicConfig() setup survives.
        root_logger = logging.getLogger()
        saved_level = root_logger.level
        saved_handlers = root_logger.handlers[:]

        try:
            if "portfolios" in tables and "alembic_version" not in tables:
                logger.info("Existing database detected without alembic_version. Stamping head...")
                command.stamp(alembic_cfg, "head")
            sync_engine.dispose()

            # Upgrade to head (this sets up tables if empty, or applies pending migrations)
            command.upgrade(alembic_cfg, "head")
        finally:
            root_logger.setLevel(saved_level)
            root_logger.handlers[:] = saved_handlers

    logger.info("Database initialized successfully")


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
