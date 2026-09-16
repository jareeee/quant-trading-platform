from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from quant_platform.exchange.models import (
    Balance,
    Candle,
    Fill,
    MarginMode,
    OrderRequest,
    OrderResult,
    OrderStatus,
    OrderType,
    Position,
)


@dataclass(frozen=True, slots=True)
class ExchangeCall:
    method: str
    args: tuple[Any, ...]


class FakeExchange:
    """Deterministic in-memory exchange adapter for tests and local simulation."""

    def __init__(
        self,
        *,
        candles: Mapping[tuple[str, str], Iterable[Candle]] | None = None,
        balances: Iterable[Balance] = (),
        positions: Iterable[Position] = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._candles = {key: tuple(value) for key, value in (candles or {}).items()}
        self._balances = tuple(balances)
        self._positions = tuple(positions)
        self._clock = clock or (lambda: datetime(2000, 1, 1, tzinfo=UTC))
        self._calls: list[ExchangeCall] = []
        self._orders_by_client_id: dict[str, OrderResult] = {}
        self._requests_by_client_id: dict[str, OrderRequest] = {}
        self._orders_by_id: dict[str, OrderResult] = {}
        self._fills_by_order_id: dict[str, tuple[Fill, ...]] = {}
        self._next_order_number = 1
        self._next_fill_number = 1
        self._failures: dict[str, list[Exception]] = {}
        self._leverage_by_symbol: dict[str, Decimal] = {}
        self._margin_mode_by_symbol: dict[str, MarginMode] = {}

    @property
    def calls(self) -> tuple[ExchangeCall, ...]:
        return tuple(self._calls)

    def inject_failure(self, method: str, error: Exception, *, times: int = 1) -> None:
        if times <= 0:
            raise ValueError("times must be positive")
        self._failures.setdefault(method, []).extend([error] * times)

    def _record(self, method: str, *args: object) -> None:
        self._calls.append(ExchangeCall(method, args))
        failures = self._failures.get(method)
        if failures:
            raise failures.pop(0)

    def test_connection(self) -> bool:
        self._record("test_connection")
        return True

    def fetch_closed_candles(
        self, symbol: str, timeframe: str, limit: int
    ) -> tuple[Candle, ...]:
        self._record("fetch_closed_candles", symbol, timeframe, limit)
        if limit <= 0:
            raise ValueError("limit must be positive")
        return self._candles.get((symbol, timeframe), ())[-limit:]

    def fetch_balance(self) -> tuple[Balance, ...]:
        self._record("fetch_balance")
        return self._balances

    def fetch_positions(self) -> tuple[Position, ...]:
        self._record("fetch_positions")
        return self._positions

    def fetch_open_orders(self, symbol: str | None = None) -> tuple[OrderResult, ...]:
        self._record("fetch_open_orders", symbol)
        return tuple(
            order
            for order in self._orders_by_id.values()
            if order.status is OrderStatus.OPEN and (symbol is None or order.symbol == symbol)
        )

    def submit_order(self, request: OrderRequest) -> OrderResult:
        self._record("submit_order", request)
        existing = self._orders_by_client_id.get(request.client_order_id)
        if existing is not None:
            if self._requests_by_client_id[request.client_order_id] != request:
                raise ValueError("client_order_id is already used for a different request")
            return existing
        submitted_at = self._clock()
        order_id = f"fake-order-{self._next_order_number:06d}"
        self._next_order_number += 1
        if request.order_type is OrderType.LIMIT:
            result = OrderResult(
                exchange_order_id=order_id,
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                side=request.side,
                order_type=request.order_type,
                status=OrderStatus.OPEN,
                quantity=request.quantity,
                filled_quantity=Decimal("0"),
                average_fill_price=None,
                submitted_at=submitted_at,
            )
            self._orders_by_client_id[request.client_order_id] = result
            self._requests_by_client_id[request.client_order_id] = request
            self._orders_by_id[order_id] = result
            return result

        symbol_candles = [
            candle
            for (symbol, _timeframe), candles in self._candles.items()
            if symbol == request.symbol
            for candle in candles
        ]
        if not symbol_candles:
            raise ValueError(f"no market price configured for {request.symbol}")
        price = max(symbol_candles, key=lambda candle: candle.opened_at).close
        result = OrderResult(
            exchange_order_id=order_id,
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            status=OrderStatus.FILLED,
            quantity=request.quantity,
            filled_quantity=request.quantity,
            average_fill_price=price,
            submitted_at=submitted_at,
        )
        fill = Fill(
            exchange_fill_id=f"fake-fill-{self._next_fill_number:06d}",
            exchange_order_id=order_id,
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            price=price,
            fee=Decimal("0"),
            fee_currency=request.symbol.rpartition("/")[2] or request.symbol,
            executed_at=submitted_at,
        )
        self._next_fill_number += 1
        self._orders_by_client_id[request.client_order_id] = result
        self._requests_by_client_id[request.client_order_id] = request
        self._orders_by_id[order_id] = result
        self._fills_by_order_id[order_id] = (fill,)
        return result

    def cancel_order(self, exchange_order_id: str, symbol: str) -> OrderResult:
        self._record("cancel_order", exchange_order_id, symbol)
        order = self._orders_by_id[exchange_order_id]
        if order.symbol != symbol:
            raise ValueError("order symbol does not match")
        if order.status is not OrderStatus.OPEN:
            raise ValueError("order is not open")
        canceled = replace(order, status=OrderStatus.CANCELED)
        self._orders_by_id[exchange_order_id] = canceled
        self._orders_by_client_id[order.client_order_id] = canceled
        return canceled

    def fetch_order_fills(self, exchange_order_id: str, symbol: str) -> tuple[Fill, ...]:
        self._record("fetch_order_fills", exchange_order_id, symbol)
        return tuple(
            fill
            for fill in self._fills_by_order_id.get(exchange_order_id, ())
            if fill.symbol == symbol
        )

    def set_leverage(self, symbol: str, leverage: Decimal) -> None:
        self._record("set_leverage", symbol, leverage)
        if not leverage.is_finite() or leverage <= 0:
            raise ValueError("leverage must be positive")
        self._leverage_by_symbol[symbol] = leverage
        self._positions = tuple(
            replace(position, leverage=leverage) if position.symbol == symbol else position
            for position in self._positions
        )

    def set_margin_mode(self, symbol: str, margin_mode: MarginMode) -> None:
        self._record("set_margin_mode", symbol, margin_mode)
        self._margin_mode_by_symbol[symbol] = margin_mode
        self._positions = tuple(
            replace(position, margin_mode=margin_mode)
            if position.symbol == symbol
            else position
            for position in self._positions
        )
