"""Shared test fixtures.

`dbsession` spins up an in-memory SQLite database with the full schema and
points `db.SessionLocal` at it, so code under test that calls `session_scope()`
hits the throwaway database.
"""

import os

os.environ.setdefault("DISCORD_TOKEN", "test")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import shadowdark_bot.db as db
from shadowdark_bot.models import Base


@pytest.fixture
def dbsession():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    previous = db.SessionLocal
    db.SessionLocal = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False
    )
    try:
        yield db.SessionLocal
    finally:
        db.SessionLocal = previous
