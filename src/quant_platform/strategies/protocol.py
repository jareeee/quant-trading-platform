from typing import Protocol, runtime_checkable

from quant_platform.strategies.models import StrategyContext, StrategySignal


@runtime_checkable
class Strategy(Protocol):
    """Pluggable strategy boundary used by the trading engine."""

    def generate_signal(self, context: StrategyContext) -> StrategySignal: ...
