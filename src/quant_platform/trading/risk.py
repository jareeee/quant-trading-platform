from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from quant_platform.exchange.models import OrderRequest


def _require_decimal(value: Decimal, field: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field} must be a Decimal")


def _require_positive(value: Decimal, field: str) -> None:
    _require_decimal(value, field)
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be positive and finite")


def _require_nonnegative(value: Decimal, field: str) -> None:
    _require_decimal(value, field)
    if not value.is_finite() or value < 0:
        raise ValueError(f"{field} must be nonnegative and finite")


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_notional: Decimal
    max_leverage: Decimal
    max_position_quantity: Decimal
    max_data_age: timedelta
    balance_usage_fraction: Decimal = Decimal("0.95")

    def __post_init__(self) -> None:
        _require_positive(self.max_notional, "max_notional")
        _require_positive(self.max_leverage, "max_leverage")
        _require_positive(self.max_position_quantity, "max_position_quantity")
        if self.max_data_age <= timedelta(0):
            raise ValueError("max_data_age must be positive")
        _require_positive(self.balance_usage_fraction, "balance_usage_fraction")
        if self.balance_usage_fraction > 1:
            raise ValueError("balance_usage_fraction must not exceed 1")


@dataclass(frozen=True, slots=True)
class MarketConstraints:
    minimum_quantity: Decimal
    maximum_quantity: Decimal
    quantity_step: Decimal
    minimum_notional: Decimal

    def __post_init__(self) -> None:
        _require_positive(self.minimum_quantity, "minimum_quantity")
        _require_positive(self.maximum_quantity, "maximum_quantity")
        _require_positive(self.quantity_step, "quantity_step")
        _require_positive(self.minimum_notional, "minimum_notional")
        if self.maximum_quantity < self.minimum_quantity:
            raise ValueError("maximum_quantity must be at least minimum_quantity")


@dataclass(frozen=True, slots=True)
class SizingRequest:
    price: Decimal
    available_balance: Decimal
    leverage: Decimal
    fixed_quantity: Decimal | None = None
    fixed_notional: Decimal | None = None
    balance_fraction: Decimal | None = None
    taker_fee_rate: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        _require_positive(self.price, "price")
        _require_positive(self.available_balance, "available_balance")
        _require_positive(self.leverage, "leverage")
        _require_nonnegative(self.taker_fee_rate, "taker_fee_rate")
        modes = (self.fixed_quantity, self.fixed_notional, self.balance_fraction)
        if sum(value is not None for value in modes) != 1:
            raise ValueError("exactly one sizing mode must be provided")
        if self.fixed_quantity is not None:
            _require_positive(self.fixed_quantity, "fixed_quantity")
        if self.fixed_notional is not None:
            _require_positive(self.fixed_notional, "fixed_notional")
        if self.balance_fraction is not None:
            _require_positive(self.balance_fraction, "balance_fraction")
            if self.balance_fraction > 1:
                raise ValueError("balance_fraction must not exceed 1")


@dataclass(frozen=True, slots=True)
class SizingResult:
    quantity: Decimal
    notional: Decimal
    estimated_fee: Decimal
    required_balance: Decimal


def calculate_position_size(
    request: SizingRequest,
    constraints: MarketConstraints,
    limits: RiskLimits,
) -> SizingResult:
    if request.leverage > limits.max_leverage:
        raise ValueError("above maximum leverage")
    if request.fixed_quantity is not None:
        raw_quantity = request.fixed_quantity
    elif request.balance_fraction is not None:
        usable_fraction = min(request.balance_fraction, limits.balance_usage_fraction)
        budget = request.available_balance * usable_fraction
        cost_per_quantity = request.price * (
            Decimal("1") / request.leverage + request.taker_fee_rate
        )
        raw_quantity = budget / cost_per_quantity
    elif request.fixed_notional is not None:
        raw_quantity = request.fixed_notional / request.price
    else:  # pragma: no cover - SizingRequest enforces a sizing mode
        raise AssertionError("unreachable sizing mode")
    quantity = (raw_quantity // constraints.quantity_step) * constraints.quantity_step
    if quantity < constraints.minimum_quantity:
        raise ValueError("below minimum quantity")
    if quantity > constraints.maximum_quantity:
        raise ValueError("above market maximum quantity")
    if quantity > limits.max_position_quantity:
        raise ValueError("above risk maximum quantity")
    notional = quantity * request.price
    if notional < constraints.minimum_notional:
        raise ValueError("below minimum notional")
    if notional > limits.max_notional:
        raise ValueError("above maximum notional")
    estimated_fee = notional * request.taker_fee_rate
    required_balance = notional / request.leverage + estimated_fee
    usable_balance = request.available_balance * limits.balance_usage_fraction
    if required_balance > usable_balance:
        raise ValueError("insufficient available balance")
    return SizingResult(
        quantity=quantity,
        notional=notional,
        estimated_fee=estimated_fee,
        required_balance=required_balance,
    )


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    reasons: tuple[str, ...]
    sizing_result: SizingResult | None = None


def validate_pre_trade(
    order: OrderRequest,
    *,
    execution_price: Decimal,
    available_balance: Decimal,
    current_exposure: Decimal,
    leverage: Decimal,
    constraints: MarketConstraints,
    limits: RiskLimits,
    market_data_at: datetime,
    now: datetime,
    taker_fee_rate: Decimal = Decimal("0"),
    live_mode: bool = False,
) -> RiskDecision:
    if (
        now.tzinfo is None
        or now.utcoffset() != timedelta(0)
        or market_data_at.tzinfo is None
        or market_data_at.utcoffset() != timedelta(0)
    ):
        return RiskDecision(False, ("timestamps must be UTC-aware",))
    if market_data_at > now:
        return RiskDecision(False, ("market data timestamp is in the future",))
    if now - market_data_at > limits.max_data_age:
        return RiskDecision(False, ("market data is stale",))
    if live_mode and limits.balance_usage_fraction > Decimal("0.95"):
        return RiskDecision(False, ("unsafe live-mode balance usage fraction",))
    try:
        _require_nonnegative(current_exposure, "current_exposure")
        sizing_result = calculate_position_size(
            SizingRequest(
                price=execution_price,
                available_balance=available_balance,
                leverage=leverage,
                fixed_quantity=order.quantity,
                taker_fee_rate=taker_fee_rate,
            ),
            constraints,
            limits,
        )
    except (ArithmeticError, TypeError, ValueError) as exc:
        return RiskDecision(False, (str(exc) or "invalid pre-trade input",))
    if current_exposure + sizing_result.notional > limits.max_notional:
        return RiskDecision(False, ("projected exposure above maximum notional",))
    return RiskDecision(True, (), sizing_result)
