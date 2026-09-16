"""Create the initial trading schema.

Revision ID: 0001_initial
Revises:
"""
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

money = sa.Numeric(30, 12)
timestamp: sa.DateTime = sa.DateTime(timezone=True)


def timestamp_columns() -> tuple[sa.Column[datetime], sa.Column[datetime]]:
    return (
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "exchange_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("exchange", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("sandbox", sa.Boolean(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        *timestamp_columns(),
        sa.UniqueConstraint("name", name="uq_exchange_configs_name"),
    )
    op.create_table(
        "asset_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exchange_config_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("base_asset", sa.String(20), nullable=False),
        sa.Column("quote_asset", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["exchange_config_id"], ["exchange_configs.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "exchange_config_id", "symbol", name="uq_asset_configs_exchange_symbol"
        ),
    )
    op.create_table(
        "strategy_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("asset_config_id", sa.Integer(), nullable=False),
        sa.Column("strategy_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("scheduled_boundary", timestamp, nullable=False),
        sa.Column("started_at", timestamp, nullable=False),
        sa.Column("ended_at", timestamp),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["asset_config_id"], ["asset_configs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("idempotency_key", name="uq_strategy_runs_idempotency_key"),
        sa.UniqueConstraint(
            "asset_config_id",
            "scheduled_boundary",
            name="uq_strategy_runs_asset_boundary",
        ),
    )
    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_run_id", sa.Integer(), nullable=False),
        sa.Column("asset_config_id", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("quantity", money, nullable=False),
        sa.Column("price", money),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("signal_at", timestamp, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["strategy_run_id"], ["strategy_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_config_id"], ["asset_configs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("idempotency_key", name="uq_signals_idempotency_key"),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exchange_config_id", sa.Integer(), nullable=False),
        sa.Column("strategy_run_id", sa.Integer()),
        sa.Column("signal_id", sa.Integer()),
        sa.Column("client_order_id", sa.String(255), nullable=False),
        sa.Column("exchange_order_id", sa.String(255)),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("order_type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("quantity", money, nullable=False),
        sa.Column("price", money),
        sa.Column("filled_quantity", money, nullable=False),
        sa.Column("submitted_at", timestamp),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["exchange_config_id"], ["exchange_configs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["strategy_run_id"], ["strategy_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("client_order_id", name="uq_orders_client_order_id"),
        sa.UniqueConstraint(
            "exchange_config_id", "exchange_order_id", name="uq_orders_exchange_order_id"
        ),
    )
    op.create_table(
        "fills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("exchange_fill_id", sa.String(255), nullable=False),
        sa.Column("quantity", money, nullable=False),
        sa.Column("price", money, nullable=False),
        sa.Column("fee_amount", money, nullable=False),
        sa.Column("fee_currency", sa.String(20)),
        sa.Column("executed_at", timestamp, nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("order_id", "exchange_fill_id", name="uq_fills_order_exchange_fill"),
    )
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exchange_config_id", sa.Integer(), nullable=False),
        sa.Column("asset_config_id", sa.Integer(), nullable=False),
        sa.Column("quantity", money, nullable=False),
        sa.Column("average_entry_price", money, nullable=False),
        sa.Column("realized_pnl", money, nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["exchange_config_id"], ["exchange_configs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["asset_config_id"], ["asset_configs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "exchange_config_id", "asset_config_id", name="uq_positions_exchange_asset"
        ),
    )
    op.create_table(
        "commands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("command_type", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("requested_at", timestamp, nullable=False),
        sa.Column("processed_at", timestamp),
        sa.Column("error", sa.Text()),
        *timestamp_columns(),
        sa.UniqueConstraint("idempotency_key", name="uq_commands_idempotency_key"),
    )
    op.create_table(
        "heartbeats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("last_seen_at", timestamp, nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        *timestamp_columns(),
        sa.UniqueConstraint("service_name", name="uq_heartbeats_service_name"),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(100)),
        sa.Column("entity_id", sa.String(255)),
        sa.Column("actor", sa.String(100)),
        sa.Column("dedupe_key", sa.String(255)),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.UniqueConstraint("dedupe_key", name="uq_audit_logs_dedupe_key"),
    )


def downgrade() -> None:
    for table_name in (
        "audit_logs",
        "heartbeats",
        "commands",
        "positions",
        "fills",
        "orders",
        "signals",
        "strategy_runs",
        "asset_configs",
        "exchange_configs",
    ):
        op.drop_table(table_name)
