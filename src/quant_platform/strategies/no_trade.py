from decimal import Decimal

from quant_platform.strategies.models import (
    SignalDecision,
    StrategyContext,
    StrategySignal,
)


class NoTradeStrategy:
    """Built-in plumbing strategy that never requests a trade."""

    def generate_signal(self, context: StrategyContext) -> StrategySignal:
        return StrategySignal(
            decision=SignalDecision.HOLD,
            confidence=Decimal("0"),
            reason="No-trade strategy always holds",
            strategy_name="no-trade",
            strategy_version="1.0.0",
            generated_at=context.scheduled_boundary,
            evidence={},
        )
