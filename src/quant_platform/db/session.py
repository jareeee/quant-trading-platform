import sqlite3
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import Engine, event
from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def _ensure_parent_directory(database_url: str) -> None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        raise ValueError("only SQLite database URLs are supported")
    if url.database and url.database != ":memory:":
        Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def _configure_sqlite_connection(
    dbapi_connection: sqlite3.Connection, connection_record: object
) -> None:
    del connection_record
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def create_engine(database_url: str) -> Engine:
    """Create a SQLite engine whose connections use safe concurrency settings."""
    _ensure_parent_directory(database_url)
    engine = sqlalchemy_create_engine(database_url)
    event.listen(engine, "connect", _configure_sqlite_connection)
    return engine


def create_session_factory(engine: Engine) -> Callable[[], Session]:
    """Create sessions with explicit transaction state retained after commits."""
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
