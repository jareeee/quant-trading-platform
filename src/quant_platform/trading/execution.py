from datetime import datetime, timedelta
from decimal import Decimal

from quant_platform.exchange import OrderRequest
from quant_platform.trading.risk import MarketConstraints, RiskDecision, RiskLimits


def validate_close_order(
    order: OrderRequest,
    *,
    execution_price: Decimal,
    leverage: Decimal,
    constraints: MarketConstraints,
    limits: RiskLimits,
    market_data_at: datetime,
    now: datetime,
) -> RiskDecision:
    """Validate risk invariants for a reduce-to-zero order.

    Closing deliberately ignores entry-only balance, exposure, maximum-notional, and
    maximum-position rules: rejecting risk reduction because the existing position is
    already above an entry limit would trap exposure. Exchange quantity/notional
    constraints, data freshness, and maximum leverage remain fail-closed.
    """
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
    if not execution_price.is_finite() or execution_price <= 0:
        return RiskDecision(False, ("execution_price must be positive and finite",))
    if not leverage.is_finite() or leverage <= 0:
        return RiskDecision(False, ("leverage must be positive and finite",))
    if leverage > limits.max_leverage:
        return RiskDecision(False, ("above maximum leverage",))
    if order.quantity < constraints.minimum_quantity:
        return RiskDecision(False, ("below minimum quantity",))
    if order.quantity > constraints.maximum_quantity:
        return RiskDecision(False, ("above market maximum quantity",))
    if order.quantity % constraints.quantity_step != 0:
        return RiskDecision(False, ("quantity does not align with market step",))
    if order.quantity * execution_price < constraints.minimum_notional:
        return RiskDecision(False, ("below minimum notional",))
    return RiskDecision(True, ())
