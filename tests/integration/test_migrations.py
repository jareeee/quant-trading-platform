from pathlib import Path

from alembic.config import Config
from sqlalchemy import inspect

from alembic import command
from quant_platform.db.session import create_engine

EXPECTED_TABLES = {
    "exchange_configs",
    "asset_configs",
    "strategy_runs",
    "signals",
    "orders",
    "fills",
    "positions",
    "commands",
    "heartbeats",
    "audit_logs",
}


def test_initial_migration_upgrades_blank_database_and_downgrades_cleanly(tmp_path: Path) -> None:
    database_path = tmp_path / "migration" / "trading.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path}")
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES | {"alembic_version"}
    engine.dispose()

    command.downgrade(config, "base")

    engine = create_engine(f"sqlite:///{database_path}")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    engine.dispose()
