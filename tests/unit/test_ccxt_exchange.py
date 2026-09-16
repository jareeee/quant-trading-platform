from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import ccxt  # type: ignore[import-untyped]
import pytest

from quant_platform.exchange import (
    AuthenticationExchangeError,
    Balance,
    Candle,
    CcxtExchange,
    CcxtExchangeOptions,
    Exchange,
    Fill,
    MalformedExchangeResponse,
    MarginMode,
    NotFoundExchangeError,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    RejectedExchangeError,
    TransientExchangeError,
)

NOW = datetime(2025, 1, 2, 3, 4, 30, tzinfo=UTC)


class StubCcxtClient:
    def __init__(self) -> None:
        self.responses: dict[str, Any] = {"fetch_status": {"status": "ok"}}
        self.errors: dict[str, Exception] = {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            error = self.errors.get(name)
            if error is not None:
                raise error
            return self.responses[name]

        return call


def test_connection_uses_injected_client_without_network() -> None:
    client = StubCcxtClient()
    exchange = CcxtExchange(client, clock=lambda: NOW)

    assert isinstance(exchange, Exchange)
    assert exchange.test_connection() is True
    assert client.calls == [("fetch_status", (), {})]


def test_connection_rejects_malformed_status_response() -> None:
    client = StubCcxtClient()
    client.responses["fetch_status"] = {}
    exchange = CcxtExchange(client, clock=lambda: NOW)

    with pytest.raises(MalformedExchangeResponse, match="status"):
        exchange.test_connection()


@pytest.mark.parametrize(
    ("ccxt_error", "domain_error"),
    [
        (ccxt.NetworkError("api-key=super-secret"), TransientExchangeError),
        (ccxt.AuthenticationError("api-key=super-secret"), AuthenticationExchangeError),
        (ccxt.InvalidOrder("api-key=super-secret"), RejectedExchangeError),
        (ccxt.OrderNotFound("api-key=super-secret"), NotFoundExchangeError),
    ],
)
def test_connection_maps_ccxt_errors_without_leaking_credentials(
    ccxt_error: Exception, domain_error: type[Exception]
) -> None:
    client = StubCcxtClient()
    client.errors["fetch_status"] = ccxt_error
    exchange = CcxtExchange(client, clock=lambda: NOW)

    with pytest.raises(domain_error) as caught:
        exchange.test_connection()

    assert "super-secret" not in str(caught.value)
    assert str(caught.value) == "exchange request failed"


def test_fetch_closed_candles_excludes_current_candle_and_preserves_precision() -> None:
    client = StubCcxtClient()
    client.responses["parse_timeframe"] = 60
    client.responses["fetch_ohlcv"] = [
        [1735786980000, 100.123456789, "101.2", "99.5", "100.9", "12.00000001"],
        [1735787040000, "100.9", "102", "100", "101", "7"],
    ]
    exchange = CcxtExchange(client, clock=lambda: NOW)

    candles = exchange.fetch_closed_candles("BTC/USDT", "1m", 1)

    assert candles == (
        Candle(
            symbol="BTC/USDT",
            timeframe="1m",
            opened_at=datetime(2025, 1, 2, 3, 3, tzinfo=UTC),
            open=Decimal("100.123456789"),
            high=Decimal("101.2"),
            low=Decimal("99.5"),
            close=Decimal("100.9"),
            volume=Decimal("12.00000001"),
        ),
    )
    assert client.calls == [
        ("parse_timeframe", ("1m",), {}),
        ("fetch_ohlcv", ("BTC/USDT", "1m"), {"limit": 2}),
    ]


def test_fetch_closed_candles_rejects_malformed_rows() -> None:
    client = StubCcxtClient()
    client.responses["parse_timeframe"] = 60
    client.responses["fetch_ohlcv"] = [[1735786980000, "100", "101"]]
    exchange = CcxtExchange(client, clock=lambda: NOW)

    with pytest.raises(MalformedExchangeResponse, match="OHLCV"):
        exchange.fetch_closed_candles("BTC/USDT", "1m", 1)


def test_fetch_closed_candles_requires_positive_timeframe_and_utc_clock() -> None:
    client = StubCcxtClient()
    client.responses["parse_timeframe"] = 0
    client.responses["fetch_ohlcv"] = []
    exchange = CcxtExchange(client, clock=lambda: NOW)
    with pytest.raises(MalformedExchangeResponse, match="timeframe"):
        exchange.fetch_closed_candles("BTC/USDT", "1m", 1)

    client.responses["parse_timeframe"] = 60
    exchange = CcxtExchange(client, clock=lambda: NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="UTC"):
        exchange.fetch_closed_candles("BTC/USDT", "1m", 1)


def test_fetch_balance_normalizes_decimal_values() -> None:
    client = StubCcxtClient()
    client.responses["fetch_balance"] = {
        "total": {"USDT": 1000.123456789, "BTC": "0.5"},
        "free": {"USDT": "800.000000001", "BTC": "0.25"},
    }
    exchange = CcxtExchange(
        client,
        clock=lambda: NOW,
        options=CcxtExchangeOptions(method_params={"fetch_balance": {"accountType": "swap"}}),
    )

    assert exchange.fetch_balance() == (
        Balance("BTC", Decimal("0.5"), Decimal("0.25")),
        Balance("USDT", Decimal("1000.123456789"), Decimal("800.000000001")),
    )
    assert client.calls == [("fetch_balance", (), {"params": {"accountType": "swap"}})]


def test_fetch_positions_signs_short_quantity() -> None:
    client = StubCcxtClient()
    client.responses["fetch_positions"] = [
        {
            "symbol": "BTC/USDT:USDT",
            "contracts": "2.50000001",
            "side": "short",
            "entryPrice": "101.123456789",
            "leverage": "3",
            "marginMode": "isolated",
        }
    ]
    exchange = CcxtExchange(client, clock=lambda: NOW)

    assert exchange.fetch_positions() == (
        Position(
            "BTC/USDT:USDT",
            Decimal("-2.50000001"),
            Decimal("101.123456789"),
            Decimal("3"),
            MarginMode.ISOLATED,
        ),
    )


def test_fetch_positions_forwards_exchange_specific_options() -> None:
    client = StubCcxtClient()
    client.responses["fetch_positions"] = []
    exchange = CcxtExchange(
        client,
        clock=lambda: NOW,
        options=CcxtExchangeOptions(
            method_params={"fetch_positions": {"productType": "USDT-FUTURES"}}
        ),
    )

    assert exchange.fetch_positions() == ()
    assert client.calls == [
        (
            "fetch_positions",
            (None,),
            {"params": {"productType": "USDT-FUTURES"}},
        )
    ]


def test_balance_and_position_reject_missing_required_fields() -> None:
    client = StubCcxtClient()
    exchange = CcxtExchange(client, clock=lambda: NOW)
    client.responses["fetch_balance"] = {"total": {"USDT": "1"}}
    with pytest.raises(MalformedExchangeResponse, match="balance"):
        exchange.fetch_balance()

    client.responses["fetch_balance"] = {
        "total": {"USDT": "1"},
        "free": {"USDT": "2"},
    }
    with pytest.raises(MalformedExchangeResponse, match="balance"):
        exchange.fetch_balance()

    client.responses["fetch_positions"] = [{"symbol": "BTC/USDT", "contracts": "1"}]
    with pytest.raises(MalformedExchangeResponse, match="position"):
        exchange.fetch_positions()


def order_response(**overrides: object) -> dict[str, object]:
    response: dict[str, object] = {
        "id": "order-1",
        "clientOrderId": "strategy-1",
        "symbol": "BTC/USDT",
        "side": "buy",
        "type": "limit",
        "status": "open",
        "amount": "1.25000001",
        "filled": "0",
        "average": None,
        "timestamp": 1735787070123,
    }
    response.update(overrides)
    return response


def test_fetch_open_orders_normalizes_orders_and_forwards_injected_options() -> None:
    client = StubCcxtClient()
    client.responses["fetch_open_orders"] = [order_response()]
    options = CcxtExchangeOptions(
        method_params={"fetch_open_orders": {"category": "linear"}}
    )
    exchange = CcxtExchange(client, clock=lambda: NOW, options=options)

    orders = exchange.fetch_open_orders("BTC/USDT")

    assert orders[0].exchange_order_id == "order-1"
    assert orders[0].quantity == Decimal("1.25000001")
    assert orders[0].submitted_at == datetime(2025, 1, 2, 3, 4, 30, 123000, tzinfo=UTC)
    assert client.calls == [
        ("fetch_open_orders", ("BTC/USDT",), {"params": {"category": "linear"}})
    ]


@pytest.mark.parametrize(
    ("order_type", "price"),
    [(OrderType.MARKET, None), (OrderType.LIMIT, Decimal("99.123456789"))],
)
def test_submit_order_forwards_precision_client_id_and_type(
    order_type: OrderType, price: Decimal | None
) -> None:
    client = StubCcxtClient()
    client.responses["create_order"] = order_response(
        type=order_type.value,
        status="closed" if order_type is OrderType.MARKET else "open",
        filled="1.25000001" if order_type is OrderType.MARKET else "0",
        average="100.000000001" if order_type is OrderType.MARKET else None,
    )
    options = CcxtExchangeOptions(
        client_order_id_key="clientOid",
        method_params={"create_order": {"positionIdx": 1, "clientOid": "wrong"}},
    )
    exchange = CcxtExchange(client, clock=lambda: NOW, options=options)
    request = OrderRequest(
        "BTC/USDT",
        OrderSide.BUY,
        order_type,
        Decimal("1.25000001"),
        "strategy-1",
        price,
    )

    result = exchange.submit_order(request)

    assert result.status is (
        OrderStatus.FILLED if order_type is OrderType.MARKET else OrderStatus.OPEN
    )
    assert client.calls == [
        (
            "create_order",
            (
                "BTC/USDT",
                order_type.value,
                "buy",
                "1.25000001",
                None if price is None else str(price),
            ),
            {"params": {"positionIdx": 1, "clientOid": "strategy-1"}},
        )
    ]


def test_submit_order_uses_requested_client_id_when_exchange_omits_it() -> None:
    client = StubCcxtClient()
    client.responses["create_order"] = order_response(
        clientOrderId=None,
        type="market",
        status="closed",
        filled="1",
        amount="1",
        average="100",
    )
    exchange = CcxtExchange(client, clock=lambda: NOW)

    result = exchange.submit_order(
        OrderRequest(
            "BTC/USDT",
            OrderSide.BUY,
            OrderType.MARKET,
            Decimal("1"),
            "requested-client-id",
        )
    )

    assert result.client_order_id == "requested-client-id"


def test_cancel_and_fetch_fills_preserve_exchange_ids_and_timestamps() -> None:
    client = StubCcxtClient()
    client.responses["cancel_order"] = order_response(status="canceled")
    client.responses["fetch_my_trades"] = [
        {
            "id": "fill-9",
            "order": "order-1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "amount": "1.25000001",
            "price": "100.000000001",
            "fee": {"cost": "0.00001", "currency": "USDT"},
            "timestamp": 1735787070456,
        },
        {
            "id": "other-fill",
            "order": "other-order",
            "symbol": "BTC/USDT",
            "side": "buy",
            "amount": "1",
            "price": "100",
            "fee": {"cost": "0", "currency": "USDT"},
            "timestamp": 1735787070456,
        },
    ]
    exchange = CcxtExchange(client, clock=lambda: NOW)

    canceled = exchange.cancel_order("order-1", "BTC/USDT")
    fills = exchange.fetch_order_fills("order-1", "BTC/USDT")

    assert canceled.status is OrderStatus.CANCELED
    assert fills == (
        Fill(
            "fill-9",
            "order-1",
            "BTC/USDT",
            OrderSide.BUY,
            Decimal("1.25000001"),
            Decimal("100.000000001"),
            Decimal("0.00001"),
            "USDT",
            datetime(2025, 1, 2, 3, 4, 30, 456000, tzinfo=UTC),
        ),
    )
    assert client.calls == [
        ("cancel_order", ("order-1", "BTC/USDT"), {"params": {}}),
        ("fetch_my_trades", ("BTC/USDT",), {"params": {"order": "order-1"}}),
    ]


def test_orders_and_fills_reject_missing_required_fields() -> None:
    client = StubCcxtClient()
    exchange = CcxtExchange(client, clock=lambda: NOW)
    client.responses["fetch_open_orders"] = [order_response(clientOrderId=None)]
    with pytest.raises(MalformedExchangeResponse, match="order"):
        exchange.fetch_open_orders()

    client.responses["fetch_my_trades"] = [
        {
            "id": "fill-1",
            "order": "order-1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "amount": "1",
            "price": "100",
            "fee": {"cost": "0"},
            "timestamp": 1735787070456,
        }
    ]
    with pytest.raises(MalformedExchangeResponse, match="fill"):
        exchange.fetch_order_fills("order-1", "BTC/USDT")


def test_leverage_and_margin_mode_forward_values_and_options() -> None:
    client = StubCcxtClient()
    client.responses["set_leverage"] = {}
    client.responses["set_margin_mode"] = {}
    options = CcxtExchangeOptions(
        method_params={
            "set_leverage": {"category": "linear"},
            "set_margin_mode": {"category": "linear"},
        }
    )
    exchange = CcxtExchange(client, clock=lambda: NOW, options=options)

    exchange.set_leverage("BTC/USDT", Decimal("5"))
    exchange.set_margin_mode("BTC/USDT", MarginMode.ISOLATED)

    assert client.calls == [
        ("set_leverage", (5, "BTC/USDT"), {"params": {"category": "linear"}}),
        (
            "set_margin_mode",
            ("isolated", "BTC/USDT"),
            {"params": {"category": "linear"}},
        ),
    ]
