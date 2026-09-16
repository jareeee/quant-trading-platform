from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quant_platform.exchange.models import OrderRequest, OrderSide, OrderType
from quant_platform.trading.risk import (
    MarketConstraints,
    RiskDecision,
    RiskLimits,
    SizingRequest,
    calculate_position_size,
    validate_pre_trade,
)


def test_risk_limits_are_immutable_and_default_to_safe_balance_usage() -> None:
    limits = RiskLimits(
        max_notional=Decimal("10000"),
        max_leverage=Decimal("3"),
        max_position_quantity=Decimal("100"),
        max_data_age=timedelta(seconds=30),
    )

    assert limits.balance_usage_fraction == Decimal("0.95")
    with pytest.raises(FrozenInstanceError):
        limits.max_notional = Decimal("20000")  # type: ignore[misc]


def test_domain_models_reject_invalid_values_and_ambiguous_sizing_modes() -> None:
    with pytest.raises(ValueError, match="max_notional must be positive"):
        RiskLimits(
            max_notional=Decimal("NaN"),
            max_leverage=Decimal("3"),
            max_position_quantity=Decimal("100"),
            max_data_age=timedelta(seconds=30),
        )
    with pytest.raises(ValueError, match="maximum_quantity"):
        MarketConstraints(
            minimum_quantity=Decimal("1"),
            maximum_quantity=Decimal("0.5"),
            quantity_step=Decimal("0.1"),
            minimum_notional=Decimal("10"),
        )
    with pytest.raises(ValueError, match="exactly one sizing mode"):
        SizingRequest(
            price=Decimal("10"),
            available_balance=Decimal("100"),
            leverage=Decimal("1"),
            fixed_quantity=Decimal("1"),
            fixed_notional=Decimal("10"),
        )


def test_fixed_quantity_is_floored_to_step_without_rounding_up() -> None:
    result = calculate_position_size(
        SizingRequest(
            price=Decimal("10"),
            available_balance=Decimal("100"),
            leverage=Decimal("1"),
            fixed_quantity=Decimal("1.29"),
        ),
        MarketConstraints(
            minimum_quantity=Decimal("0.1"),
            maximum_quantity=Decimal("20"),
            quantity_step=Decimal("0.1"),
            minimum_notional=Decimal("1"),
        ),
        RiskLimits(
            max_notional=Decimal("1000"),
            max_leverage=Decimal("3"),
            max_position_quantity=Decimal("10"),
            max_data_age=timedelta(seconds=30),
        ),
    )

    assert result.quantity == Decimal("1.2")
    assert result.notional == Decimal("12.0")
    assert result.estimated_fee == Decimal("0.0")
    assert result.required_balance == Decimal("12.0")


def test_full_balance_request_reserves_fees_and_obeys_default_safety_fraction() -> None:
    request = SizingRequest(
        price=Decimal("1"),
        available_balance=Decimal("100"),
        leverage=Decimal("1"),
        balance_fraction=Decimal("1"),
        taker_fee_rate=Decimal("0.01"),
    )
    result = calculate_position_size(
        request,
        MarketConstraints(
            minimum_quantity=Decimal("0.01"),
            maximum_quantity=Decimal("1000"),
            quantity_step=Decimal("0.01"),
            minimum_notional=Decimal("1"),
        ),
        RiskLimits(
            max_notional=Decimal("1000"),
            max_leverage=Decimal("3"),
            max_position_quantity=Decimal("1000"),
            max_data_age=timedelta(seconds=30),
        ),
    )

    assert result.quantity == Decimal("94.05")
    assert result.required_balance == Decimal("94.9905")
    assert result.required_balance <= request.available_balance * Decimal("0.95")


def test_fixed_notional_is_converted_to_quantity_then_floored() -> None:
    result = calculate_position_size(
        SizingRequest(
            price=Decimal("3"),
            available_balance=Decimal("100"),
            leverage=Decimal("2"),
            fixed_notional=Decimal("10"),
        ),
        MarketConstraints(
            minimum_quantity=Decimal("0.1"),
            maximum_quantity=Decimal("100"),
            quantity_step=Decimal("0.1"),
            minimum_notional=Decimal("1"),
        ),
        RiskLimits(
            max_notional=Decimal("100"),
            max_leverage=Decimal("2"),
            max_position_quantity=Decimal("100"),
            max_data_age=timedelta(seconds=30),
        ),
    )

    assert result.quantity == Decimal("3.3")
    assert result.notional == Decimal("9.9")


@pytest.mark.parametrize(
    ("sizing_request", "constraints", "limits", "message"),
    [
        (
            SizingRequest(
                Decimal("10"),
                Decimal("100"),
                Decimal("1"),
                fixed_quantity=Decimal("0.09"),
            ),
            MarketConstraints(Decimal("0.1"), Decimal("20"), Decimal("0.01"), Decimal("0.1")),
            RiskLimits(Decimal("1000"), Decimal("3"), Decimal("20"), timedelta(seconds=30)),
            "below minimum quantity",
        ),
        (
            SizingRequest(Decimal("5"), Decimal("100"), Decimal("1"), fixed_quantity=Decimal("1")),
            MarketConstraints(Decimal("0.1"), Decimal("20"), Decimal("0.1"), Decimal("10")),
            RiskLimits(Decimal("1000"), Decimal("3"), Decimal("20"), timedelta(seconds=30)),
            "below minimum notional",
        ),
        (
            SizingRequest(Decimal("1"), Decimal("100"), Decimal("1"), fixed_quantity=Decimal("11")),
            MarketConstraints(Decimal("0.1"), Decimal("10"), Decimal("0.1"), Decimal("1")),
            RiskLimits(Decimal("1000"), Decimal("3"), Decimal("20"), timedelta(seconds=30)),
            "above market maximum quantity",
        ),
        (
            SizingRequest(Decimal("1"), Decimal("100"), Decimal("1"), fixed_quantity=Decimal("9")),
            MarketConstraints(Decimal("0.1"), Decimal("10"), Decimal("0.1"), Decimal("1")),
            RiskLimits(Decimal("1000"), Decimal("3"), Decimal("8"), timedelta(seconds=30)),
            "above risk maximum quantity",
        ),
        (
            SizingRequest(
                Decimal("10"),
                Decimal("100"),
                Decimal("2"),
                fixed_quantity=Decimal("11"),
            ),
            MarketConstraints(Decimal("0.1"), Decimal("20"), Decimal("0.1"), Decimal("1")),
            RiskLimits(Decimal("100"), Decimal("3"), Decimal("20"), timedelta(seconds=30)),
            "above maximum notional",
        ),
        (
            SizingRequest(Decimal("10"), Decimal("100"), Decimal("4"), fixed_quantity=Decimal("1")),
            MarketConstraints(Decimal("0.1"), Decimal("20"), Decimal("0.1"), Decimal("1")),
            RiskLimits(Decimal("1000"), Decimal("3"), Decimal("20"), timedelta(seconds=30)),
            "above maximum leverage",
        ),
        (
            SizingRequest(
                Decimal("10"),
                Decimal("100"),
                Decimal("1"),
                fixed_quantity=Decimal("10"),
                taker_fee_rate=Decimal("0.01"),
            ),
            MarketConstraints(Decimal("0.1"), Decimal("20"), Decimal("0.1"), Decimal("1")),
            RiskLimits(Decimal("1000"), Decimal("3"), Decimal("20"), timedelta(seconds=30)),
            "insufficient available balance",
        ),
    ],
)
def test_sizing_rejects_exchange_and_risk_boundaries(
    sizing_request: SizingRequest,
    constraints: MarketConstraints,
    limits: RiskLimits,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        calculate_position_size(sizing_request, constraints, limits)


def test_pre_trade_rejects_stale_market_data_fail_closed() -> None:
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    decision = validate_pre_trade(
        OrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1"),
            client_order_id="risk-1",
        ),
        execution_price=Decimal("10"),
        available_balance=Decimal("100"),
        current_exposure=Decimal("0"),
        leverage=Decimal("1"),
        constraints=MarketConstraints(
            Decimal("0.1"), Decimal("20"), Decimal("0.1"), Decimal("1")
        ),
        limits=RiskLimits(
            Decimal("1000"), Decimal("3"), Decimal("20"), timedelta(seconds=30)
        ),
        market_data_at=now - timedelta(seconds=31),
        now=now,
    )

    assert decision.approved is False
    assert decision.reasons == ("market data is stale",)
    assert decision.sizing_result is None


def test_pre_trade_rejects_unsafe_live_balance_configuration() -> None:
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    decision = validate_pre_trade(
        OrderRequest("BTC/USDT", OrderSide.BUY, OrderType.MARKET, Decimal("1"), "risk-2"),
        execution_price=Decimal("10"),
        available_balance=Decimal("100"),
        current_exposure=Decimal("0"),
        leverage=Decimal("1"),
        constraints=MarketConstraints(
            Decimal("0.1"), Decimal("20"), Decimal("0.1"), Decimal("1")
        ),
        limits=RiskLimits(
            Decimal("1000"),
            Decimal("3"),
            Decimal("20"),
            timedelta(seconds=30),
            balance_usage_fraction=Decimal("1"),
        ),
        market_data_at=now,
        now=now,
        live_mode=True,
    )

    assert decision == RiskDecision(False, ("unsafe live-mode balance usage fraction",))


def test_pre_trade_approves_valid_order_with_exact_sizing_result() -> None:
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    decision = validate_pre_trade(
        OrderRequest("BTC/USDT", OrderSide.BUY, OrderType.MARKET, Decimal("1.29"), "risk-3"),
        execution_price=Decimal("10"),
        available_balance=Decimal("100"),
        current_exposure=Decimal("10"),
        leverage=Decimal("2"),
        constraints=MarketConstraints(
            Decimal("0.1"), Decimal("20"), Decimal("0.1"), Decimal("1")
        ),
        limits=RiskLimits(
            Decimal("100"), Decimal("3"), Decimal("20"), timedelta(seconds=30)
        ),
        market_data_at=now - timedelta(seconds=30),
        now=now,
        taker_fee_rate=Decimal("0.01"),
    )

    assert decision.approved is True
    assert decision.reasons == ()
    assert decision.sizing_result is not None
    assert decision.sizing_result.quantity == Decimal("1.2")


def test_pre_trade_rejects_projected_exposure_above_notional_limit() -> None:
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    decision = validate_pre_trade(
        OrderRequest("BTC/USDT", OrderSide.BUY, OrderType.MARKET, Decimal("1"), "risk-4"),
        execution_price=Decimal("10"),
        available_balance=Decimal("100"),
        current_exposure=Decimal("95"),
        leverage=Decimal("2"),
        constraints=MarketConstraints(
            Decimal("0.1"), Decimal("20"), Decimal("0.1"), Decimal("1")
        ),
        limits=RiskLimits(
            Decimal("100"), Decimal("3"), Decimal("20"), timedelta(seconds=30)
        ),
        market_data_at=now,
        now=now,
    )

    assert decision == RiskDecision(False, ("projected exposure above maximum notional",))


def test_pre_trade_rejects_invalid_market_timestamps_instead_of_raising() -> None:
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    decision = validate_pre_trade(
        OrderRequest("BTC/USDT", OrderSide.BUY, OrderType.MARKET, Decimal("1"), "risk-5"),
        execution_price=Decimal("10"),
        available_balance=Decimal("100"),
        current_exposure=Decimal("0"),
        leverage=Decimal("1"),
        constraints=MarketConstraints(
            Decimal("0.1"), Decimal("20"), Decimal("0.1"), Decimal("1")
        ),
        limits=RiskLimits(
            Decimal("100"), Decimal("3"), Decimal("20"), timedelta(seconds=30)
        ),
        market_data_at=now.replace(tzinfo=None),
        now=now,
    )

    assert decision == RiskDecision(False, ("timestamps must be UTC-aware",))

    future_decision = validate_pre_trade(
        OrderRequest("BTC/USDT", OrderSide.BUY, OrderType.MARKET, Decimal("1"), "risk-6"),
        execution_price=Decimal("10"),
        available_balance=Decimal("100"),
        current_exposure=Decimal("0"),
        leverage=Decimal("1"),
        constraints=MarketConstraints(
            Decimal("0.1"), Decimal("20"), Decimal("0.1"), Decimal("1")
        ),
        limits=RiskLimits(
            Decimal("100"), Decimal("3"), Decimal("20"), timedelta(seconds=30)
        ),
        market_data_at=now + timedelta(microseconds=1),
        now=now,
    )

    assert future_decision == RiskDecision(False, ("market data timestamp is in the future",))


def test_trading_package_exports_risk_domain_api() -> None:
    import quant_platform.trading as trading

    assert trading.MarketConstraints is MarketConstraints
    assert trading.RiskDecision is RiskDecision
    assert trading.RiskLimits is RiskLimits
    assert trading.SizingRequest is SizingRequest
    assert trading.calculate_position_size is calculate_position_size
    assert trading.validate_pre_trade is validate_pre_trade
