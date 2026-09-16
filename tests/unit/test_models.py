from sqlalchemy import DateTime, Float, Numeric, inspect

from quant_platform.db.base import Base
from quant_platform.db.models import (  # noqa: F401
    AssetConfig,
    AuditLog,
    Command,
    ExchangeConfig,
    Fill,
    Heartbeat,
    Order,
    Position,
    Signal,
    StrategyRun,
)
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

EXPECTED_UNIQUE_COLUMNS: dict[str, set[tuple[str, ...]]] = {
    "exchange_configs": {("name",)},
    "asset_configs": {("exchange_config_id", "symbol")},
    "strategy_runs": {("idempotency_key",)},
    "signals": {("idempotency_key",)},
    "orders": {("client_order_id",)},
    "fills": {("order_id", "exchange_fill_id")},
    "positions": {("exchange_config_id", "asset_config_id")},
    "commands": {("idempotency_key",)},
    "heartbeats": {("service_name",)},
    "audit_logs": {("dedupe_key",)},
}


def test_core_model_metadata_has_exact_financial_and_idempotency_contracts() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES

    for table in Base.metadata.tables.values():
        for column in table.columns:
            assert not isinstance(column.type, Float)
            if column.name in {
                "quantity",
                "price",
                "filled_quantity",
                "fee_amount",
                "average_entry_price",
                "realized_pnl",
            }:
                assert isinstance(column.type, Numeric)
            if column.name.endswith("_at"):
                assert isinstance(column.type, DateTime)
                assert column.type.timezone is True

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db_inspector = inspect(engine)
    for table_name, expected_constraints in EXPECTED_UNIQUE_COLUMNS.items():
        actual_constraints = {
            tuple(constraint["column_names"])
            for constraint in db_inspector.get_unique_constraints(table_name)
        }
        assert expected_constraints <= actual_constraints
    engine.dispose()
