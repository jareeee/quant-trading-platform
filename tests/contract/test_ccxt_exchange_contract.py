from datetime import UTC, datetime
from decimal import Decimal

import pytest

from quant_platform.exchange import Candle, CcxtExchange, OrderRequest, OrderSide, OrderType
from tests.contract.exchange_contract import ExchangeContractCase, ExchangeContractTests

NOW = datetime(2025, 1, 2, 3, 4, 30, tzinfo=UTC)


class StatefulCcxtClient:
    """Offline CCXT-shaped client used to run the reusable exchange contract."""

    def __init__(self) -> None:
        self.orders: dict[str, dict[str, object]] = {}
        self.fills: list[dict[str, object]] = []
        self.next_id = 1

    def fetch_status(self) -> dict[str, str]:
        return {"status": "ok"}

    def parse_timeframe(self, timeframe: str) -> int:
        assert timeframe == "1m"
        return 60

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, *, limit: int
    ) -> list[list[object]]:
        assert (symbol, timeframe, limit) == ("BTC/USDT", "1m", 2)
        return [[1735786980000, "100", "110", "90", "105", "10"]]

    def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: str,
        price: str | None,
        *,
        params: dict[str, object],
    ) -> dict[str, object]:
        client_id = str(params["clientOrderId"])
        existing = self.orders.get(client_id)
        if existing is not None:
            return existing
        order_id = f"ccxt-{self.next_id}"
        self.next_id += 1
        market = order_type == "market"
        order: dict[str, object] = {
            "id": order_id,
            "clientOrderId": client_id,
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "status": "closed" if market else "open",
            "amount": amount,
            "filled": amount if market else "0",
            "average": "105" if market else None,
            "timestamp": 1735787070000,
        }
        self.orders[client_id] = order
        if market:
            self.fills.append(
                {
                    "id": f"fill-{order_id}",
                    "order": order_id,
                    "symbol": symbol,
                    "side": side,
                    "amount": amount,
                    "price": "105",
                    "fee": {"cost": "0", "currency": "USDT"},
                    "timestamp": 1735787070000,
                }
            )
        return order

    def fetch_open_orders(
        self, symbol: str | None, *, params: dict[str, object]
    ) -> list[dict[str, object]]:
        assert params == {}
        return [
            order
            for order in self.orders.values()
            if order["status"] == "open" and (symbol is None or order["symbol"] == symbol)
        ]

    def cancel_order(
        self, order_id: str, symbol: str, *, params: dict[str, object]
    ) -> dict[str, object]:
        assert params == {}
        order = next(order for order in self.orders.values() if order["id"] == order_id)
        assert order["symbol"] == symbol
        order["status"] = "canceled"
        return order

    def fetch_my_trades(
        self, symbol: str, *, params: dict[str, object]
    ) -> list[dict[str, object]]:
        return [
            fill
            for fill in self.fills
            if fill["symbol"] == symbol and fill["order"] == params["order"]
        ]


class TestCcxtExchangeContract(ExchangeContractTests):
    @pytest.fixture
    def exchange_case(self) -> ExchangeContractCase:
        candle = Candle(
            "BTC/USDT",
            "1m",
            datetime(2025, 1, 2, 3, 3, tzinfo=UTC),
            Decimal("100"),
            Decimal("110"),
            Decimal("90"),
            Decimal("105"),
            Decimal("10"),
        )
        return ExchangeContractCase(
            exchange=CcxtExchange(StatefulCcxtClient(), clock=lambda: NOW),
            symbol="BTC/USDT",
            timeframe="1m",
            expected_candles=(candle,),
            market_order=OrderRequest(
                "BTC/USDT", OrderSide.BUY, OrderType.MARKET, Decimal("1"), "market-1"
            ),
            limit_order=OrderRequest(
                "BTC/USDT",
                OrderSide.SELL,
                OrderType.LIMIT,
                Decimal("1"),
                "limit-1",
                Decimal("120"),
            ),
        )
