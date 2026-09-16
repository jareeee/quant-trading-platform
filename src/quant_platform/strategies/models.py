import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import TypeAlias

from quant_platform.exchange import Candle, Position

ParameterValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | list["ParameterValue"]
    | tuple["ParameterValue", ...]
    | Mapping[str, "ParameterValue"]
)
EvidenceValue: TypeAlias = (
    str | int | float | bool | None | list["EvidenceValue"] | dict[str, "EvidenceValue"]
)
_TIMEFRAME = re.compile(r"^([1-9][0-9]*)([smhdw])$")
_TIMEFRAME_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be UTC-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must use UTC")


def _timeframe_duration(value: str) -> timedelta:
    match = _TIMEFRAME.fullmatch(value)
    if match is None:
        raise ValueError("timeframe must be a positive fixed interval such as 1m or 4h")
    amount, unit = match.groups()
    return timedelta(seconds=int(amount) * _TIMEFRAME_SECONDS[unit])


def _freeze(value: object) -> ParameterValue:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("parameters must contain only finite numbers")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("parameters keys must be strings")
        frozen = {key: _freeze(item) for key, item in value.items()}
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    raise ValueError("parameters must contain only JSON-compatible values")


@dataclass(frozen=True, slots=True)
class StrategyContext:
    asset_id: int
    symbol: str
    timeframe: str
    scheduled_boundary: datetime
    candles: tuple[Candle, ...]
    position: Position | None
    parameters: Mapping[str, ParameterValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "candles", tuple(self.candles))
        _require_utc(self.scheduled_boundary, "scheduled_boundary")
        duration = _timeframe_duration(self.timeframe)
        if not self.candles:
            raise ValueError("candles must not be empty")
        previous: datetime | None = None
        for candle in self.candles:
            if candle.symbol != self.symbol:
                raise ValueError("candle symbol must match context symbol")
            if candle.timeframe != self.timeframe:
                raise ValueError("candle timeframe must match context timeframe")
            _require_utc(candle.opened_at, "candle opened_at")
            if previous is not None and candle.opened_at <= previous:
                raise ValueError("candles must be sorted strictly ascending")
            if candle.opened_at + duration > self.scheduled_boundary:
                raise ValueError("candles must be fully closed before the scheduled boundary")
            previous = candle.opened_at
        if not isinstance(self.parameters, Mapping):
            raise ValueError("parameters must be a mapping")
        object.__setattr__(self, "parameters", _freeze(self.parameters))


class SignalDecision(StrEnum):
    LONG = "long"
    SHORT = "short"
    HOLD = "hold"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class StrategySignal:
    decision: SignalDecision
    confidence: Decimal
    reason: str
    strategy_name: str
    strategy_version: str
    generated_at: datetime
    evidence: dict[str, EvidenceValue]

    def __post_init__(self) -> None:
        if not self.confidence.is_finite():
            raise ValueError("confidence must be finite")
        for field in ("reason", "strategy_name", "strategy_version"):
            if not getattr(self, field).strip():
                raise ValueError(f"{field} must not be empty")
        _require_utc(self.generated_at, "generated_at")
        try:
            serialized = json.dumps(self.evidence, allow_nan=False)
            copied = json.loads(serialized)
        except (TypeError, ValueError) as error:
            raise ValueError("evidence must be JSON-serializable") from error
        if not isinstance(copied, dict):
            raise ValueError("evidence must be a JSON object")
        object.__setattr__(self, "evidence", copied)

    @property
    def score(self) -> Decimal:
        """Alias for the strategy's finite confidence score."""
        return self.confidence
