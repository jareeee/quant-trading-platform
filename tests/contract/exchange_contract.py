from dataclasses import dataclass

from quant_platform.exchange import Candle, Exchange, OrderRequest, OrderStatus


@dataclass(frozen=True, slots=True)
class ExchangeContractCase:
    exchange: Exchange
    symbol: str
    timeframe: str
    expected_candles: tuple[Candle, ...]
    market_order: OrderRequest
    limit_order: OrderRequest


class ExchangeContractTests:
    """Reusable behavioral contract; adapters provide an ExchangeContractCase fixture."""

    def test_connection_and_closed_candle_access(self, exchange_case: ExchangeContractCase) -> None:
        assert exchange_case.exchange.test_connection() is True
        assert exchange_case.exchange.fetch_closed_candles(
            exchange_case.symbol,
            exchange_case.timeframe,
            len(exchange_case.expected_candles),
        ) == exchange_case.expected_candles

    def test_market_order_is_idempotent_and_reports_fill(
        self, exchange_case: ExchangeContractCase
    ) -> None:
        exchange = exchange_case.exchange

        submitted = exchange.submit_order(exchange_case.market_order)
        repeated = exchange.submit_order(exchange_case.market_order)

        assert repeated == submitted
        assert submitted.status is OrderStatus.FILLED
        assert submitted.filled_quantity == exchange_case.market_order.quantity
        fills = exchange.fetch_order_fills(submitted.exchange_order_id, submitted.symbol)
        expected_quantity = exchange_case.market_order.quantity
        total_filled = sum(
            (fill.quantity for fill in fills), start=expected_quantity * 0
        )
        assert total_filled == expected_quantity

    def test_limit_order_is_open_then_cancelable(self, exchange_case: ExchangeContractCase) -> None:
        exchange = exchange_case.exchange

        submitted = exchange.submit_order(exchange_case.limit_order)

        assert submitted.status is OrderStatus.OPEN
        assert submitted in exchange.fetch_open_orders(exchange_case.symbol)
        canceled = exchange.cancel_order(submitted.exchange_order_id, exchange_case.symbol)
        assert canceled.status is OrderStatus.CANCELED
        assert submitted not in exchange.fetch_open_orders(exchange_case.symbol)
