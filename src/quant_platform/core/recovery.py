from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from quant_platform.db.models import StrategyRun


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    grace_window: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.grace_window, timedelta):
            raise TypeError("grace_window must be a timedelta")
        if self.grace_window <= timedelta(0):
            raise ValueError("grace_window must be positive")


@dataclass(frozen=True, slots=True)
class RecoverySchedule:
    asset_config_id: int
    interval: timedelta
    anchor: datetime
    enabled: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.asset_config_id, bool) or not isinstance(self.asset_config_id, int):
            raise TypeError("asset_config_id must be an integer")
        if self.asset_config_id <= 0:
            raise ValueError("asset_config_id must be positive")
        if not isinstance(self.interval, timedelta):
            raise TypeError("interval must be a timedelta")
        if self.interval <= timedelta(0):
            raise ValueError("interval must be positive")
        if not isinstance(self.anchor, datetime):
            raise TypeError("anchor must be a datetime")
        if self.anchor.tzinfo is None or self.anchor.utcoffset() != timedelta(0):
            raise ValueError("anchor must be UTC-aware")
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a boolean")


class RecoveryStatus(StrEnum):
    NO_ACTION = "no_action"
    CAUGHT_UP = "caught_up"
    ALREADY_HANDLED = "already_handled"
    STALE_SKIPPED = "stale_skipped"
    MULTIPLE_MISSED_SKIPPED = "multiple_missed_skipped"
    BLOCKED_RECONCILIATION = "blocked_reconciliation"
    DISPATCH_FAILED = "dispatch_failed"


@dataclass(frozen=True, slots=True)
class AssetRecoveryResult:
    asset_config_id: int
    status: RecoveryStatus
    boundary: datetime | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    last_active_at: datetime
    recovered_at: datetime
    results: tuple[AssetRecoveryResult, ...]


ReconcileCallback = Callable[[int], object]
DispatchCallback = Callable[[int, datetime], object]


class RecoveryCoordinator:
    def __init__(
        self,
        *,
        session: Session,
        policy: RecoveryPolicy,
        reconcile: ReconcileCallback,
        dispatch: DispatchCallback,
    ) -> None:
        self._session = session
        self._policy = policy
        self._reconcile = reconcile
        self._dispatch = dispatch

    def recover(
        self,
        *,
        last_active_at: datetime,
        now: datetime,
        schedules: Iterable[RecoverySchedule],
    ) -> RecoveryReport:
        self._validate_clock("last_active_at", last_active_at)
        self._validate_clock("now", now)
        if last_active_at > now:
            raise ValueError("last_active_at must not be after now")
        ordered = tuple(sorted(schedules, key=lambda schedule: schedule.asset_config_id))
        asset_ids = [schedule.asset_config_id for schedule in ordered]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("asset_config_id must be unique")
        results: list[AssetRecoveryResult] = []
        for schedule in ordered:
            if not schedule.enabled or now < schedule.anchor:
                continue
            if last_active_at < schedule.anchor:
                first_boundary = schedule.anchor
            else:
                elapsed = (last_active_at - schedule.anchor) // schedule.interval
                first_boundary = schedule.anchor + (elapsed + 1) * schedule.interval
            if first_boundary > now:
                results.append(
                    AssetRecoveryResult(schedule.asset_config_id, RecoveryStatus.NO_ACTION)
                )
                continue
            next_boundary = first_boundary + schedule.interval
            if next_boundary <= now:
                results.append(
                    AssetRecoveryResult(
                        schedule.asset_config_id,
                        RecoveryStatus.MULTIPLE_MISSED_SKIPPED,
                        first_boundary,
                    )
                )
                continue
            if now - first_boundary > self._policy.grace_window:
                results.append(
                    AssetRecoveryResult(
                        schedule.asset_config_id,
                        RecoveryStatus.STALE_SKIPPED,
                        first_boundary,
                    )
                )
                continue
            existing = self._strategy_run(schedule.asset_config_id, first_boundary)
            if existing is not None:
                results.append(
                    AssetRecoveryResult(
                        schedule.asset_config_id,
                        RecoveryStatus.ALREADY_HANDLED,
                        first_boundary,
                    )
                )
                continue
            try:
                reconciliation = self._reconcile(schedule.asset_config_id)
                if reconciliation is False or getattr(reconciliation, "success", True) is False:
                    raise RuntimeError("reconciliation callback reported failure")
            except Exception:
                self._session.rollback()
                results.append(
                    AssetRecoveryResult(
                        schedule.asset_config_id,
                        RecoveryStatus.BLOCKED_RECONCILIATION,
                        first_boundary,
                        "reconciliation failed",
                    )
                )
                continue
            try:
                self._dispatch(schedule.asset_config_id, first_boundary)
                self._session.flush()
                persisted = self._strategy_run(schedule.asset_config_id, first_boundary)
            except Exception:
                self._session.rollback()
                results.append(
                    AssetRecoveryResult(
                        schedule.asset_config_id,
                        RecoveryStatus.DISPATCH_FAILED,
                        first_boundary,
                        "dispatch failed",
                    )
                )
                continue
            if persisted is None:
                self._session.rollback()
                results.append(
                    AssetRecoveryResult(
                        schedule.asset_config_id,
                        RecoveryStatus.DISPATCH_FAILED,
                        first_boundary,
                        "dispatch failed",
                    )
                )
                continue
            self._session.commit()
            results.append(
                AssetRecoveryResult(
                    schedule.asset_config_id,
                    RecoveryStatus.CAUGHT_UP,
                    first_boundary,
                )
            )
        return RecoveryReport(last_active_at, now, tuple(results))

    @staticmethod
    def _validate_clock(name: str, value: datetime) -> None:
        if not isinstance(value, datetime):
            raise TypeError(f"{name} must be a datetime")
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError(f"{name} must be UTC-aware")

    def _strategy_run(self, asset_config_id: int, boundary: datetime) -> StrategyRun | None:
        return self._session.scalar(
            select(StrategyRun).where(
                StrategyRun.asset_config_id == asset_config_id,
                StrategyRun.scheduled_boundary == boundary,
            )
        )
