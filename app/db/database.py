"""Database engine and session management."""
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

_settings = get_settings()

_connect_args: dict = {}
if _settings.database_url.startswith("sqlite"):
    _connect_args["check_same_thread"] = False
    _db_path = _settings.database_url.replace("sqlite:///", "", 1)
    if _db_path and _db_path != ":memory:":
        Path(_db_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(_settings.database_url, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.db import models  # noqa: F401  (registers tables on Base.metadata)

    Base.metadata.create_all(bind=engine)
