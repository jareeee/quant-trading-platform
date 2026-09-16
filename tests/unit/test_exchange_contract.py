from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from quant_platform.exchange import (
    Balance,
    Candle,
    Exchange,
    Fill,
    MarginMode,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)

NOW = datetime(2025, 1, 2, 3, 4, tzinfo=UTC)


def test_exchange_models_are_immutable_and_use_decimal_values() -> None:
    candle = Candle(
        symbol="BTC/USDT",
        timeframe="1m",
        opened_at=NOW,
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("12.5"),
    )
    balance = Balance(currency="USDT", total=Decimal("1000"), available=Decimal("800"))
    position = Position(
        symbol="BTC/USDT",
        quantity=Decimal("2"),
        entry_price=Decimal("95"),
        leverage=Decimal("2"),
        margin_mode=MarginMode.ISOLATED,
    )

    assert candle.close == Decimal("105")
    assert balance.available == Decimal("800")
    assert position.side is OrderSide.BUY
    with pytest.raises(FrozenInstanceError):
        candle.close = Decimal("1")  # type: ignore[misc]


def test_order_models_capture_request_result_and_fill() -> None:
    request = OrderRequest(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("2"),
        client_order_id="strategy-1",
        price=Decimal("101"),
    )
    result = OrderResult(
        exchange_order_id="order-1",
        client_order_id=request.client_order_id,
        symbol=request.symbol,
        side=request.side,
        order_type=request.order_type,
        status=OrderStatus.OPEN,
        quantity=request.quantity,
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        submitted_at=NOW,
    )
    fill = Fill(
        exchange_fill_id="fill-1",
        exchange_order_id=result.exchange_order_id,
        symbol=result.symbol,
        side=result.side,
        quantity=Decimal("2"),
        price=Decimal("100.50"),
        fee=Decimal("0.01"),
        fee_currency="USDT",
        executed_at=NOW,
    )

    assert request.price == Decimal("101")
    assert result.status is OrderStatus.OPEN
    assert fill.price == Decimal("100.50")


@pytest.mark.parametrize(
    ("factory", "match"),
    [
        (
            lambda: Candle(
                "BTC/USDT",
                "1m",
                NOW.replace(tzinfo=None),
                Decimal("1"),
                Decimal("1"),
                Decimal("1"),
                Decimal("1"),
                Decimal("0"),
            ),
            "UTC-aware",
        ),
        (
            lambda: Candle(
                "BTC/USDT",
                "1m",
                NOW.astimezone(timezone(timedelta(hours=1))),
                Decimal("1"),
                Decimal("1"),
                Decimal("1"),
                Decimal("1"),
                Decimal("0"),
            ),
            "UTC",
        ),
        (
            lambda: Balance("USDT", Decimal("10"), Decimal("11")),
            "available",
        ),
        (
            lambda: Position(
                "BTC/USDT",
                Decimal("1"),
                Decimal("0"),
                Decimal("1"),
                MarginMode.CROSS,
            ),
            "entry_price",
        ),
        (
            lambda: OrderRequest(
                "BTC/USDT",
                OrderSide.BUY,
                OrderType.MARKET,
                Decimal("0"),
                "client-1",
            ),
            "quantity",
        ),
        (
            lambda: OrderRequest(
                "BTC/USDT",
                OrderSide.BUY,
                OrderType.LIMIT,
                Decimal("1"),
                "client-1",
            ),
            "price",
        ),
        (
            lambda: Fill(
                "fill-1",
                "order-1",
                "BTC/USDT",
                OrderSide.BUY,
                Decimal("1"),
                Decimal("-1"),
                Decimal("0"),
                "USDT",
                NOW,
            ),
            "price",
        ),
    ],
)
def test_exchange_models_reject_invalid_financial_or_timestamp_data(
    factory: object, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        factory()  # type: ignore[operator]


def test_exchange_protocol_lists_the_adapter_boundary() -> None:
    expected_methods = {
        "test_connection",
        "fetch_closed_candles",
        "fetch_balance",
        "fetch_positions",
        "fetch_open_orders",
        "submit_order",
        "cancel_order",
        "fetch_order_fills",
        "set_leverage",
        "set_margin_mode",
    }

    assert expected_methods <= set(Exchange.__dict__)
