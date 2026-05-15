"""Database engine, session factory, and FastAPI dependency."""
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from config import settings

engine = create_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,   # detect stale connections before use
    pool_recycle=3600,    # recycle every hour to avoid PG idle timeout
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session():
    """Context manager for non-FastAPI database sessions.

    Preferred over manual SessionLocal() + try/finally for background tasks,
    services, and scripts. Rolls back automatically on exception.

    Usage:
        with db_session() as db:
            db.query(...)
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
