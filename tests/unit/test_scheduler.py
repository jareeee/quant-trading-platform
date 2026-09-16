from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

import quant_platform.core as core
from quant_platform.core.scheduler import AssetSchedule, Scheduler

ANCHOR = datetime(2026, 1, 1, tzinfo=UTC)


def test_core_exports_scheduler_and_command_processor_contracts() -> None:
    assert core.AssetSchedule is AssetSchedule
    assert core.Scheduler is Scheduler
    assert all(
        (core.CommandEnvelope, core.CommandProcessor, core.ProcessResult, core.ProcessStatus)
    )


def test_asset_schedule_is_immutable_and_validated() -> None:
    schedule = AssetSchedule(
        asset_config_id=7,
        interval=timedelta(minutes=5),
        anchor=datetime(2026, 1, 1, tzinfo=UTC),
    )

    with pytest.raises(FrozenInstanceError):
        schedule.enabled = False  # type: ignore[misc]

    invalid_values = (
        {"asset_config_id": 0},
        {"interval": timedelta(0)},
        {"interval": timedelta(seconds=-1)},
        {"anchor": datetime(2026, 1, 1)},
        {"anchor": datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1)))},
        {"anchor": "2026-01-01"},
        {"enabled": 1},
    )
    for replacement in invalid_values:
        values = {
            "asset_config_id": 7,
            "interval": timedelta(minutes=5),
            "anchor": datetime(2026, 1, 1, tzinfo=UTC),
            "enabled": True,
        }
        values.update(replacement)
        with pytest.raises((TypeError, ValueError)):
            AssetSchedule(**values)  # type: ignore[arg-type]


def test_asset_schedule_rejects_non_integer_asset_id() -> None:
    with pytest.raises(TypeError, match="asset_config_id"):
        AssetSchedule(1.5, timedelta(minutes=1), ANCHOR)  # type: ignore[arg-type]


def test_tick_dispatches_latest_independent_boundaries_once() -> None:
    dispatched: list[tuple[int, datetime]] = []
    scheduler = Scheduler(
        schedules=(
            AssetSchedule(1, timedelta(minutes=5), ANCHOR),
            AssetSchedule(2, timedelta(minutes=7), ANCHOR),
            AssetSchedule(3, timedelta(minutes=1), ANCHOR, enabled=False),
        ),
        callback=lambda asset_id, boundary: dispatched.append((asset_id, boundary)),
    )

    now = ANCHOR + timedelta(minutes=16, seconds=30)
    scheduler.tick(now)
    scheduler.tick(now)

    assert dispatched == [
        (1, ANCHOR + timedelta(minutes=15)),
        (2, ANCHOR + timedelta(minutes=14)),
    ]


def test_callback_failure_leaves_boundary_available_for_retry() -> None:
    attempts: list[int] = []

    def fail_once(asset_id: int, boundary: datetime) -> None:
        assert boundary == ANCHOR
        attempts.append(asset_id)
        if asset_id == 1 and attempts.count(1) == 1:
            raise RuntimeError("temporary")

    scheduler = Scheduler(
        (
            AssetSchedule(1, timedelta(minutes=5), ANCHOR),
            AssetSchedule(2, timedelta(minutes=5), ANCHOR),
        ),
        fail_once,
    )

    with pytest.raises(ExceptionGroup, match="scheduler callbacks failed"):
        scheduler.tick(ANCHOR)
    scheduler.tick(ANCHOR)
    scheduler.tick(ANCHOR)

    assert attempts == [1, 2, 1]


def test_replacing_changed_schedule_resets_only_its_dedupe_state() -> None:
    dispatched: list[tuple[int, datetime]] = []
    first = AssetSchedule(1, timedelta(minutes=5), ANCHOR)
    unchanged = AssetSchedule(2, timedelta(minutes=5), ANCHOR)
    scheduler = Scheduler((first, unchanged), lambda *dispatch: dispatched.append(dispatch))
    now = ANCHOR + timedelta(minutes=10)
    scheduler.tick(now)

    scheduler.replace_schedules(
        (
            AssetSchedule(1, timedelta(minutes=2), ANCHOR),
            unchanged,
        )
    )
    scheduler.tick(now)

    assert dispatched == [(1, now), (2, now), (1, now)]


def test_tick_does_not_redispatch_an_older_boundary_after_clock_moves_backward() -> None:
    dispatched: list[tuple[int, datetime]] = []
    scheduler = Scheduler(
        (AssetSchedule(1, timedelta(minutes=5), ANCHOR),),
        lambda *dispatch: dispatched.append(dispatch),
    )

    newer = ANCHOR + timedelta(minutes=15)
    older = ANCHOR + timedelta(minutes=10)
    scheduler.tick(newer)
    scheduler.tick(older)

    assert dispatched == [(1, newer)]


def test_tick_rejects_naive_and_non_utc_values() -> None:
    scheduler = Scheduler((), lambda asset_id, boundary: None)

    with pytest.raises(ValueError, match="UTC-aware"):
        scheduler.tick(datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="UTC-aware"):
        scheduler.tick(datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1))))


def test_duplicate_asset_schedules_are_rejected() -> None:
    schedule = AssetSchedule(1, timedelta(minutes=5), ANCHOR)

    with pytest.raises(ValueError, match="unique"):
        Scheduler((schedule, schedule), lambda asset_id, boundary: None)
