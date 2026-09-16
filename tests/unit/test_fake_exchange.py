from datetime import UTC, datetime
from decimal import Decimal

import pytest

from quant_platform.exchange import (
    Balance,
    Candle,
    Exchange,
    FakeExchange,
    MarginMode,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)

NOW = datetime(2025, 1, 2, 3, 4, tzinfo=UTC)
CANDLES = (
    Candle(
        "BTC/USDT",
        "1m",
        NOW,
        Decimal("100"),
        Decimal("110"),
        Decimal("90"),
        Decimal("105"),
        Decimal("10"),
    ),
    Candle(
        "BTC/USDT",
        "1m",
        NOW.replace(minute=5),
        Decimal("105"),
        Decimal("120"),
        Decimal("100"),
        Decimal("115"),
        Decimal("12"),
    ),
)
BALANCES = (Balance("USDT", Decimal("1000"), Decimal("800")),)
POSITIONS = (
    Position(
        "ETH/USDT",
        Decimal("2"),
        Decimal("2000"),
        Decimal("2"),
        MarginMode.ISOLATED,
    ),
)


def make_exchange() -> FakeExchange:
    return FakeExchange(
        candles={("BTC/USDT", "1m"): CANDLES},
        balances=BALANCES,
        positions=POSITIONS,
        clock=lambda: NOW,
    )


def test_fake_exchange_is_contract_compatible_and_returns_configured_state() -> None:
    exchange = make_exchange()

    assert isinstance(exchange, Exchange)
    assert exchange.test_connection() is True
    assert exchange.fetch_closed_candles("BTC/USDT", "1m", 1) == CANDLES[-1:]
    assert exchange.fetch_balance() == BALANCES
    assert exchange.fetch_positions() == POSITIONS
    assert exchange.fetch_open_orders() == ()
    assert [call.method for call in exchange.calls] == [
        "test_connection",
        "fetch_closed_candles",
        "fetch_balance",
        "fetch_positions",
        "fetch_open_orders",
    ]


def test_market_order_fills_at_latest_close_and_is_idempotent() -> None:
    exchange = make_exchange()
    request = OrderRequest(
        "BTC/USDT",
        OrderSide.BUY,
        OrderType.MARKET,
        Decimal("2"),
        "market-1",
    )

    first = exchange.submit_order(request)
    repeated = exchange.submit_order(request)

    assert repeated is first
    assert first.exchange_order_id == "fake-order-000001"
    assert first.status is OrderStatus.FILLED
    assert first.filled_quantity == Decimal("2")
    assert first.average_fill_price == Decimal("115")
    assert first.submitted_at == NOW
    fills = exchange.fetch_order_fills(first.exchange_order_id, "BTC/USDT")
    assert len(fills) == 1
    assert fills[0].exchange_fill_id == "fake-fill-000001"
    assert fills[0].price == Decimal("115")
    assert fills[0].executed_at == NOW
    assert exchange.fetch_order_fills(first.exchange_order_id, "ETH/USDT") == ()


def test_market_order_updates_positions() -> None:
    exchange = make_exchange()

    exchange.submit_order(
        OrderRequest(
            "BTC/USDT",
            OrderSide.BUY,
            OrderType.MARKET,
            Decimal("2"),
            "position-buy",
        )
    )
    exchange.submit_order(
        OrderRequest(
            "BTC/USDT",
            OrderSide.SELL,
            OrderType.MARKET,
            Decimal("0.5"),
            "position-reduce",
        )
    )

    position = next(
        position for position in exchange.fetch_positions() if position.symbol == "BTC/USDT"
    )
    assert position.quantity == Decimal("1.5")
    assert position.entry_price == Decimal("115")


def test_limit_order_remains_open_until_canceled() -> None:
    exchange = make_exchange()
    request = OrderRequest(
        "BTC/USDT",
        OrderSide.SELL,
        OrderType.LIMIT,
        Decimal("1.5"),
        "limit-1",
        Decimal("130"),
    )

    submitted = exchange.submit_order(request)

    assert submitted.status is OrderStatus.OPEN
    assert submitted.filled_quantity == 0
    assert exchange.fetch_open_orders() == (submitted,)
    assert exchange.fetch_open_orders("ETH/USDT") == ()
    canceled = exchange.cancel_order(submitted.exchange_order_id, "BTC/USDT")
    assert canceled.status is OrderStatus.CANCELED
    assert exchange.fetch_open_orders() == ()
    assert exchange.fetch_order_fills(submitted.exchange_order_id, "BTC/USDT") == ()


def test_reusing_client_order_id_with_different_request_is_rejected() -> None:
    exchange = make_exchange()
    exchange.submit_order(
        OrderRequest(
            "BTC/USDT",
            OrderSide.BUY,
            OrderType.MARKET,
            Decimal("1"),
            "duplicate-id",
        )
    )

    with pytest.raises(ValueError, match="client_order_id"):
        exchange.submit_order(
            OrderRequest(
                "BTC/USDT",
                OrderSide.SELL,
                OrderType.MARKET,
                Decimal("1"),
                "duplicate-id",
            )
        )


def test_injected_failure_is_deterministic_consumed_and_recorded() -> None:
    exchange = make_exchange()
    exchange.inject_failure("fetch_balance", RuntimeError("exchange unavailable"))

    with pytest.raises(RuntimeError, match="exchange unavailable"):
        exchange.fetch_balance()

    assert exchange.fetch_balance() == BALANCES
    assert [call.method for call in exchange.calls] == ["fetch_balance", "fetch_balance"]


def test_set_leverage_updates_configured_position() -> None:
    exchange = make_exchange()

    exchange.set_leverage("ETH/USDT", Decimal("5"))

    assert exchange.fetch_positions()[0].leverage == Decimal("5")
    with pytest.raises(ValueError, match="leverage"):
        exchange.set_leverage("ETH/USDT", Decimal("0"))


def test_set_margin_mode_updates_configured_position() -> None:
    exchange = make_exchange()

    exchange.set_margin_mode("ETH/USDT", MarginMode.CROSS)

    assert exchange.fetch_positions()[0].margin_mode is MarginMode.CROSS


def test_only_matching_open_order_can_be_canceled() -> None:
    exchange = make_exchange()
    filled = exchange.submit_order(
        OrderRequest(
            "BTC/USDT",
            OrderSide.BUY,
            OrderType.MARKET,
            Decimal("1"),
            "filled-1",
        )
    )
    open_order = exchange.submit_order(
        OrderRequest(
            "BTC/USDT",
            OrderSide.BUY,
            OrderType.LIMIT,
            Decimal("1"),
            "open-1",
            Decimal("90"),
        )
    )

    with pytest.raises(ValueError, match="not open"):
        exchange.cancel_order(filled.exchange_order_id, "BTC/USDT")
    with pytest.raises(ValueError, match="symbol"):
        exchange.cancel_order(open_order.exchange_order_id, "ETH/USDT")
