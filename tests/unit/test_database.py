from pathlib import Path

from pytest import MonkeyPatch
from sqlalchemy import text

from quant_platform.db.session import create_engine, create_session_factory


def test_file_database_creates_parent_and_enforces_sqlite_pragmas(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "trading.db"

    engine = create_engine(f"sqlite:///{database_path}")

    assert database_path.parent.is_dir()
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("PRAGMA busy_timeout")).scalar_one() == 5000
    engine.dispose()


def test_memory_database_does_not_create_a_memory_directory(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    engine = create_engine("sqlite:///:memory:")

    assert not (tmp_path / ":memory:").exists()
    engine.dispose()


def test_session_factory_uses_sqlalchemy_two_style_session() -> None:
    engine = create_engine("sqlite:///:memory:")
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        assert session.execute(text("SELECT 1")).scalar_one() == 1
        assert session.expire_on_commit is False

    engine.dispose()
