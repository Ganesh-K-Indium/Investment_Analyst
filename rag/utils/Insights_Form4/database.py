"""
Database module for Form 4 transactions.

This module re-exports the Form4Transaction model and the main application's
async engine/session (app.database.connection) — the Form4 pipeline shares
the same async Postgres engine as the rest of the app, not a separate one.
"""
import logging
import os
import sys

logger = logging.getLogger("rag.utils.form4.database")

# Ensure project root is on sys.path so we can import from app.*
_project_root = os.path.dirname(
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
    )
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from app.database.connection import engine, get_db, get_db_session  # noqa: E402
from app.database.models import Base, Form4Transaction  # noqa: E402

__all__ = ['Form4Transaction', 'get_db', 'get_db_session', 'init_db', 'init', 'reset_db']


async def init_db():
    """Initialize the database by creating all tables (including form4_transactions)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# Alias for backward compatibility with existing scripts
init = init_db


async def reset_db():
    """
    Drops and recreates only the form4_transactions table.
    WARNING: Deletes all Form 4 data!
    """
    from sqlalchemy import inspect

    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Form4Transaction.__table__.drop(sync_conn, checkfirst=True))
        await conn.run_sync(lambda sync_conn: Form4Transaction.__table__.create(sync_conn))

        def _list_tables(sync_conn):
            return inspect(sync_conn).get_table_names()

        tables = await conn.run_sync(_list_tables)
        logger.info(f"Database reset complete. Active tables: {tables}")
