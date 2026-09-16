from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from quant_platform.core.reconciliation import Reconciler, ReconciliationError
from quant_platform.db.base import Base
from quant_platform.db.models import AssetConfig, ExchangeConfig, Fill, Order, Position
from quant_platform.db.session import create_engine, create_session_factory
from quant_platform.exchange import (
    FakeExchange,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)
from quant_platform.exchange import Fill as ExchangeFill
from quant_platform.exchange import Position as ExchangePosition

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)
BTC = "BTC/USD"
ETH = "ETH/USD"


class SnapshotExchange(FakeExchange):
    def __init__(
        self,
        *,
        positions: tuple[ExchangePosition, ...] = (),
        open_orders: tuple[OrderResult, ...] = (),
        fills: tuple[ExchangeFill, ...] = (),
    ) -> None:
        super().__init__(positions=positions)
        self.open_orders = open_orders
        self.fills_by_order = {
            order_id: tuple(fill for fill in fills if fill.exchange_order_id == order_id)
            for order_id in {fill.exchange_order_id for fill in fills}
        }

    def fetch_open_orders(self, symbol: str | None = None) -> tuple[OrderResult, ...]:
        self._record("fetch_open_orders", symbol)
        return tuple(
            order
            for order in self.open_orders
            if symbol is None or order.symbol == symbol
        )

    def fetch_order_fills(
        self, exchange_order_id: str, symbol: str
    ) -> tuple[ExchangeFill, ...]:
        self._record("fetch_order_fills", exchange_order_id, symbol)
        return self.fills_by_order.get(exchange_order_id, ())


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'reconciliation.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as database_session:
        yield database_session
    engine.dispose()


@pytest.fixture
def configured(session: Session) -> tuple[ExchangeConfig, AssetConfig, AssetConfig]:
    exchange = ExchangeConfig(name="primary", exchange="fake", options={})
    session.add(exchange)
    session.flush()
    btc = AssetConfig(
        exchange_config_id=exchange.id,
        symbol=BTC,
        base_asset="BTC",
        quote_asset="USD",
        settings={},
    )
    eth = AssetConfig(
        exchange_config_id=exchange.id,
        symbol=ETH,
        base_asset="ETH",
        quote_asset="USD",
        settings={},
    )
    session.add_all((btc, eth))
    session.commit()
    return exchange, btc, eth


def remote_order(
    *,
    exchange_order_id: str = "order-1",
    client_order_id: str = "client-1",
    symbol: str = BTC,
    side: OrderSide = OrderSide.BUY,
    quantity: Decimal = Decimal("2"),
) -> OrderResult:
    return OrderResult(
        exchange_order_id=exchange_order_id,
        client_order_id=client_order_id,
        symbol=symbol,
        side=side,
        order_type=OrderType.LIMIT,
        status=OrderStatus.OPEN,
        quantity=quantity,
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        submitted_at=NOW,
    )


def remote_fill(
    *,
    exchange_fill_id: str = "fill-1",
    exchange_order_id: str = "order-1",
    symbol: str = BTC,
    side: OrderSide = OrderSide.BUY,
    quantity: Decimal = Decimal("2"),
    executed_at: datetime = NOW,
) -> ExchangeFill:
    return ExchangeFill(
        exchange_fill_id=exchange_fill_id,
        exchange_order_id=exchange_order_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=Decimal("100"),
        fee=Decimal("0.1"),
        fee_currency="USD",
        executed_at=executed_at,
    )


def test_exchange_position_snapshot_corrects_and_zeros_without_inventing_pnl(
    session: Session, configured: tuple[ExchangeConfig, AssetConfig, AssetConfig]
) -> None:
    exchange_config, btc, eth = configured
    session.add_all(
        (
            Position(
                exchange_config_id=exchange_config.id,
                asset_config_id=btc.id,
                quantity=Decimal("1"),
                average_entry_price=Decimal("90"),
                realized_pnl=Decimal("12.5"),
            ),
            Position(
                exchange_config_id=exchange_config.id,
                asset_config_id=eth.id,
                quantity=Decimal("4"),
                average_entry_price=Decimal("20"),
                realized_pnl=Decimal("-2"),
            ),
        )
    )
    session.commit()
    exchange = FakeExchange(
        positions=(ExchangePosition(symbol=BTC, quantity=Decimal("2"), entry_price=Decimal("100")),)
    )

    report = Reconciler(session, exchange, exchange_config.id, clock=lambda: NOW).reconcile()

    positions = {
        row.asset_config_id: row for row in session.scalars(select(Position)).all()
    }
    assert (positions[btc.id].quantity, positions[btc.id].average_entry_price) == (
        Decimal("2"),
        Decimal("100"),
    )
    assert positions[btc.id].realized_pnl == Decimal("12.5")
    assert (positions[eth.id].quantity, positions[eth.id].average_entry_price) == (
        Decimal("0"),
        Decimal("0"),
    )
    assert positions[eth.id].realized_pnl == Decimal("-2")
    assert report.success is True
    assert report.positions_updated == 2
    assert report.realized_pnl_updated is False
    assert report.new_position_pnl_unavailable_symbols == ()


def test_new_exchange_position_reports_pnl_as_unavailable_not_exchange_zero(
    session: Session, configured: tuple[ExchangeConfig, AssetConfig, AssetConfig]
) -> None:
    exchange_config, btc, _ = configured
    exchange = FakeExchange(
        positions=(ExchangePosition(symbol=BTC, quantity=Decimal("3"), entry_price=Decimal("101")),)
    )

    report = Reconciler(session, exchange, exchange_config.id).reconcile()

    position = session.scalar(select(Position).where(Position.asset_config_id == btc.id))
    assert position is not None
    assert position.realized_pnl == Decimal("0")
    assert report.realized_pnl_updated is False
    assert report.new_position_pnl_unavailable_symbols == (BTC,)


def test_unknown_exchange_position_symbol_rejects_and_rolls_back_snapshot(
    session: Session, configured: tuple[ExchangeConfig, AssetConfig, AssetConfig]
) -> None:
    exchange_config, btc, _ = configured
    local = Position(
        exchange_config_id=exchange_config.id,
        asset_config_id=btc.id,
        quantity=Decimal("1"),
        average_entry_price=Decimal("90"),
        realized_pnl=Decimal("7"),
    )
    session.add(local)
    session.commit()
    exchange = FakeExchange(
        positions=(
            ExchangePosition(symbol=BTC, quantity=Decimal("2"), entry_price=Decimal("100")),
            ExchangePosition(symbol="DOGE/USD", quantity=Decimal("5"), entry_price=Decimal("1")),
        )
    )

    with pytest.raises(ReconciliationError, match="unknown position symbol"):
        Reconciler(session, exchange, exchange_config.id).reconcile()

    session.refresh(local)
    assert (local.quantity, local.average_entry_price, local.realized_pnl) == (
        Decimal("1"),
        Decimal("90"),
        Decimal("7"),
    )


def test_duplicate_exchange_position_symbol_is_rejected(
    session: Session, configured: tuple[ExchangeConfig, AssetConfig, AssetConfig]
) -> None:
    exchange_config, _, _ = configured
    duplicate = ExchangePosition(symbol=BTC, quantity=Decimal("2"), entry_price=Decimal("100"))
    exchange = FakeExchange(positions=(duplicate, duplicate))

    with pytest.raises(ReconciliationError, match="duplicate position symbol"):
        Reconciler(session, exchange, exchange_config.id).reconcile()

    assert session.scalars(select(Position)).all() == []


def test_exchange_only_open_order_is_imported_without_strategy_identity(
    session: Session, configured: tuple[ExchangeConfig, AssetConfig, AssetConfig]
) -> None:
    exchange_config, _, _ = configured
    exchange = FakeExchange(clock=lambda: NOW)
    remote = exchange.submit_order(
        OrderRequest(
            symbol=BTC,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("2"),
            price=Decimal("99"),
            client_order_id="external-client-1",
        )
    )

    report = Reconciler(session, exchange, exchange_config.id).reconcile()

    order = session.scalars(select(Order)).one()
    assert order.exchange_order_id == remote.exchange_order_id
    assert order.client_order_id == "external-client-1"
    assert order.symbol == BTC
    assert order.side == "buy"
    assert order.order_type == "limit"
    assert order.status == "open"
    assert order.quantity == Decimal("2")
    assert order.price is None
    assert order.filled_quantity == Decimal("0")
    assert order.submitted_at is not None
    assert order.submitted_at.replace(tzinfo=UTC) == NOW
    assert order.strategy_run_id is None
    assert order.signal_id is None
    assert report.orders_created == 1


def test_missing_authoritative_fill_is_backfilled_and_completes_order(
    session: Session, configured: tuple[ExchangeConfig, AssetConfig, AssetConfig]
) -> None:
    exchange_config, _, _ = configured
    order_snapshot = remote_order()
    exchange = SnapshotExchange(
        open_orders=(order_snapshot,), fills=(remote_fill(),)
    )

    report = Reconciler(session, exchange, exchange_config.id).reconcile()

    order = session.scalars(select(Order)).one()
    persisted_fill = session.scalars(select(Fill)).one()
    assert persisted_fill.order_id == order.id
    assert persisted_fill.exchange_fill_id == "fill-1"
    assert persisted_fill.quantity == Decimal("2")
    assert persisted_fill.price == Decimal("100")
    assert persisted_fill.fee_amount == Decimal("0.1")
    assert order.filled_quantity == Decimal("2")
    assert order.status == "filled"
    assert report.fills_created == 1


def test_identical_reconciliation_rerun_is_idempotent(
    session: Session, configured: tuple[ExchangeConfig, AssetConfig, AssetConfig]
) -> None:
    exchange_config, _, _ = configured
    exchange = SnapshotExchange(
        open_orders=(remote_order(),), fills=(remote_fill(quantity=Decimal("1")),)
    )
    reconciler = Reconciler(session, exchange, exchange_config.id)
    reconciler.reconcile()

    second = reconciler.reconcile()

    assert len(session.scalars(select(Position)).all()) == 2
    assert len(session.scalars(select(Order)).all()) == 1
    assert len(session.scalars(select(Fill)).all()) == 1
    assert second.positions_updated == 0
    assert second.orders_created == 0
    assert second.orders_updated == 0
    assert second.fills_created == 0


def test_completed_fill_reconciliation_rerun_reports_no_order_update(
    session: Session, configured: tuple[ExchangeConfig, AssetConfig, AssetConfig]
) -> None:
    exchange_config, _, _ = configured
    exchange = SnapshotExchange(
        open_orders=(remote_order(),), fills=(remote_fill(),)
    )
    reconciler = Reconciler(session, exchange, exchange_config.id)
    reconciler.reconcile()

    second = reconciler.reconcile()

    order = session.scalars(select(Order)).one()
    assert order.status == "filled"
    assert order.filled_quantity == Decimal("2")
    assert second.orders_updated == 0
    assert second.fills_created == 0


def test_order_absent_from_open_snapshot_is_unresolved_not_guessed_canceled(
    session: Session, configured: tuple[ExchangeConfig, AssetConfig, AssetConfig]
) -> None:
    exchange_config, _, _ = configured
    local = Order(
        exchange_config_id=exchange_config.id,
        client_order_id="client-1",
        exchange_order_id="order-1",
        symbol=BTC,
        side="buy",
        order_type="limit",
        status="open",
        quantity=Decimal("2"),
        price=Decimal("99"),
        filled_quantity=Decimal("0"),
        submitted_at=NOW,
    )
    session.add(local)
    session.commit()

    report = Reconciler(session, SnapshotExchange(), exchange_config.id).reconcile()

    session.refresh(local)
    assert local.status == "open"
    assert report.unresolved_order_ids == ("order-1",)


def test_malformed_fill_identity_rolls_back_all_reconciliation_changes(
    session: Session, configured: tuple[ExchangeConfig, AssetConfig, AssetConfig]
) -> None:
    exchange_config, btc, _ = configured
    local = Position(
        exchange_config_id=exchange_config.id,
        asset_config_id=btc.id,
        quantity=Decimal("1"),
        average_entry_price=Decimal("90"),
        realized_pnl=Decimal("5"),
    )
    session.add(local)
    session.commit()
    exchange = SnapshotExchange(
        positions=(
            ExchangePosition(
                symbol=BTC, quantity=Decimal("2"), entry_price=Decimal("100")
            ),
        ),
        open_orders=(remote_order(),),
        fills=(remote_fill(symbol=ETH),),
    )

    with pytest.raises(ReconciliationError, match="fill symbol conflict"):
        Reconciler(session, exchange, exchange_config.id).reconcile()

    session.refresh(local)
    assert (local.quantity, local.average_entry_price, local.realized_pnl) == (
        Decimal("1"),
        Decimal("90"),
        Decimal("5"),
    )
    assert session.scalars(select(Order)).all() == []
    assert session.scalars(select(Fill)).all() == []


def test_authoritative_overfill_rejects_and_rolls_back(
    session: Session, configured: tuple[ExchangeConfig, AssetConfig, AssetConfig]
) -> None:
    exchange_config, _, _ = configured
    exchange = SnapshotExchange(
        open_orders=(remote_order(quantity=Decimal("2")),),
        fills=(remote_fill(quantity=Decimal("3")),),
    )

    with pytest.raises(ReconciliationError, match="order overfilled"):
        Reconciler(session, exchange, exchange_config.id).reconcile()

    assert session.scalars(select(Order)).all() == []
    assert session.scalars(select(Fill)).all() == []
    assert session.scalars(select(Position)).all() == []


def test_exchange_failure_during_fill_fetch_rolls_back_all_local_changes(
    session: Session, configured: tuple[ExchangeConfig, AssetConfig, AssetConfig]
) -> None:
    exchange_config, btc, _ = configured
    local = Position(
        exchange_config_id=exchange_config.id,
        asset_config_id=btc.id,
        quantity=Decimal("1"),
        average_entry_price=Decimal("90"),
        realized_pnl=Decimal("4"),
    )
    session.add(local)
    session.commit()
    exchange = SnapshotExchange(
        positions=(
            ExchangePosition(
                symbol=BTC, quantity=Decimal("2"), entry_price=Decimal("100")
            ),
        ),
        open_orders=(remote_order(),),
    )
    exchange.inject_failure("fetch_order_fills", RuntimeError("venue unavailable"))

    with pytest.raises(ReconciliationError, match="exchange reconciliation failed"):
        Reconciler(session, exchange, exchange_config.id).reconcile()

    session.refresh(local)
    assert (local.quantity, local.average_entry_price, local.realized_pnl) == (
        Decimal("1"),
        Decimal("90"),
        Decimal("4"),
    )
    assert session.scalars(select(Order)).all() == []
    session.add(ExchangeConfig(name="recovered", exchange="fake", options={}))
    session.commit()


def test_open_order_missing_required_exchange_identity_is_rejected(
    session: Session, configured: tuple[ExchangeConfig, AssetConfig, AssetConfig]
) -> None:
    exchange_config, _, _ = configured
    malformed = cast(
        OrderResult,
        SimpleNamespace(
            exchange_order_id="",
            client_order_id="client-1",
            symbol=BTC,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            status=OrderStatus.OPEN,
            quantity=Decimal("2"),
            filled_quantity=Decimal("0"),
            average_fill_price=None,
            submitted_at=NOW,
        ),
    )
    exchange = SnapshotExchange(open_orders=(malformed,))

    with pytest.raises(ReconciliationError, match="missing order identity"):
        Reconciler(session, exchange, exchange_config.id).reconcile()

    assert session.scalars(select(Order)).all() == []
    assert session.scalars(select(Position)).all() == []


def test_filled_status_without_full_authoritative_fills_is_rejected(
    session: Session, configured: tuple[ExchangeConfig, AssetConfig, AssetConfig]
) -> None:
    exchange_config, _, _ = configured
    local = Order(
        exchange_config_id=exchange_config.id,
        client_order_id="client-1",
        exchange_order_id="order-1",
        symbol=BTC,
        side="buy",
        order_type="limit",
        status="filled",
        quantity=Decimal("2"),
        price=Decimal("100"),
        filled_quantity=Decimal("2"),
        submitted_at=NOW,
    )
    session.add(local)
    session.commit()

    with pytest.raises(ReconciliationError, match="filled status lacks authoritative fills"):
        Reconciler(session, SnapshotExchange(), exchange_config.id).reconcile()

    session.refresh(local)
    assert local.status == "filled"
    assert local.filled_quantity == Decimal("2")


def test_local_fill_missing_from_authoritative_exchange_history_is_rejected(
    session: Session, configured: tuple[ExchangeConfig, AssetConfig, AssetConfig]
) -> None:
    exchange_config, _, _ = configured
    local_order = Order(
        exchange_config_id=exchange_config.id,
        client_order_id="client-1",
        exchange_order_id="order-1",
        symbol=BTC,
        side="buy",
        order_type="limit",
        status="open",
        quantity=Decimal("2"),
        price=Decimal("99"),
        filled_quantity=Decimal("2"),
        submitted_at=NOW,
    )
    session.add(local_order)
    session.flush()
    session.add(
        Fill(
            order_id=local_order.id,
            exchange_fill_id="local-only-fill",
            quantity=Decimal("2"),
            price=Decimal("100"),
            fee_amount=Decimal("0.1"),
            fee_currency="USD",
            executed_at=NOW,
        )
    )
    session.commit()
    exchange = SnapshotExchange(open_orders=(remote_order(),), fills=())

    with pytest.raises(ReconciliationError, match="local fill missing from exchange"):
        Reconciler(session, exchange, exchange_config.id).reconcile()

    session.refresh(local_order)
    assert local_order.status == "open"
    assert local_order.filled_quantity == Decimal("2")
    assert session.scalars(select(Fill)).one().exchange_fill_id == "local-only-fill"


def test_existing_fill_id_with_changed_execution_time_is_rejected(
    session: Session, configured: tuple[ExchangeConfig, AssetConfig, AssetConfig]
) -> None:
    exchange_config, _, _ = configured
    first_exchange = SnapshotExchange(
        open_orders=(remote_order(),), fills=(remote_fill(),)
    )
    Reconciler(session, first_exchange, exchange_config.id).reconcile()
    changed_exchange = SnapshotExchange(
        open_orders=(remote_order(),),
        fills=(remote_fill(executed_at=NOW + timedelta(seconds=1)),),
    )

    with pytest.raises(ReconciliationError, match="fill identity conflict"):
        Reconciler(session, changed_exchange, exchange_config.id).reconcile()

    assert session.scalars(select(Fill)).one().executed_at.replace(tzinfo=UTC) == NOW
