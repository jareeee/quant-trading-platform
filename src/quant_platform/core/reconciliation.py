from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from quant_platform.db.models import AssetConfig, Order, Position
from quant_platform.db.models import Fill as PersistedFill
from quant_platform.exchange.protocol import Exchange


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


class ReconciliationError(RuntimeError):
    """Raised when authoritative exchange state cannot be reconciled safely."""


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    success: bool
    positions_updated: int
    orders_created: int = 0
    orders_updated: int = 0
    fills_created: int = 0
    unresolved_order_ids: tuple[str, ...] = ()
    unresolved_symbols: tuple[str, ...] = ()
    new_position_pnl_unavailable_symbols: tuple[str, ...] = ()
    realized_pnl_updated: bool = False


class Reconciler:
    def __init__(
        self,
        session: Session,
        exchange: Exchange,
        exchange_config_id: int,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._exchange = exchange
        self._exchange_config_id = exchange_config_id
        self._clock = clock or (lambda: datetime.now(UTC))

    def reconcile(self) -> ReconciliationReport:
        try:
            exchange_positions = self._exchange.fetch_positions()
            open_orders = self._exchange.fetch_open_orders()
            assets = list(
                self._session.scalars(
                    select(AssetConfig).where(
                        AssetConfig.exchange_config_id == self._exchange_config_id
                    )
                )
            )
            by_symbol = {asset.symbol: asset for asset in assets}
            snapshots = {}
            for position in exchange_positions:
                if position.symbol not in by_symbol:
                    raise ReconciliationError(
                        f"unknown position symbol: {position.symbol}"
                    )
                if position.symbol in snapshots:
                    raise ReconciliationError(
                        f"duplicate position symbol: {position.symbol}"
                    )
                snapshots[position.symbol] = position
            updated = 0
            pnl_unavailable: list[str] = []
            for asset in assets:
                snapshot = snapshots.get(asset.symbol)
                quantity = snapshot.quantity if snapshot is not None else Decimal("0")
                entry_price = snapshot.entry_price if snapshot is not None else Decimal("0")
                persisted = self._session.scalar(
                    select(Position).where(
                        Position.exchange_config_id == self._exchange_config_id,
                        Position.asset_config_id == asset.id,
                    )
                )
                if persisted is None:
                    persisted = Position(
                        exchange_config_id=self._exchange_config_id,
                        asset_config_id=asset.id,
                        quantity=quantity,
                        average_entry_price=entry_price,
                    )
                    self._session.add(persisted)
                    updated += 1
                    if snapshot is not None and snapshot.quantity != 0:
                        pnl_unavailable.append(asset.symbol)
                elif (
                    persisted.quantity != quantity
                    or persisted.average_entry_price != entry_price
                ):
                    persisted.quantity = quantity
                    persisted.average_entry_price = entry_price
                    updated += 1
            orders_created = 0
            preexisting_orders = list(
                self._session.scalars(
                    select(Order).where(
                        Order.exchange_config_id == self._exchange_config_id,
                        Order.exchange_order_id.is_not(None),
                    )
                )
            )
            existing_order_state: dict[
                int, tuple[str, Decimal | None, datetime | None, Decimal]
            ] = {
                order.id: (
                    order.status,
                    order.price,
                    _utc_naive(order.submitted_at),
                    order.filled_quantity,
                )
                for order in preexisting_orders
            }
            seen_order_ids: set[str] = set()
            for remote in open_orders:
                identity_text = (
                    remote.exchange_order_id,
                    remote.client_order_id,
                    remote.symbol,
                )
                if any(
                    not isinstance(value, str) or not value.strip()
                    for value in identity_text
                ):
                    raise ReconciliationError("missing order identity")
                if remote.symbol not in by_symbol:
                    raise ReconciliationError(f"unknown order symbol: {remote.symbol}")
                if remote.exchange_order_id in seen_order_ids:
                    raise ReconciliationError(
                        f"duplicate exchange order: {remote.exchange_order_id}"
                    )
                seen_order_ids.add(remote.exchange_order_id)
                persisted_order = self._session.scalar(
                    select(Order).where(
                        Order.exchange_config_id == self._exchange_config_id,
                        Order.exchange_order_id == remote.exchange_order_id,
                    )
                )
                if persisted_order is None:
                    client_collision = self._session.scalar(
                        select(Order).where(Order.client_order_id == remote.client_order_id)
                    )
                    if client_collision is not None:
                        raise ReconciliationError(
                            f"client order identity conflict: {remote.client_order_id}"
                        )
                    persisted_order = Order(
                        exchange_config_id=self._exchange_config_id,
                        strategy_run_id=None,
                        signal_id=None,
                        client_order_id=remote.client_order_id,
                        exchange_order_id=remote.exchange_order_id,
                        symbol=remote.symbol,
                        side=remote.side.value,
                        order_type=remote.order_type.value,
                        status=remote.status.value,
                        quantity=remote.quantity,
                        price=remote.average_fill_price,
                        filled_quantity=Decimal("0"),
                        submitted_at=remote.submitted_at,
                    )
                    self._session.add(persisted_order)
                    orders_created += 1
                else:
                    identity = (
                        persisted_order.client_order_id,
                        persisted_order.symbol,
                        persisted_order.side,
                        persisted_order.order_type,
                        persisted_order.quantity,
                    )
                    remote_identity = (
                        remote.client_order_id,
                        remote.symbol,
                        remote.side.value,
                        remote.order_type.value,
                        remote.quantity,
                    )
                    if identity != remote_identity:
                        raise ReconciliationError(
                            f"order identity conflict: {remote.exchange_order_id}"
                        )
                    persisted_order.status = remote.status.value
                    persisted_order.price = remote.average_fill_price
                    persisted_order.submitted_at = remote.submitted_at
            self._session.flush()
            known_orders = list(
                self._session.scalars(
                    select(Order).where(
                        Order.exchange_config_id == self._exchange_config_id,
                        Order.exchange_order_id.is_not(None),
                    )
                )
            )
            fills_created = 0
            for known_order in known_orders:
                assert known_order.exchange_order_id is not None
                remote_fills = self._exchange.fetch_order_fills(
                    known_order.exchange_order_id, known_order.symbol
                )
                seen_fill_ids: set[str] = set()
                for remote_fill in remote_fills:
                    if remote_fill.exchange_fill_id in seen_fill_ids:
                        raise ReconciliationError(
                            f"duplicate exchange fill: {remote_fill.exchange_fill_id}"
                        )
                    seen_fill_ids.add(remote_fill.exchange_fill_id)
                    if remote_fill.exchange_order_id != known_order.exchange_order_id:
                        raise ReconciliationError(
                            f"fill order identity conflict: {remote_fill.exchange_fill_id}"
                        )
                    if remote_fill.symbol != known_order.symbol:
                        raise ReconciliationError(
                            f"fill symbol conflict: {remote_fill.exchange_fill_id}"
                        )
                    if remote_fill.side.value != known_order.side:
                        raise ReconciliationError(
                            f"fill side conflict: {remote_fill.exchange_fill_id}"
                        )
                    existing_fill = self._session.scalar(
                        select(PersistedFill).where(
                            PersistedFill.order_id == known_order.id,
                            PersistedFill.exchange_fill_id
                            == remote_fill.exchange_fill_id,
                        )
                    )
                    if existing_fill is None:
                        self._session.add(
                            PersistedFill(
                                order_id=known_order.id,
                                exchange_fill_id=remote_fill.exchange_fill_id,
                                quantity=remote_fill.quantity,
                                price=remote_fill.price,
                                fee_amount=remote_fill.fee,
                                fee_currency=remote_fill.fee_currency,
                                executed_at=remote_fill.executed_at,
                            )
                        )
                        fills_created += 1
                    elif (
                        existing_fill.quantity != remote_fill.quantity
                        or existing_fill.price != remote_fill.price
                        or existing_fill.fee_amount != remote_fill.fee
                        or existing_fill.fee_currency != remote_fill.fee_currency
                        or _utc_naive(existing_fill.executed_at)
                        != _utc_naive(remote_fill.executed_at)
                    ):
                        raise ReconciliationError(
                            f"fill identity conflict: {remote_fill.exchange_fill_id}"
                        )
                self._session.flush()
                persisted_fills = list(
                    self._session.scalars(
                        select(PersistedFill).where(
                            PersistedFill.order_id == known_order.id
                        )
                    )
                )
                local_only_fill_ids = sorted(
                    fill.exchange_fill_id
                    for fill in persisted_fills
                    if fill.exchange_fill_id not in seen_fill_ids
                )
                if local_only_fill_ids:
                    raise ReconciliationError(
                        "local fill missing from exchange: "
                        + ", ".join(local_only_fill_ids)
                    )
                cumulative = sum(
                    (fill.quantity for fill in persisted_fills), Decimal("0")
                )
                if cumulative > known_order.quantity:
                    raise ReconciliationError(
                        f"order overfilled: {known_order.exchange_order_id}"
                    )
                if known_order.status == "filled" and cumulative != known_order.quantity:
                    raise ReconciliationError(
                        "filled status lacks authoritative fills: "
                        f"{known_order.exchange_order_id}"
                    )
                known_order.filled_quantity = cumulative
                if cumulative == known_order.quantity:
                    known_order.status = "filled"
            orders_updated = sum(
                1
                for order in known_orders
                if order.id in existing_order_state
                and existing_order_state[order.id]
                != (
                    order.status,
                    order.price,
                    _utc_naive(order.submitted_at),
                    order.filled_quantity,
                )
            )
            unresolved_order_ids = tuple(
                sorted(
                    order.exchange_order_id
                    for order in known_orders
                    if order.exchange_order_id not in seen_order_ids
                    and order.status != "filled"
                    and order.exchange_order_id is not None
                )
            )
            unresolved_symbols = tuple(
                sorted(
                    {
                        order.symbol
                        for order in known_orders
                        if order.exchange_order_id in unresolved_order_ids
                    }
                )
            )
            self._session.commit()
            return ReconciliationReport(
                success=True,
                positions_updated=updated,
                orders_created=orders_created,
                orders_updated=orders_updated,
                fills_created=fills_created,
                unresolved_order_ids=unresolved_order_ids,
                unresolved_symbols=unresolved_symbols,
                new_position_pnl_unavailable_symbols=tuple(pnl_unavailable),
            )
        except Exception as error:
            self._session.rollback()
            if isinstance(error, ReconciliationError):
                raise
            raise ReconciliationError("exchange reconciliation failed") from error
