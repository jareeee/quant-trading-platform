from decimal import Decimal
from typing import Protocol, runtime_checkable

from quant_platform.exchange.models import (
    Balance,
    Candle,
    Fill,
    MarginMode,
    OrderRequest,
    OrderResult,
    Position,
)


@runtime_checkable
class Exchange(Protocol):
    """Exchange operations used by the trading engine, independent of any SDK."""

    def test_connection(self) -> bool: ...

    def fetch_closed_candles(
        self, symbol: str, timeframe: str, limit: int
    ) -> tuple[Candle, ...]: ...

    def fetch_balance(self) -> tuple[Balance, ...]: ...

    def fetch_positions(self) -> tuple[Position, ...]: ...

    def fetch_open_orders(self, symbol: str | None = None) -> tuple[OrderResult, ...]: ...

    def submit_order(self, request: OrderRequest) -> OrderResult: ...

    def cancel_order(self, exchange_order_id: str, symbol: str) -> OrderResult: ...

    def fetch_order_fills(self, exchange_order_id: str, symbol: str) -> tuple[Fill, ...]: ...

    def set_leverage(self, symbol: str, leverage: Decimal) -> None: ...

    def set_margin_mode(self, symbol: str, margin_mode: MarginMode) -> None: ...
