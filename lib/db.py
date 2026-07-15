"""SQLAlchemy engine + session wiring (SQLite). Replaces Prisma from the spec."""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Default DB lives in ./data/hrms.db (git-ignored).
_DEFAULT = f"sqlite:///{Path(__file__).resolve().parent.parent / 'data' / 'hrms.db'}"
DATABASE_URL = os.getenv("DATABASE_URL", _DEFAULT)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    # Import models so they register on Base.metadata, then create tables.
    from app import models  # noqa: F401

    Path(_DEFAULT.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
