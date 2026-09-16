from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from quant_platform.db.base import Base

MONEY = Numeric(30, 12)


def utc_now() -> datetime:
    """Return an aware UTC timestamp for ORM defaults."""
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class ExchangeConfig(TimestampMixin, Base):
    __tablename__ = "exchange_configs"
    __table_args__ = (UniqueConstraint("name", name="uq_exchange_configs_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    exchange: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sandbox: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class AssetConfig(TimestampMixin, Base):
    __tablename__ = "asset_configs"
    __table_args__ = (
        UniqueConstraint(
            "exchange_config_id", "symbol", name="uq_asset_configs_exchange_symbol"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange_config_id: Mapped[int] = mapped_column(
        ForeignKey("exchange_configs.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    base_asset: Mapped[str] = mapped_column(String(20), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class StrategyRun(TimestampMixin, Base):
    __tablename__ = "strategy_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_strategy_runs_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("asset_configs.id", ondelete="SET NULL")
    )
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Signal(TimestampMixin, Base):
    __tablename__ = "signals"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_signals_idempotency_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_run_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_runs.id", ondelete="CASCADE"), nullable=False
    )
    asset_config_id: Mapped[int] = mapped_column(
        ForeignKey("asset_configs.id", ondelete="RESTRICT"), nullable=False
    )
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    price: Mapped[Decimal | None] = mapped_column(MONEY)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    signal_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("client_order_id", name="uq_orders_client_order_id"),
        UniqueConstraint(
            "exchange_config_id", "exchange_order_id", name="uq_orders_exchange_order_id"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange_config_id: Mapped[int] = mapped_column(
        ForeignKey("exchange_configs.id", ondelete="RESTRICT"), nullable=False
    )
    strategy_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_runs.id", ondelete="SET NULL")
    )
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id", ondelete="SET NULL"))
    client_order_id: Mapped[str] = mapped_column(String(255), nullable=False)
    exchange_order_id: Mapped[str | None] = mapped_column(String(255))
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    order_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    price: Mapped[Decimal | None] = mapped_column(MONEY)
    filled_quantity: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Fill(TimestampMixin, Base):
    __tablename__ = "fills"
    __table_args__ = (
        UniqueConstraint("order_id", "exchange_fill_id", name="uq_fills_order_exchange_fill"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    exchange_fill_id: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    fee_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    fee_currency: Mapped[str | None] = mapped_column(String(20))
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Position(TimestampMixin, Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint(
            "exchange_config_id", "asset_config_id", name="uq_positions_exchange_asset"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange_config_id: Mapped[int] = mapped_column(
        ForeignKey("exchange_configs.id", ondelete="CASCADE"), nullable=False
    )
    asset_config_id: Mapped[int] = mapped_column(
        ForeignKey("asset_configs.id", ondelete="CASCADE"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    average_entry_price: Mapped[Decimal] = mapped_column(
        MONEY, default=Decimal("0"), nullable=False
    )
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)


class Command(TimestampMixin, Base):
    __tablename__ = "commands"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_commands_idempotency_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    command_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class Heartbeat(TimestampMixin, Base):
    __tablename__ = "heartbeats"
    __table_args__ = (UniqueConstraint("service_name", name="uq_heartbeats_service_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_audit_logs_dedupe_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(100))
    entity_id: Mapped[str | None] = mapped_column(String(255))
    actor: Mapped[str | None] = mapped_column(String(100))
    dedupe_key: Mapped[str | None] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
