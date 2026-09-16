from datetime import UTC, datetime
from decimal import Decimal

import pytest

from quant_platform.exchange import Candle, OrderRequest, OrderSide, OrderType
from quant_platform.exchange.fake import FakeExchange
from tests.contract.exchange_contract import ExchangeContractCase, ExchangeContractTests

NOW = datetime(2025, 1, 2, 3, 4, tzinfo=UTC)


class TestFakeExchangeContract(ExchangeContractTests):
    @pytest.fixture
    def exchange_case(self) -> ExchangeContractCase:
        candles = (
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
        )
        return ExchangeContractCase(
            exchange=FakeExchange(
                candles={("BTC/USDT", "1m"): candles},
                clock=lambda: NOW,
            ),
            symbol="BTC/USDT",
            timeframe="1m",
            expected_candles=candles,
            market_order=OrderRequest(
                "BTC/USDT",
                OrderSide.BUY,
                OrderType.MARKET,
                Decimal("1"),
                "contract-market",
            ),
            limit_order=OrderRequest(
                "BTC/USDT",
                OrderSide.SELL,
                OrderType.LIMIT,
                Decimal("1"),
                "contract-limit",
                Decimal("120"),
            ),
        )
