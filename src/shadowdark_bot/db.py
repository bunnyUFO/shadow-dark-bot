import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from shadowdark_bot.config import settings


def _sqlite_path_from_url(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    raw = url[len("sqlite:///") :]
    return Path(raw)


def ensure_sqlite_dir() -> None:
    """Create the parent directory of the SQLite file if needed. No-op for non-SQLite URLs."""
    path = _sqlite_path_from_url(settings.DATABASE_URL)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)


ensure_sqlite_dir()

engine = create_engine(
    settings.DATABASE_URL,
    future=True,
    connect_args=(
        {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
    ),
)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record) -> None:
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commits on clean exit, rolls back on exception, always closes."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
