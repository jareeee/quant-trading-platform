from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

import quant_platform.core as core
from quant_platform.core.recovery import (
    AssetRecoveryResult,
    RecoveryCoordinator,
    RecoveryPolicy,
    RecoveryReport,
    RecoverySchedule,
    RecoveryStatus,
)
from quant_platform.db.base import Base
from quant_platform.db.session import create_engine, create_session_factory

ANCHOR = datetime(2026, 1, 1, tzinfo=UTC)


def test_recovery_contracts_are_exported() -> None:
    assert core.AssetRecoveryResult is AssetRecoveryResult
    assert core.RecoveryCoordinator is RecoveryCoordinator
    assert core.RecoveryPolicy is RecoveryPolicy
    assert core.RecoveryReport is RecoveryReport
    assert core.RecoverySchedule is RecoverySchedule
    assert core.RecoveryStatus is RecoveryStatus


def test_recovery_policy_is_immutable_and_requires_positive_grace() -> None:
    policy = RecoveryPolicy(grace_window=timedelta(minutes=2))

    with pytest.raises(FrozenInstanceError):
        policy.grace_window = timedelta(minutes=3)  # type: ignore[misc]

    for invalid in (timedelta(0), timedelta(seconds=-1), "2 minutes"):
        with pytest.raises((TypeError, ValueError)):
            RecoveryPolicy(grace_window=invalid)  # type: ignore[arg-type]


def test_recovery_schedule_is_immutable_and_validated() -> None:
    schedule = RecoverySchedule(1, timedelta(minutes=5), ANCHOR)

    with pytest.raises(FrozenInstanceError):
        schedule.enabled = False  # type: ignore[misc]

    invalid_values = (
        {"asset_config_id": 0},
        {"asset_config_id": True},
        {"interval": timedelta(0)},
        {"interval": "5 minutes"},
        {"anchor": datetime(2026, 1, 1)},
        {"anchor": datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1)))},
        {"anchor": "2026-01-01"},
        {"enabled": 1},
    )
    for replacement in invalid_values:
        values = {
            "asset_config_id": 1,
            "interval": timedelta(minutes=5),
            "anchor": ANCHOR,
            "enabled": True,
        }
        values.update(replacement)
        with pytest.raises((TypeError, ValueError)):
            RecoverySchedule(**values)  # type: ignore[arg-type]


def test_recovery_status_values_are_stable() -> None:
    assert [status.value for status in RecoveryStatus] == [
        "no_action",
        "caught_up",
        "already_handled",
        "stale_skipped",
        "multiple_missed_skipped",
        "blocked_reconciliation",
        "dispatch_failed",
    ]


def test_no_crossed_boundaries_are_reported_in_asset_order_and_skips_are_omitted() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    reconciled: list[int] = []
    dispatched: list[tuple[int, datetime]] = []
    now = ANCHOR + timedelta(minutes=10)

    with session_factory() as session:
        coordinator = RecoveryCoordinator(
            session=session,
            policy=RecoveryPolicy(timedelta(minutes=2)),
            reconcile=lambda asset_id: reconciled.append(asset_id),
            dispatch=lambda asset_id, boundary: dispatched.append((asset_id, boundary)),
        )
        report = coordinator.recover(
            last_active_at=now,
            now=now,
            schedules=(
                RecoverySchedule(2, timedelta(minutes=5), ANCHOR),
                RecoverySchedule(4, timedelta(minutes=5), ANCHOR, enabled=False),
                RecoverySchedule(3, timedelta(minutes=5), now + timedelta(minutes=1)),
                RecoverySchedule(1, timedelta(minutes=5), ANCHOR),
            ),
        )

    engine.dispose()
    assert report == RecoveryReport(
        last_active_at=now,
        recovered_at=now,
        results=(
            AssetRecoveryResult(1, RecoveryStatus.NO_ACTION),
            AssetRecoveryResult(2, RecoveryStatus.NO_ACTION),
        ),
    )
    assert reconciled == []
    assert dispatched == []


def test_single_boundary_older_than_grace_is_skipped_without_callbacks() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    reconciled: list[int] = []
    dispatched: list[tuple[int, datetime]] = []
    boundary = ANCHOR + timedelta(minutes=5)

    with session_factory() as session:
        report = RecoveryCoordinator(
            session=session,
            policy=RecoveryPolicy(timedelta(seconds=30)),
            reconcile=lambda asset_id: reconciled.append(asset_id),
            dispatch=lambda asset_id, crossed: dispatched.append((asset_id, crossed)),
        ).recover(
            last_active_at=ANCHOR + timedelta(minutes=4),
            now=boundary + timedelta(minutes=1),
            schedules=(RecoverySchedule(1, timedelta(minutes=5), ANCHOR),),
        )

    engine.dispose()
    assert report.results == (
        AssetRecoveryResult(1, RecoveryStatus.STALE_SKIPPED, boundary),
    )
    assert reconciled == []
    assert dispatched == []


def test_multiple_crossed_boundaries_are_never_replayed() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    callbacks: list[tuple[str, int]] = []

    with session_factory() as session:
        report = RecoveryCoordinator(
            session=session,
            policy=RecoveryPolicy(timedelta(hours=1)),
            reconcile=lambda asset_id: callbacks.append(("reconcile", asset_id)),
            dispatch=lambda asset_id, boundary: callbacks.append(("dispatch", asset_id)),
        ).recover(
            last_active_at=ANCHOR + timedelta(minutes=4),
            now=ANCHOR + timedelta(minutes=16),
            schedules=(RecoverySchedule(1, timedelta(minutes=5), ANCHOR),),
        )

    engine.dispose()
    assert report.results == (
        AssetRecoveryResult(
            1,
            RecoveryStatus.MULTIPLE_MISSED_SKIPPED,
            ANCHOR + timedelta(minutes=5),
        ),
    )
    assert callbacks == []


def test_recover_rejects_invalid_clocks() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    valid = ANCHOR + timedelta(minutes=1)
    invalid_pairs = (
        (datetime(2026, 1, 1), valid),
        (valid, datetime(2026, 1, 1)),
        (datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1))), valid),
        (valid, datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1)))),
        (valid + timedelta(seconds=1), valid),
    )

    with session_factory() as session:
        recovery = RecoveryCoordinator(
            session=session,
            policy=RecoveryPolicy(timedelta(minutes=1)),
            reconcile=lambda asset_id: None,
            dispatch=lambda asset_id, boundary: None,
        )
        for last_active_at, now in invalid_pairs:
            with pytest.raises(ValueError):
                recovery.recover(last_active_at=last_active_at, now=now, schedules=())

    engine.dispose()


def test_recover_rejects_duplicate_asset_schedules_before_callbacks() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    schedule = RecoverySchedule(1, timedelta(minutes=5), ANCHOR)
    callbacks: list[int] = []

    with session_factory() as session:
        recovery = RecoveryCoordinator(
            session=session,
            policy=RecoveryPolicy(timedelta(minutes=1)),
            reconcile=lambda asset_id: callbacks.append(asset_id),
            dispatch=lambda asset_id, boundary: callbacks.append(asset_id),
        )
        with pytest.raises(ValueError, match="unique"):
            recovery.recover(
                last_active_at=ANCHOR,
                now=ANCHOR,
                schedules=(schedule, schedule),
            )

    engine.dispose()
    assert callbacks == []
