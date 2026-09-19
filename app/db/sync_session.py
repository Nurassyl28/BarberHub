"""Sync engine for Celery workers, which cannot drive the async session.

Same models, same database — only the driver and session style differ.
"""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

sync_engine = create_engine(
    settings.sync_database_url,
    echo=settings.DB_ECHO,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SyncSessionFactory = sessionmaker(bind=sync_engine, expire_on_commit=False, autoflush=False)


@contextmanager
def sync_session() -> Generator[Session]:
    session = SyncSessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
