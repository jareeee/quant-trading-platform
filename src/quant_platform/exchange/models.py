from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(StrEnum):
    OPEN = "open"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


class MarginMode(StrEnum):
    CROSS = "cross"
    ISOLATED = "isolated"


def _require_text(value: str, field: str) -> None:
    if not value.strip():
        raise ValueError(f"{field} must not be empty")


def _require_positive(value: Decimal, field: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be positive")


def _require_nonnegative(value: Decimal, field: str) -> None:
    if not value.is_finite() or value < 0:
        raise ValueError(f"{field} must be nonnegative")


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be UTC-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must use UTC")


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    timeframe: str
    opened_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        _require_text(self.symbol, "symbol")
        _require_text(self.timeframe, "timeframe")
        _require_utc(self.opened_at, "opened_at")
        for field in ("open", "high", "low", "close"):
            _require_positive(getattr(self, field), field)
        _require_nonnegative(self.volume, "volume")
        if self.high < max(self.open, self.low, self.close):
            raise ValueError("high must be at least open, low, and close")
        if self.low > min(self.open, self.high, self.close):
            raise ValueError("low must be at most open, high, and close")


@dataclass(frozen=True, slots=True)
class Balance:
    currency: str
    total: Decimal
    available: Decimal

    def __post_init__(self) -> None:
        _require_text(self.currency, "currency")
        _require_nonnegative(self.total, "total")
        _require_nonnegative(self.available, "available")
        if self.available > self.total:
            raise ValueError("available must not exceed total")


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    quantity: Decimal
    entry_price: Decimal
    leverage: Decimal = Decimal("1")
    margin_mode: MarginMode = MarginMode.CROSS

    def __post_init__(self) -> None:
        _require_text(self.symbol, "symbol")
        if not self.quantity.is_finite():
            raise ValueError("quantity must be finite")
        _require_nonnegative(self.entry_price, "entry_price")
        if self.quantity != 0:
            _require_positive(self.entry_price, "entry_price")
        _require_positive(self.leverage, "leverage")

    @property
    def side(self) -> OrderSide | None:
        if self.quantity > 0:
            return OrderSide.BUY
        if self.quantity < 0:
            return OrderSide.SELL
        return None


@dataclass(frozen=True, slots=True)
class OrderRequest:
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    client_order_id: str
    price: Decimal | None = None

    def __post_init__(self) -> None:
        _require_text(self.symbol, "symbol")
        _require_text(self.client_order_id, "client_order_id")
        _require_positive(self.quantity, "quantity")
        if self.order_type is OrderType.LIMIT and self.price is None:
            raise ValueError("price is required for a limit order")
        if self.price is not None:
            _require_positive(self.price, "price")


@dataclass(frozen=True, slots=True)
class OrderResult:
    exchange_order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    status: OrderStatus
    quantity: Decimal
    filled_quantity: Decimal
    average_fill_price: Decimal | None
    submitted_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.exchange_order_id, "exchange_order_id")
        _require_text(self.client_order_id, "client_order_id")
        _require_text(self.symbol, "symbol")
        _require_positive(self.quantity, "quantity")
        _require_nonnegative(self.filled_quantity, "filled_quantity")
        if self.filled_quantity > self.quantity:
            raise ValueError("filled_quantity must not exceed quantity")
        if self.average_fill_price is not None:
            _require_positive(self.average_fill_price, "average_fill_price")
        if self.filled_quantity > 0 and self.average_fill_price is None:
            raise ValueError("average_fill_price is required for a filled quantity")
        if self.status is OrderStatus.FILLED and self.filled_quantity != self.quantity:
            raise ValueError("a filled order must have its full quantity filled")
        _require_utc(self.submitted_at, "submitted_at")


@dataclass(frozen=True, slots=True)
class Fill:
    exchange_fill_id: str
    exchange_order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    fee: Decimal
    fee_currency: str
    executed_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.exchange_fill_id, "exchange_fill_id")
        _require_text(self.exchange_order_id, "exchange_order_id")
        _require_text(self.symbol, "symbol")
        _require_text(self.fee_currency, "fee_currency")
        _require_positive(self.quantity, "quantity")
        _require_positive(self.price, "price")
        _require_nonnegative(self.fee, "fee")
        _require_utc(self.executed_at, "executed_at")
