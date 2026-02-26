"""
Database module for Form 4 transactions.

This module re-exports the Form4Transaction model and database utilities
from the main application database (portfolios.db / DATABASE_URL env var),
replacing the old standalone form4_data.db setup.
"""
import os
import sys

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

from app.database.connection import engine, SessionLocal, get_db  # noqa: E402
from app.database.models import Base, Form4Transaction  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

def init_db():
    """Initialize the database by creating all tables (including form4_transactions)."""
    Base.metadata.create_all(bind=engine)


# Alias for backward compatibility with existing scripts
init = init_db

__all__ = ['Form4Transaction', 'get_db', 'init_db', 'init', 'reset_db', 'get_session']


def reset_db():
    """
    Drops and recreates only the form4_transactions table.
    WARNING: Deletes all Form 4 data!
    """
    from sqlalchemy import inspect

    Form4Transaction.__table__.drop(engine, checkfirst=True)
    Form4Transaction.__table__.create(engine)

    inspector = inspect(engine)
    print(f"Database reset complete. Active tables: {inspector.get_table_names()}")


def get_session() -> Session:
    """Get a new database session."""
    return SessionLocal()
