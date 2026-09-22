import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from quant_platform.config import Settings, TradingMode
from quant_platform.core.command_processor import CommandEnvelope, CommandProcessor, ProcessStatus
from quant_platform.core.reconciliation import Reconciler
from quant_platform.core.scheduler import AssetSchedule, Scheduler
from quant_platform.db.models import AssetConfig
from quant_platform.db.session import create_engine, create_session_factory
from quant_platform.exchange import Candle, FakeExchange
from quant_platform.strategies import NoTradeStrategy, StrategyContext, StrategyRegistry
from quant_platform.trading import (
    ExecutionRequest,
    ExecutionStatus,
    MarketConstraints,
    RiskLimits,
    TradingEngine,
)

_TIMEFRAME = re.compile(r"^([1-9][0-9]*)([smhdw])$")
_TIMEFRAME_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_SECRET_PARTS = (
    "apikey",
    "credential",
    "passphrase",
    "password",
    "privatekey",
    "secret",
    "token",
)


def _contains_secret_key(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = "".join(character for character in str(key).lower() if character.isalnum())
            if any(part in normalized for part in _SECRET_PARTS) or _contains_secret_key(item):
                return True
    elif isinstance(value, list | tuple):
        return any(_contains_secret_key(item) for item in value)
    return False


def _require_finite_json(value: object) -> None:
    if value is None or isinstance(value, str | bool | int):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("parameters must contain finite JSON values")
    if isinstance(value, list):
        for item in value:
            _require_finite_json(item)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("parameters must contain finite JSON values")
        for item in value.values():
            _require_finite_json(item)
        return
    raise ValueError("parameters must contain finite JSON values")


class PaperAssetSettings(BaseModel):
    """Strict non-secret settings for deterministic paper execution."""

    model_config = ConfigDict(extra="forbid")

    timeframe: str
    strategy: str
    paper_open: Decimal
    paper_high: Decimal
    paper_low: Decimal
    paper_close: Decimal
    paper_volume: Decimal
    quantity: Decimal
    available_balance: Decimal
    current_exposure: Decimal
    leverage: Decimal
    minimum_quantity: Decimal
    maximum_quantity: Decimal
    quantity_step: Decimal
    minimum_notional: Decimal
    max_notional: Decimal
    max_position_quantity: Decimal
    max_data_age_seconds: int = Field(gt=0)
    balance_usage_fraction: Decimal
    taker_fee_rate: Decimal
    parameters: dict[str, Any]

    @model_validator(mode="before")
    @classmethod
    def reject_secret_like_configuration(cls, value: object) -> object:
        if _contains_secret_key(value):
            raise ValueError("paper settings must not contain secret-like keys")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        _require_finite_json(self.parameters)
        match = _TIMEFRAME.fullmatch(self.timeframe)
        if match is None:
            raise ValueError("timeframe must be a positive fixed interval such as 1m or 4h")
        if self.strategy != "no-trade":
            raise ValueError("only the trusted no-trade strategy is supported")
        positive = (
            "paper_open",
            "paper_high",
            "paper_low",
            "paper_close",
            "quantity",
            "available_balance",
            "leverage",
            "minimum_quantity",
            "maximum_quantity",
            "quantity_step",
            "minimum_notional",
            "max_notional",
            "max_position_quantity",
            "balance_usage_fraction",
        )
        nonnegative = ("paper_volume", "current_exposure", "taker_fee_rate")
        for field in positive:
            value = getattr(self, field)
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{field} must be positive and finite")
        for field in nonnegative:
            value = getattr(self, field)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{field} must be nonnegative and finite")
        if self.paper_high < max(self.paper_open, self.paper_low, self.paper_close):
            raise ValueError("paper_high is inconsistent with the paper candle")
        if self.paper_low > min(self.paper_open, self.paper_high, self.paper_close):
            raise ValueError("paper_low is inconsistent with the paper candle")
        if self.maximum_quantity < self.minimum_quantity:
            raise ValueError("maximum_quantity must be at least minimum_quantity")
        if self.balance_usage_fraction > 1:
            raise ValueError("balance_usage_fraction must not exceed 1")
        return self

    @property
    def interval_seconds(self) -> int:
        match = _TIMEFRAME.fullmatch(self.timeframe)
        assert match is not None
        amount, unit = match.groups()
        return int(amount) * _TIMEFRAME_SECONDS[unit]

    @property
    def interval(self) -> timedelta:
        return timedelta(seconds=self.interval_seconds)


class CoreConfigurationError(RuntimeError):
    """Raised when the paper core cannot start from safe persisted configuration."""


class CoreIterationDegraded(RuntimeError):
    """Raised to make CoreDaemon publish a degraded heartbeat."""


@dataclass(frozen=True, slots=True)
class CoreIterationReport:
    commands_completed: int
    commands_failed: int
    dispatched: int
    duplicates: int
    invalid_asset_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _ConfiguredAsset:
    id: int
    exchange_config_id: int
    symbol: str
    settings: PaperAssetSettings


class PaperCore:
    """Paper-only composition root with fresh sessions at each unit of work."""

    def __init__(
        self,
        *,
        engine: Engine,
        session_factory: Callable[[], Session],
        exchange: FakeExchange,
        clock: Callable[[], datetime],
    ) -> None:
        self._engine = engine
        self._session_factory = session_factory
        self.exchange = exchange
        self._clock = clock
        self._assets: dict[int, _ConfiguredAsset] = {}
        self._dispatched = 0
        self._duplicates = 0
        self._scheduler = Scheduler((), self._dispatch)
        registry = StrategyRegistry()
        registry.register("no-trade", NoTradeStrategy)
        self._strategies = registry

    @property
    def session_factory(self) -> Callable[[], Session]:
        return self._session_factory

    def close(self) -> None:
        self._engine.dispose()

    def iterate(self) -> CoreIterationReport:
        now = self._validated_now()
        commands_completed, commands_failed = self._process_commands()
        assets, invalid_asset_ids = self._load_assets()
        self._assets = {asset.id: asset for asset in assets}
        self._scheduler.replace_schedules(
            AssetSchedule(
                asset_config_id=asset.id,
                interval=asset.settings.interval,
                anchor=datetime(1970, 1, 1, tzinfo=UTC),
            )
            for asset in assets
        )
        before_dispatched = self._dispatched
        before_duplicates = self._duplicates
        self._scheduler.tick(now)
        return CoreIterationReport(
            commands_completed=commands_completed,
            commands_failed=commands_failed,
            dispatched=self._dispatched - before_dispatched,
            duplicates=self._duplicates - before_duplicates,
            invalid_asset_ids=invalid_asset_ids,
        )

    def daemon_iteration(self) -> None:
        report = self.iterate()
        if report.invalid_asset_ids or report.commands_failed:
            raise CoreIterationDegraded("core iteration completed with isolated failures")

    def _process_commands(self) -> tuple[int, int]:
        completed = 0
        failed = 0
        while True:
            with self._session_factory() as session:
                processor = CommandProcessor(
                    session,
                    {
                        "pause": lambda command: self._set_asset_enabled(
                            session, command, enabled=False
                        ),
                        "resume": lambda command: self._set_asset_enabled(
                            session, command, enabled=True
                        ),
                        "reconcile": lambda command: self._handle_reconcile(session, command),
                    },
                    clock=self._clock,
                )
                result = processor.process_next()
            if result.status is ProcessStatus.EMPTY:
                return completed, failed
            if result.status is ProcessStatus.COMPLETED:
                completed += 1
            else:
                failed += 1

    @staticmethod
    def _asset_id(command: CommandEnvelope) -> int:
        asset_id = command.payload.get("asset_id")
        if isinstance(asset_id, bool) or not isinstance(asset_id, int) or asset_id <= 0:
            raise ValueError("command requires a positive integer asset_id")
        return asset_id

    def _set_asset_enabled(
        self, session: Session, command: CommandEnvelope, *, enabled: bool
    ) -> None:
        asset = session.get(AssetConfig, self._asset_id(command))
        if asset is None:
            raise LookupError("asset does not exist")
        asset.enabled = enabled
        session.flush()

    def _handle_reconcile(self, session: Session, command: CommandEnvelope) -> None:
        asset_id = command.payload.get("asset_id")
        if asset_id is None:
            exchange_ids = tuple(
                session.scalars(
                    select(AssetConfig.exchange_config_id).distinct().order_by(
                        AssetConfig.exchange_config_id
                    )
                )
            )
        else:
            asset = session.get(AssetConfig, self._asset_id(command))
            if asset is None:
                raise LookupError("asset does not exist")
            exchange_ids = (asset.exchange_config_id,)
        for exchange_config_id in exchange_ids:
            Reconciler(session, self.exchange, exchange_config_id, clock=self._clock).reconcile()

    def _load_assets(self) -> tuple[tuple[_ConfiguredAsset, ...], tuple[int, ...]]:
        valid: list[_ConfiguredAsset] = []
        invalid: list[int] = []
        with self._session_factory() as session:
            rows = session.scalars(
                select(AssetConfig).where(AssetConfig.enabled.is_(True)).order_by(AssetConfig.id)
            ).all()
            for row in rows:
                try:
                    settings = PaperAssetSettings.model_validate(row.settings)
                except ValidationError:
                    invalid.append(row.id)
                    continue
                valid.append(
                    _ConfiguredAsset(
                        id=row.id,
                        exchange_config_id=row.exchange_config_id,
                        symbol=row.symbol,
                        settings=settings,
                    )
                )
        return tuple(valid), tuple(invalid)

    def _dispatch(self, asset_config_id: int, boundary: datetime) -> None:
        asset = self._assets[asset_config_id]
        settings = asset.settings
        candle = Candle(
            symbol=asset.symbol,
            timeframe=settings.timeframe,
            opened_at=boundary - settings.interval,
            open=settings.paper_open,
            high=settings.paper_high,
            low=settings.paper_low,
            close=settings.paper_close,
            volume=settings.paper_volume,
        )
        self.exchange.set_closed_candles(asset.symbol, settings.timeframe, (candle,))
        with self._session_factory() as reconciliation_session:
            report = Reconciler(
                reconciliation_session,
                self.exchange,
                asset.exchange_config_id,
                clock=self._clock,
            ).reconcile()
            if not report.success:
                raise RuntimeError("reconciliation failed")
        candles = self.exchange.fetch_closed_candles(asset.symbol, settings.timeframe, 1)
        positions = self.exchange.fetch_positions()
        position = next((item for item in positions if item.symbol == asset.symbol), None)
        context = StrategyContext(
            asset_id=asset.id,
            symbol=asset.symbol,
            timeframe=settings.timeframe,
            scheduled_boundary=boundary,
            candles=candles,
            position=position,
            parameters=settings.parameters,
        )
        request = ExecutionRequest(
            exchange_config_id=asset.exchange_config_id,
            context=context,
            quantity=settings.quantity,
            execution_price=settings.paper_close,
            available_balance=settings.available_balance,
            current_exposure=settings.current_exposure,
            leverage=settings.leverage,
            constraints=MarketConstraints(
                minimum_quantity=settings.minimum_quantity,
                maximum_quantity=settings.maximum_quantity,
                quantity_step=settings.quantity_step,
                minimum_notional=settings.minimum_notional,
            ),
            limits=RiskLimits(
                max_notional=settings.max_notional,
                max_leverage=settings.leverage,
                max_position_quantity=settings.max_position_quantity,
                max_data_age=timedelta(seconds=settings.max_data_age_seconds),
                balance_usage_fraction=settings.balance_usage_fraction,
            ),
            market_data_at=boundary,
            taker_fee_rate=settings.taker_fee_rate,
            live_mode=False,
        )
        with self._session_factory() as execution_session:
            result = TradingEngine(
                session=execution_session,
                exchange=self.exchange,
                strategy=self._strategies.create(settings.strategy),
                clock=self._clock,
            ).execute(request)
        if result.status is ExecutionStatus.DUPLICATE:
            self._duplicates += 1
        elif result.status is ExecutionStatus.SUCCESS:
            self._dispatched += 1
        else:
            raise RuntimeError("paper execution failed")

    def _validated_now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("clock must return a UTC-aware datetime")
        return now


def _validate_schema(engine: Engine) -> None:
    required = {
        "alembic_version",
        "asset_configs",
        "commands",
        "exchange_configs",
        "heartbeats",
        "positions",
        "signals",
        "strategy_runs",
    }
    tables = set(inspect(engine).get_table_names())
    if not required.issubset(tables):
        raise CoreConfigurationError("database schema is missing; run alembic upgrade head")
    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
    if revision != "0001_initial":
        raise CoreConfigurationError("database schema is not current; run alembic upgrade head")


def build_paper_core(
    *,
    settings: Settings,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    exchange_factory: Callable[[], FakeExchange] = FakeExchange,
) -> PaperCore:
    """Compose the core after enforcing mode and migration safety gates."""
    if settings.trading_mode is not TradingMode.PAPER:
        raise ValueError("Task 21 core supports paper mode only")
    database_url = make_url(settings.database_url)
    if database_url.drivername.startswith("sqlite"):
        database = database_url.database
        if (
            database is not None
            and database not in ("", ":memory:")
            and not Path(database).is_file()
        ):
            raise CoreConfigurationError("database schema is missing; run alembic upgrade head")
    engine = create_engine(settings.database_url)
    try:
        _validate_schema(engine)
        session_factory = create_session_factory(engine)
        exchange = exchange_factory()
        return PaperCore(
            engine=engine,
            session_factory=session_factory,
            exchange=exchange,
            clock=clock,
        )
    except BaseException:
        engine.dispose()
        raise
