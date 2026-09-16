from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy.orm import Session

from quant_platform.db.models import Order, Signal, StrategyRun
from quant_platform.db.repositories import CreateOutcome, FillRepository, StrategyRunRepository
from quant_platform.exchange import Exchange, OrderRequest, OrderSide, OrderType
from quant_platform.strategies import SignalDecision, Strategy, StrategyContext
from quant_platform.trading.execution import validate_close_order
from quant_platform.trading.risk import (
    MarketConstraints,
    RiskLimits,
    validate_pre_trade,
)


class ExecutionStatus(StrEnum):
    SUCCESS = "success"
    DUPLICATE = "duplicate"
    RISK_REJECTED = "risk_rejected"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    exchange_config_id: int
    context: StrategyContext
    quantity: Decimal
    execution_price: Decimal
    available_balance: Decimal
    current_exposure: Decimal
    leverage: Decimal
    constraints: MarketConstraints
    limits: RiskLimits
    market_data_at: datetime
    taker_fee_rate: Decimal = Decimal("0")
    live_mode: bool = False


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    status: ExecutionStatus
    run_id: int
    order_id: int | None = None
    reason: str | None = None


class TradingEngine:
    """Synchronous strategy execution with durable boundary reservation."""

    def __init__(
        self,
        *,
        session: Session,
        exchange: Exchange,
        strategy: Strategy,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._exchange = exchange
        self._strategy = strategy
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        run_key = (
            f"strategy-run:{request.context.asset_id}:"
            f"{request.context.scheduled_boundary.isoformat()}"
        )
        created = StrategyRunRepository(self._session).create_for_boundary(
            asset_config_id=request.context.asset_id,
            scheduled_boundary=request.context.scheduled_boundary,
            strategy_name=type(self._strategy).__name__,
            idempotency_key=run_key,
            parameters=dict(request.context.parameters),
        )
        if created.outcome is CreateOutcome.DUPLICATE:
            run_id = created.run.id
            self._session.rollback()
            return ExecutionResult(ExecutionStatus.DUPLICATE, run_id)
        self._session.commit()

        try:
            return self._execute_reserved(request, created.run, run_key)
        except Exception:
            self._session.rollback()
            run = self._session.get_one(StrategyRun, created.run.id)
            run.status = "failed"
            run.ended_at = self._clock()
            self._session.commit()
            return ExecutionResult(
                ExecutionStatus.FAILED,
                run.id,
                reason="execution failed",
            )

    def _execute_reserved(
        self,
        request: ExecutionRequest,
        run: StrategyRun,
        run_key: str,
    ) -> ExecutionResult:
        strategy_signal = self._strategy.generate_signal(request.context)
        run.strategy_name = strategy_signal.strategy_name
        position = request.context.position
        if strategy_signal.decision is SignalDecision.HOLD:
            signal_quantity = Decimal("0")
        elif strategy_signal.decision is SignalDecision.CLOSE:
            signal_quantity = Decimal("0") if position is None else abs(position.quantity)
        else:
            signal_quantity = request.quantity
        persisted_signal = Signal(
            strategy_run_id=run.id,
            asset_config_id=request.context.asset_id,
            side=strategy_signal.decision.value,
            quantity=signal_quantity,
            price=None,
            idempotency_key=f"{run_key}:signal",
            signal_at=strategy_signal.generated_at,
            payload={
                "confidence": str(strategy_signal.confidence),
                "reason": strategy_signal.reason,
                "strategy_name": strategy_signal.strategy_name,
                "strategy_version": strategy_signal.strategy_version,
                "evidence": strategy_signal.evidence,
            },
        )
        self._session.add(persisted_signal)
        if strategy_signal.decision is SignalDecision.HOLD:
            run.status = "completed"
            run.ended_at = self._clock()
            self._session.commit()
            return ExecutionResult(ExecutionStatus.SUCCESS, run.id)

        self._session.commit()
        validation_leverage = request.leverage
        if strategy_signal.decision is SignalDecision.CLOSE:
            if position is None or position.quantity == 0:
                return self._reject(run, "close requires an existing nonzero position")
            side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
            order_quantity = abs(position.quantity)
            validation_leverage = position.leverage
        else:
            side = (
                OrderSide.BUY
                if strategy_signal.decision is SignalDecision.LONG
                else OrderSide.SELL
            )
            order_quantity = request.quantity
        order_request = OrderRequest(
            symbol=request.context.symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=order_quantity,
            client_order_id=(
                f"strategy-order:{request.context.asset_id}:"
                f"{request.context.scheduled_boundary.isoformat()}"
            ),
        )
        if strategy_signal.decision is SignalDecision.CLOSE:
            risk = validate_close_order(
                order_request,
                execution_price=request.execution_price,
                leverage=validation_leverage,
                constraints=request.constraints,
                limits=request.limits,
                market_data_at=request.market_data_at,
                now=self._clock(),
            )
        else:
            risk = validate_pre_trade(
                order_request,
                execution_price=request.execution_price,
                available_balance=request.available_balance,
                current_exposure=request.current_exposure,
                leverage=request.leverage,
                constraints=request.constraints,
                limits=request.limits,
                market_data_at=request.market_data_at,
                now=self._clock(),
                taker_fee_rate=request.taker_fee_rate,
                live_mode=request.live_mode,
            )
        if not risk.approved:
            return self._reject(run, "; ".join(risk.reasons))

        exchange_order = self._exchange.submit_order(order_request)
        exchange_fills = self._exchange.fetch_order_fills(
            exchange_order.exchange_order_id, request.context.symbol
        )
        if not exchange_fills:
            raise RuntimeError("exchange returned no authoritative fills")
        if any(
            fill.exchange_order_id != exchange_order.exchange_order_id
            or fill.symbol != exchange_order.symbol
            or fill.side is not exchange_order.side
            for fill in exchange_fills
        ):
            raise RuntimeError("authoritative fill identity does not match order")
        fill_quantity = sum((fill.quantity for fill in exchange_fills), Decimal("0"))
        if fill_quantity != exchange_order.filled_quantity:
            raise RuntimeError("authoritative fills do not match order filled quantity")

        order = Order(
            exchange_config_id=request.exchange_config_id,
            strategy_run_id=run.id,
            signal_id=persisted_signal.id,
            client_order_id=exchange_order.client_order_id,
            exchange_order_id=exchange_order.exchange_order_id,
            symbol=exchange_order.symbol,
            side=exchange_order.side.value,
            order_type=exchange_order.order_type.value,
            status=exchange_order.status.value,
            quantity=exchange_order.quantity,
            price=None,
            filled_quantity=exchange_order.filled_quantity,
            submitted_at=exchange_order.submitted_at,
        )
        self._session.add(order)
        self._session.flush()
        fill_repository = FillRepository(self._session)
        for fill in exchange_fills:
            fill_repository.append(
                order_id=order.id,
                exchange_fill_id=fill.exchange_fill_id,
                quantity=fill.quantity,
                price=fill.price,
                fee_amount=fill.fee,
                fee_currency=fill.fee_currency,
                executed_at=fill.executed_at,
            )
        run.status = "completed"
        run.ended_at = self._clock()
        self._session.commit()
        return ExecutionResult(ExecutionStatus.SUCCESS, run.id, order.id)

    def _reject(self, run: StrategyRun, reason: str) -> ExecutionResult:
        run.status = "rejected"
        run.ended_at = self._clock()
        self._session.commit()
        return ExecutionResult(ExecutionStatus.RISK_REJECTED, run.id, reason=reason)
