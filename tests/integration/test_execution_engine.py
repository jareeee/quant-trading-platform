from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from quant_platform.db.base import Base
from quant_platform.db.models import (
    AssetConfig,
    ExchangeConfig,
    Order,
    Signal,
    StrategyRun,
)
from quant_platform.db.models import (
    Fill as PersistedFill,
)
from quant_platform.db.session import create_engine, create_session_factory
from quant_platform.exchange import Candle, FakeExchange, Position
from quant_platform.strategies import SignalDecision, StrategyContext, StrategySignal
from quant_platform.trading import (
    ExecutionRequest,
    ExecutionStatus,
    MarketConstraints,
    RiskLimits,
    TradingEngine,
)

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)
BOUNDARY = datetime(2026, 9, 17, 11, tzinfo=UTC)
SYMBOL = "BTC/USD"
PRICE = Decimal("100")


class StubStrategy:
    def __init__(self, signal: StrategySignal | Exception) -> None:
        self.signal = signal
        self.calls: list[StrategyContext] = []

    def generate_signal(self, context: StrategyContext) -> StrategySignal:
        self.calls.append(context)
        if isinstance(self.signal, Exception):
            raise self.signal
        return self.signal


class NoFillExchange(FakeExchange):
    def fetch_order_fills(self, exchange_order_id: str, symbol: str):  # type: ignore[no-untyped-def]
        self._record("fetch_order_fills", exchange_order_id, symbol)
        return ()


class MisdirectedFillExchange(FakeExchange):
    def fetch_order_fills(self, exchange_order_id: str, symbol: str):  # type: ignore[no-untyped-def]
        fills = super().fetch_order_fills(exchange_order_id, symbol)
        return (replace(fills[0], exchange_order_id="another-order"),)


class QuantityMismatchExchange(FakeExchange):
    def fetch_order_fills(self, exchange_order_id: str, symbol: str):  # type: ignore[no-untyped-def]
        fills = super().fetch_order_fills(exchange_order_id, symbol)
        return (replace(fills[0], quantity=Decimal("1")),)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'execution.db'}")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as database_session:
        yield database_session
    engine.dispose()


@pytest.fixture
def asset(session: Session) -> AssetConfig:
    exchange = ExchangeConfig(name="primary", exchange="fake", options={})
    session.add(exchange)
    session.flush()
    asset = AssetConfig(
        exchange_config_id=exchange.id,
        symbol=SYMBOL,
        base_asset="BTC",
        quote_asset="USD",
        settings={},
    )
    session.add(asset)
    session.commit()
    return asset


def candle() -> Candle:
    return Candle(
        symbol=SYMBOL,
        timeframe="1h",
        opened_at=BOUNDARY - timedelta(hours=1),
        open=PRICE,
        high=PRICE,
        low=PRICE,
        close=PRICE,
        volume=Decimal("1"),
    )


def context(asset: AssetConfig, *, position=None) -> StrategyContext:  # type: ignore[no-untyped-def]
    return StrategyContext(
        asset_id=asset.id,
        symbol=SYMBOL,
        timeframe="1h",
        scheduled_boundary=BOUNDARY,
        candles=(candle(),),
        position=position,
        parameters={},
    )


def signal(decision: SignalDecision) -> StrategySignal:
    return StrategySignal(
        decision=decision,
        confidence=Decimal("0.8"),
        reason="test decision",
        strategy_name="stub",
        strategy_version="1",
        generated_at=NOW,
        evidence={"source": "test"},
    )


def request(
    asset: AssetConfig, *, strategy_context: StrategyContext | None = None
) -> ExecutionRequest:
    return ExecutionRequest(
        exchange_config_id=asset.exchange_config_id,
        context=strategy_context or context(asset),
        quantity=Decimal("2"),
        execution_price=PRICE,
        available_balance=Decimal("1000"),
        current_exposure=Decimal("0"),
        leverage=Decimal("2"),
        constraints=MarketConstraints(
            minimum_quantity=Decimal("0.1"),
            maximum_quantity=Decimal("100"),
            quantity_step=Decimal("0.1"),
            minimum_notional=Decimal("1"),
        ),
        limits=RiskLimits(
            max_notional=Decimal("10000"),
            max_leverage=Decimal("5"),
            max_position_quantity=Decimal("100"),
            max_data_age=timedelta(minutes=5),
        ),
        market_data_at=NOW - timedelta(minutes=1),
    )


def engine(
    session: Session,
    strategy: StubStrategy,
    exchange: FakeExchange,
    *,
    clock: Callable[[], datetime] = lambda: NOW,
) -> TradingEngine:
    return TradingEngine(session=session, exchange=exchange, strategy=strategy, clock=clock)


def test_hold_persists_signal_and_completes_without_an_order(
    session: Session, asset: AssetConfig
) -> None:
    strategy = StubStrategy(signal(SignalDecision.HOLD))
    exchange = FakeExchange(candles={(SYMBOL, "1h"): (candle(),)}, clock=lambda: NOW)

    result = engine(session, strategy, exchange).execute(request(asset))

    assert result.status is ExecutionStatus.SUCCESS
    assert len(strategy.calls) == 1
    assert exchange.calls == ()
    run = session.get_one(StrategyRun, result.run_id)
    assert run.status == "completed"
    assert run.strategy_name == "stub"
    assert run.ended_at is not None
    assert run.ended_at.replace(tzinfo=UTC) == NOW
    persisted_signal = session.scalars(select(Signal)).one()
    assert persisted_signal.side == "hold"
    assert persisted_signal.quantity == Decimal("0")
    assert persisted_signal.payload == {
        "confidence": "0.8",
        "reason": "test decision",
        "strategy_name": "stub",
        "strategy_version": "1",
        "evidence": {"source": "test"},
    }
    assert session.scalars(select(Order)).all() == []


def test_duplicate_boundary_returns_duplicate_without_strategy_or_exchange_calls(
    session: Session, asset: AssetConfig
) -> None:
    first_strategy = StubStrategy(signal(SignalDecision.HOLD))
    exchange = FakeExchange(candles={(SYMBOL, "1h"): (candle(),)}, clock=lambda: NOW)
    first = engine(session, first_strategy, exchange).execute(request(asset))
    duplicate_strategy = StubStrategy(signal(SignalDecision.LONG))

    duplicate = engine(session, duplicate_strategy, exchange).execute(request(asset))

    assert first.status is ExecutionStatus.SUCCESS
    assert duplicate.status is ExecutionStatus.DUPLICATE
    assert duplicate.run_id == first.run_id
    assert duplicate_strategy.calls == []
    assert exchange.calls == ()
    assert session.scalars(select(StrategyRun)).all() == [
        session.get_one(StrategyRun, first.run_id)
    ]


def test_long_submits_deterministic_market_buy_and_succeeds_only_after_fills_persist(
    session: Session, asset: AssetConfig
) -> None:
    strategy = StubStrategy(signal(SignalDecision.LONG))
    exchange = FakeExchange(candles={(SYMBOL, "1h"): (candle(),)}, clock=lambda: NOW)

    result = engine(session, strategy, exchange).execute(request(asset))

    assert result.status is ExecutionStatus.SUCCESS
    assert [call.method for call in exchange.calls] == ["submit_order", "fetch_order_fills"]
    submitted = exchange.calls[0].args[0]
    assert submitted.side.value == "buy"
    assert submitted.order_type.value == "market"
    assert submitted.quantity == Decimal("2")
    assert submitted.client_order_id == f"strategy-order:{asset.id}:{BOUNDARY.isoformat()}"
    persisted_order = session.scalars(select(Order)).one()
    assert result.order_id == persisted_order.id
    assert persisted_order.exchange_order_id == "fake-order-000001"
    assert persisted_order.status == "filled"
    assert persisted_order.filled_quantity == Decimal("2")
    persisted_fill = session.scalars(select(PersistedFill)).one()
    assert persisted_fill.exchange_fill_id == "fake-fill-000001"
    assert persisted_fill.quantity == Decimal("2")
    assert persisted_fill.price == PRICE
    assert persisted_fill.fee_amount == Decimal("0")
    assert persisted_fill.fee_currency == "USD"
    assert session.get_one(StrategyRun, result.run_id).status == "completed"


def test_short_submits_market_sell_and_persists_authoritative_fill(
    session: Session, asset: AssetConfig
) -> None:
    strategy = StubStrategy(signal(SignalDecision.SHORT))
    exchange = FakeExchange(candles={(SYMBOL, "1h"): (candle(),)}, clock=lambda: NOW)

    result = engine(session, strategy, exchange).execute(request(asset))

    assert result.status is ExecutionStatus.SUCCESS
    submitted = exchange.calls[0].args[0]
    assert submitted.side.value == "sell"
    assert submitted.quantity == Decimal("2")
    persisted_order = session.scalars(select(Order)).one()
    assert persisted_order.side == "sell"
    assert session.scalars(select(PersistedFill)).one().quantity == Decimal("2")


def test_close_long_uses_exact_opposite_quantity_and_bypasses_entry_sizing_rules(
    session: Session, asset: AssetConfig
) -> None:
    position = Position(
        symbol=SYMBOL,
        quantity=Decimal("8"),
        entry_price=Decimal("90"),
        leverage=Decimal("2"),
    )
    strategy = StubStrategy(signal(SignalDecision.CLOSE))
    exchange = FakeExchange(
        candles={(SYMBOL, "1h"): (candle(),)},
        positions=(position,),
        clock=lambda: NOW,
    )
    close_request = replace(
        request(asset, strategy_context=context(asset, position=position)),
        available_balance=Decimal("0"),
        current_exposure=Decimal("50000"),
        limits=RiskLimits(
            max_notional=Decimal("10"),
            max_leverage=Decimal("5"),
            max_position_quantity=Decimal("1"),
            max_data_age=timedelta(minutes=5),
        ),
    )

    result = engine(session, strategy, exchange).execute(close_request)

    assert result.status is ExecutionStatus.SUCCESS
    submitted = exchange.calls[0].args[0]
    assert submitted.side.value == "sell"
    assert submitted.quantity == Decimal("8")
    assert session.scalars(select(Order)).one().quantity == Decimal("8")


def test_zero_authoritative_fills_fails_run_without_persisting_order_and_session_recovers(
    session: Session, asset: AssetConfig
) -> None:
    strategy = StubStrategy(signal(SignalDecision.LONG))
    exchange = NoFillExchange(candles={(SYMBOL, "1h"): (candle(),)}, clock=lambda: NOW)

    result = engine(session, strategy, exchange).execute(request(asset))

    assert result.status is ExecutionStatus.FAILED
    assert result.reason == "execution failed"
    assert session.get_one(StrategyRun, result.run_id).status == "failed"
    assert session.scalars(select(Order)).all() == []
    assert session.scalars(select(PersistedFill)).all() == []
    calls_before_retry = exchange.calls
    retry_strategy = StubStrategy(signal(SignalDecision.LONG))
    retry = engine(session, retry_strategy, exchange).execute(request(asset))
    assert retry.status is ExecutionStatus.DUPLICATE
    assert retry_strategy.calls == []
    assert exchange.calls == calls_before_retry
    recovery_row = ExchangeConfig(name="recovery", exchange="fake", options={})
    session.add(recovery_row)
    session.commit()
    assert recovery_row.id is not None


def test_fill_for_another_exchange_order_is_not_successful(
    session: Session, asset: AssetConfig
) -> None:
    strategy = StubStrategy(signal(SignalDecision.LONG))
    exchange = MisdirectedFillExchange(
        candles={(SYMBOL, "1h"): (candle(),)}, clock=lambda: NOW
    )

    result = engine(session, strategy, exchange).execute(request(asset))

    assert result.status is ExecutionStatus.FAILED
    assert session.get_one(StrategyRun, result.run_id).status == "failed"
    assert session.scalars(select(Order)).all() == []
    assert session.scalars(select(PersistedFill)).all() == []


def test_fill_quantity_must_match_authoritative_order_result(
    session: Session, asset: AssetConfig
) -> None:
    strategy = StubStrategy(signal(SignalDecision.LONG))
    exchange = QuantityMismatchExchange(
        candles={(SYMBOL, "1h"): (candle(),)}, clock=lambda: NOW
    )

    result = engine(session, strategy, exchange).execute(request(asset))

    assert result.status is ExecutionStatus.FAILED
    assert session.scalars(select(Order)).all() == []
    assert session.scalars(select(PersistedFill)).all() == []


def test_risk_rejection_is_fail_closed_before_exchange_submission(
    session: Session, asset: AssetConfig
) -> None:
    strategy = StubStrategy(signal(SignalDecision.LONG))
    exchange = FakeExchange(candles={(SYMBOL, "1h"): (candle(),)}, clock=lambda: NOW)
    stale_request = replace(request(asset), market_data_at=NOW - timedelta(hours=1))

    result = engine(session, strategy, exchange).execute(stale_request)

    assert result.status is ExecutionStatus.RISK_REJECTED
    assert result.reason == "market data is stale"
    assert exchange.calls == ()
    assert session.get_one(StrategyRun, result.run_id).status == "rejected"
    assert session.scalars(select(Signal)).one().side == "long"
    assert session.scalars(select(Order)).all() == []


def test_strategy_exception_marks_run_failed_without_exposing_error_and_session_recovers(
    session: Session, asset: AssetConfig
) -> None:
    strategy = StubStrategy(RuntimeError("api_key=top-secret"))
    exchange = FakeExchange(candles={(SYMBOL, "1h"): (candle(),)}, clock=lambda: NOW)

    result = engine(session, strategy, exchange).execute(request(asset))

    assert result.status is ExecutionStatus.FAILED
    assert result.reason == "execution failed"
    assert "secret" not in result.reason
    assert session.get_one(StrategyRun, result.run_id).status == "failed"
    assert session.scalars(select(Signal)).all() == []
    recovery_row = ExchangeConfig(name="after-strategy-error", exchange="fake", options={})
    session.add(recovery_row)
    session.commit()
    assert recovery_row.id is not None


def test_close_short_buys_exact_absolute_position_quantity(
    session: Session, asset: AssetConfig
) -> None:
    position = Position(
        symbol=SYMBOL,
        quantity=Decimal("-3.5"),
        entry_price=Decimal("110"),
        leverage=Decimal("2"),
    )
    strategy = StubStrategy(signal(SignalDecision.CLOSE))
    exchange = FakeExchange(
        candles={(SYMBOL, "1h"): (candle(),)}, positions=(position,), clock=lambda: NOW
    )

    result = engine(session, strategy, exchange).execute(
        request(asset, strategy_context=context(asset, position=position))
    )

    assert result.status is ExecutionStatus.SUCCESS
    submitted = exchange.calls[0].args[0]
    assert submitted.side.value == "buy"
    assert submitted.quantity == Decimal("3.5")


def test_close_without_nonzero_position_is_rejected_without_exchange_call(
    session: Session, asset: AssetConfig
) -> None:
    strategy = StubStrategy(signal(SignalDecision.CLOSE))
    exchange = FakeExchange(candles={(SYMBOL, "1h"): (candle(),)}, clock=lambda: NOW)

    result = engine(session, strategy, exchange).execute(request(asset))

    assert result.status is ExecutionStatus.RISK_REJECTED
    assert result.reason == "close requires an existing nonzero position"
    assert exchange.calls == ()
    assert session.scalars(select(Order)).all() == []


def test_close_validates_authoritative_position_leverage(
    session: Session, asset: AssetConfig
) -> None:
    position = Position(
        symbol=SYMBOL,
        quantity=Decimal("2"),
        entry_price=Decimal("90"),
        leverage=Decimal("10"),
    )
    strategy = StubStrategy(signal(SignalDecision.CLOSE))
    exchange = FakeExchange(
        candles={(SYMBOL, "1h"): (candle(),)}, positions=(position,), clock=lambda: NOW
    )
    close_request = request(asset, strategy_context=context(asset, position=position))

    result = engine(session, strategy, exchange).execute(close_request)

    assert result.status is ExecutionStatus.RISK_REJECTED
    assert result.reason == "above maximum leverage"
    assert exchange.calls == ()
