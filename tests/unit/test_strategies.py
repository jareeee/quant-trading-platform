import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from quant_platform.exchange import Candle, Position
from quant_platform.strategies import (
    EvidenceValue,
    NoTradeStrategy,
    ParameterValue,
    SignalDecision,
    Strategy,
    StrategyContext,
    StrategyRegistry,
    StrategySignal,
)

BOUNDARY = datetime(2025, 1, 1, 0, 2, tzinfo=UTC)


def candle(opened_at: datetime, *, symbol: str = "BTC/USDT", timeframe: str = "1m") -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        opened_at=opened_at,
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("10"),
    )


def test_strategy_context_is_deeply_immutable() -> None:
    thresholds: list[ParameterValue] = ["0.1", "0.2"]
    parameters: dict[str, ParameterValue] = {"thresholds": thresholds}
    context = StrategyContext(
        asset_id=42,
        symbol="BTC/USDT",
        timeframe="1m",
        scheduled_boundary=BOUNDARY,
        candles=(candle(datetime(2025, 1, 1, 0, 0, tzinfo=UTC)),),
        position=Position("BTC/USDT", Decimal("1"), Decimal("100")),
        parameters=parameters,
    )
    thresholds.append("0.3")

    assert context.parameters["thresholds"] == ("0.1", "0.2")
    with pytest.raises(TypeError):
        context.parameters["new"] = True  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        context.symbol = "ETH/USDT"  # type: ignore[misc]


def test_strategy_context_copies_candles_to_a_tuple() -> None:
    source = [candle(datetime(2025, 1, 1, 0, 0, tzinfo=UTC))]
    context = StrategyContext(
        asset_id=42,
        symbol="BTC/USDT",
        timeframe="1m",
        scheduled_boundary=BOUNDARY,
        candles=source,  # type: ignore[arg-type]
        position=None,
        parameters={},
    )
    source.append(candle(datetime(2025, 1, 1, 0, 1, tzinfo=UTC)))

    assert isinstance(context.candles, tuple)
    assert len(context.candles) == 1


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"candles": ()}, "candles must not be empty"),
        (
            {"candles": (candle(datetime(2025, 1, 1, 0, 0, tzinfo=UTC), symbol="ETH/USDT"),)},
            "candle symbol",
        ),
        (
            {"candles": (candle(datetime(2025, 1, 1, 0, 0, tzinfo=UTC), timeframe="5m"),)},
            "candle timeframe",
        ),
        (
            {
                "candles": (
                    candle(datetime(2025, 1, 1, 0, 1, tzinfo=UTC)),
                    candle(datetime(2025, 1, 1, 0, 0, tzinfo=UTC)),
                )
            },
            "strictly ascending",
        ),
        (
            {"candles": (candle(datetime(2025, 1, 1, 0, 2, tzinfo=UTC)),)},
            "fully closed",
        ),
        (
            {"scheduled_boundary": BOUNDARY.replace(tzinfo=None)},
            "scheduled_boundary must be UTC-aware",
        ),
    ],
)
def test_strategy_context_rejects_invalid_candle_sets(
    changes: dict[str, object], match: str
) -> None:
    values: dict[str, object] = {
        "asset_id": 42,
        "symbol": "BTC/USDT",
        "timeframe": "1m",
        "scheduled_boundary": BOUNDARY,
        "candles": (candle(datetime(2025, 1, 1, 0, 0, tzinfo=UTC)),),
        "position": None,
        "parameters": {},
    }
    values.update(changes)

    with pytest.raises(ValueError, match=match):
        StrategyContext(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "parameters",
    [
        [],
        {1: "not a string key"},
        {"bad": object()},
        {"bad": float("inf")},
    ],
)
def test_strategy_context_rejects_invalid_parameters(parameters: object) -> None:
    with pytest.raises(ValueError, match="parameters"):
        StrategyContext(
            asset_id=42,
            symbol="BTC/USDT",
            timeframe="1m",
            scheduled_boundary=BOUNDARY,
            candles=(candle(datetime(2025, 1, 1, 0, 0, tzinfo=UTC)),),
            position=None,
            parameters=parameters,  # type: ignore[arg-type]
        )


def valid_context() -> StrategyContext:
    return StrategyContext(
        asset_id=42,
        symbol="BTC/USDT",
        timeframe="1m",
        scheduled_boundary=BOUNDARY,
        candles=(candle(datetime(2025, 1, 1, 0, 1, tzinfo=UTC)),),
        position=None,
        parameters={"enabled": True},
    )


def test_no_trade_strategy_returns_a_deterministic_hold_signal() -> None:
    strategy = NoTradeStrategy()
    context = valid_context()

    first = strategy.generate_signal(context)
    second = strategy.generate_signal(context)

    assert isinstance(strategy, Strategy)
    assert first == second == StrategySignal(
        decision=SignalDecision.HOLD,
        confidence=Decimal("0"),
        reason="No-trade strategy always holds",
        strategy_name="no-trade",
        strategy_version="1.0.0",
        generated_at=BOUNDARY,
        evidence={},
    )
    assert first.score == first.confidence
    assert json.dumps(first.evidence) == "{}"


def test_registry_registers_lists_looks_up_and_creates_deterministically() -> None:
    class AnotherNoTrade(NoTradeStrategy):
        pass

    registry = StrategyRegistry()
    registry.register("z-last", AnotherNoTrade)
    registry.register("a-first", NoTradeStrategy)

    assert registry.names() == ("a-first", "z-last")
    assert registry.get("a-first") is NoTradeStrategy
    assert isinstance(registry.create("a-first"), NoTradeStrategy)
    assert registry.create("a-first") is not registry.create("a-first")


def test_registry_rejects_duplicates_and_unknown_names() -> None:
    registry = StrategyRegistry()
    registry.register("no-trade", NoTradeStrategy)

    with pytest.raises(ValueError, match="already registered"):
        registry.register("no-trade", NoTradeStrategy)
    with pytest.raises(KeyError, match="missing"):
        registry.get("missing")
    with pytest.raises(KeyError, match="missing"):
        registry.create("missing")


def test_strategy_signal_copies_serializable_evidence() -> None:
    values: list[EvidenceValue] = ["1", "2"]
    evidence: dict[str, EvidenceValue] = {"window": 20, "values": values}
    signal = StrategySignal(
        decision=SignalDecision.LONG,
        confidence=Decimal("0.75"),
        reason="confirmed setup",
        strategy_name="example",
        strategy_version="2.0.0",
        generated_at=BOUNDARY,
        evidence=evidence,
    )
    evidence["window"] = 99
    values.append("3")

    assert signal.evidence == {"window": 20, "values": ["1", "2"]}
    assert json.loads(json.dumps(signal.evidence)) == signal.evidence


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"confidence": Decimal("NaN")}, "confidence must be finite"),
        ({"confidence": Decimal("Infinity")}, "confidence must be finite"),
        ({"generated_at": BOUNDARY.replace(tzinfo=None)}, "generated_at must be UTC-aware"),
        ({"evidence": {"bad": object()}}, "evidence must be JSON-serializable"),
        ({"evidence": {"bad": float("nan")}}, "evidence must be JSON-serializable"),
    ],
)
def test_strategy_signal_rejects_invalid_values(changes: dict[str, object], match: str) -> None:
    values: dict[str, object] = {
        "decision": SignalDecision.HOLD,
        "confidence": Decimal("0"),
        "reason": "nothing to do",
        "strategy_name": "example",
        "strategy_version": "1.0.0",
        "generated_at": BOUNDARY,
        "evidence": {},
    }
    values.update(changes)

    with pytest.raises(ValueError, match=match):
        StrategySignal(**values)  # type: ignore[arg-type]
