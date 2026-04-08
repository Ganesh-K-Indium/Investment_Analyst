"""
Database connection and session management
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from .models import Base
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./portfolios.db")

# Create engine
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Initialize database tables using Alembic migrations to ensure schema version tracking"""
    from alembic.config import Config
    from alembic import command
    from sqlalchemy import inspect
    import os

    alembic_ini_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "alembic.ini")
    
    # If alembic.ini is missing, fallback to create_all as a safeguard
    if not os.path.exists(alembic_ini_path):
        print(f"Warning: alembic.ini not found at {alembic_ini_path}. Falling back to metadata.create_all()")
        Base.metadata.create_all(bind=engine)
    else:
        print("Running Alembic migrations to initialize/upgrade database...")
        alembic_cfg = Config(alembic_ini_path)
        # Ensure our overridden DATABASE_URL is used
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            alembic_cfg.set_main_option("sqlalchemy.url", database_url)
            
        # Check if tables exist but alembic_version is missing
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        
        if "portfolios" in tables and "alembic_version" not in tables:
            print("Existing database detected without alembic_version. Stamping head...")
            command.stamp(alembic_cfg, "head")
        
        # Upgrade to head (this sets up tables if empty, or applies pending migrations)
        command.upgrade(alembic_cfg, "head")
        
    print("Database initialized successfully")


@contextmanager
def get_db():
    """Get database session with context manager"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db_session() -> Session:
    """Get database session (for FastAPI dependency injection)"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
