from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from quant_platform.core import (
    AssetRecoveryResult,
    RecoveryCoordinator,
    RecoveryPolicy,
    RecoverySchedule,
    RecoveryStatus,
)
from quant_platform.db.base import Base
from quant_platform.db.models import AssetConfig, ExchangeConfig, StrategyRun
from quant_platform.db.session import create_engine, create_session_factory

ANCHOR = datetime(2026, 1, 1, tzinfo=UTC)
BOUNDARY = ANCHOR + timedelta(minutes=5)
NOW = BOUNDARY + timedelta(seconds=30)


def seed_assets(session: Session, count: int = 1) -> tuple[AssetConfig, ...]:
    exchange = ExchangeConfig(name="primary", exchange="fake", options={})
    session.add(exchange)
    session.flush()
    assets = tuple(
        AssetConfig(
            exchange_config_id=exchange.id,
            symbol=f"ASSET-{index}/USD",
            base_asset=f"ASSET-{index}",
            quote_asset="USD",
            settings={},
        )
        for index in range(1, count + 1)
    )
    session.add_all(assets)
    session.commit()
    return assets


def add_run(session: Session, asset_id: int, boundary: datetime, status: str = "running") -> None:
    session.add(
        StrategyRun(
            asset_config_id=asset_id,
            strategy_name="recovery-test",
            status=status,
            idempotency_key=f"recovery:{asset_id}:{boundary.isoformat()}",
            parameters={},
            scheduled_boundary=boundary,
        )
    )


def coordinator(
    session: Session,
    *,
    reconcile: Callable[[int], object],
    dispatch: Callable[[int, datetime], object],
) -> RecoveryCoordinator:
    return RecoveryCoordinator(
        session=session,
        policy=RecoveryPolicy(timedelta(minutes=1)),
        reconcile=reconcile,
        dispatch=dispatch,
    )


def test_catch_up_is_durable_and_restart_reports_already_handled(tmp_path: Path) -> None:
    database_path = tmp_path / "recovery.db"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    calls: list[tuple[str, int, datetime | None]] = []

    with session_factory() as session:
        (asset,) = seed_assets(session)
        asset_id = asset.id

        def reconcile(current_asset_id: int) -> None:
            calls.append(("reconcile", current_asset_id, None))

        def dispatch(current_asset_id: int, boundary: datetime) -> None:
            calls.append(("dispatch", current_asset_id, boundary))
            add_run(session, current_asset_id, boundary)

        first = coordinator(session, reconcile=reconcile, dispatch=dispatch).recover(
            last_active_at=ANCHOR + timedelta(minutes=4),
            now=NOW,
            schedules=(RecoverySchedule(asset_id, timedelta(minutes=5), ANCHOR),),
        )

    assert first.results == (
        AssetRecoveryResult(asset_id, RecoveryStatus.CAUGHT_UP, BOUNDARY),
    )
    assert calls == [
        ("reconcile", asset_id, None),
        ("dispatch", asset_id, BOUNDARY),
    ]

    with session_factory() as restarted_session:
        second = coordinator(
            restarted_session,
            reconcile=lambda asset_id: calls.append(("unexpected-reconcile", asset_id, None)),
            dispatch=lambda asset_id, boundary: calls.append(
                ("unexpected-dispatch", asset_id, boundary)
            ),
        ).recover(
            last_active_at=ANCHOR + timedelta(minutes=4),
            now=NOW,
            schedules=(RecoverySchedule(asset_id, timedelta(minutes=5), ANCHOR),),
        )
        persisted = restarted_session.query(StrategyRun).all()

    engine.dispose()
    assert second.results == (
        AssetRecoveryResult(asset_id, RecoveryStatus.ALREADY_HANDLED, BOUNDARY),
    )
    assert len(persisted) == 1
    assert persisted[0].scheduled_boundary.replace(tzinfo=UTC) == BOUNDARY
    assert calls == [
        ("reconcile", asset_id, None),
        ("dispatch", asset_id, BOUNDARY),
    ]


def test_reconciliation_failure_blocks_only_that_asset_and_sanitizes_error(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'independent.db'}")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    calls: list[tuple[str, int]] = []

    with session_factory() as session:
        first, second = seed_assets(session, count=2)
        first_id, second_id = first.id, second.id

        def reconcile(asset_id: int) -> None:
            calls.append(("reconcile", asset_id))
            if asset_id == first_id:
                raise RuntimeError("api_key=top-secret")

        def dispatch(asset_id: int, boundary: datetime) -> None:
            calls.append(("dispatch", asset_id))
            add_run(session, asset_id, boundary, status="completed")

        report = coordinator(session, reconcile=reconcile, dispatch=dispatch).recover(
            last_active_at=ANCHOR + timedelta(minutes=4),
            now=NOW,
            schedules=(
                RecoverySchedule(second_id, timedelta(minutes=5), ANCHOR),
                RecoverySchedule(first_id, timedelta(minutes=5), ANCHOR),
            ),
        )

    engine.dispose()
    assert report.results == (
        AssetRecoveryResult(
            first_id,
            RecoveryStatus.BLOCKED_RECONCILIATION,
            BOUNDARY,
            "reconciliation failed",
        ),
        AssetRecoveryResult(second_id, RecoveryStatus.CAUGHT_UP, BOUNDARY),
    )
    assert "secret" not in (report.results[0].error or "")
    assert calls == [
        ("reconcile", first_id),
        ("reconcile", second_id),
        ("dispatch", second_id),
    ]


def test_dispatch_failure_is_sanitized_and_boundary_remains_retryable(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'retry.db'}")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    attempts = 0
    reconciliations = 0

    with session_factory() as session:
        (asset,) = seed_assets(session)
        asset_id = asset.id

        def reconcile(current_asset_id: int) -> None:
            nonlocal reconciliations
            assert current_asset_id == asset_id
            reconciliations += 1

        def dispatch(current_asset_id: int, boundary: datetime) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("token=dispatch-secret")
            add_run(session, current_asset_id, boundary, status="completed")

        recovery = coordinator(session, reconcile=reconcile, dispatch=dispatch)
        failed = recovery.recover(
            last_active_at=ANCHOR + timedelta(minutes=4),
            now=NOW,
            schedules=(RecoverySchedule(asset_id, timedelta(minutes=5), ANCHOR),),
        )
        retried = recovery.recover(
            last_active_at=ANCHOR + timedelta(minutes=4),
            now=NOW,
            schedules=(RecoverySchedule(asset_id, timedelta(minutes=5), ANCHOR),),
        )

    engine.dispose()
    assert failed.results == (
        AssetRecoveryResult(
            asset_id,
            RecoveryStatus.DISPATCH_FAILED,
            BOUNDARY,
            "dispatch failed",
        ),
    )
    assert "secret" not in (failed.results[0].error or "")
    assert retried.results == (
        AssetRecoveryResult(asset_id, RecoveryStatus.CAUGHT_UP, BOUNDARY),
    )
    assert attempts == 2
    assert reconciliations == 2


def test_unsuccessful_reconciliation_result_blocks_dispatch(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'reconcile-result.db'}")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    dispatched: list[int] = []

    with session_factory() as session:
        (asset,) = seed_assets(session)
        asset_id = asset.id
        report = coordinator(
            session,
            reconcile=lambda current_asset_id: False,
            dispatch=lambda current_asset_id, boundary: dispatched.append(current_asset_id),
        ).recover(
            last_active_at=ANCHOR + timedelta(minutes=4),
            now=NOW,
            schedules=(RecoverySchedule(asset_id, timedelta(minutes=5), ANCHOR),),
        )

    engine.dispose()
    assert report.results == (
        AssetRecoveryResult(
            asset_id,
            RecoveryStatus.BLOCKED_RECONCILIATION,
            BOUNDARY,
            "reconciliation failed",
        ),
    )
    assert dispatched == []


def test_dispatch_without_durable_reservation_fails_closed_and_retries(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'missing-reservation.db'}")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    dispatches = 0

    with session_factory() as session:
        (asset,) = seed_assets(session)
        asset_id = asset.id

        def dispatch(current_asset_id: int, boundary: datetime) -> None:
            nonlocal dispatches
            dispatches += 1

        recovery = coordinator(session, reconcile=lambda asset_id: None, dispatch=dispatch)
        first = recovery.recover(
            last_active_at=ANCHOR + timedelta(minutes=4),
            now=NOW,
            schedules=(RecoverySchedule(asset_id, timedelta(minutes=5), ANCHOR),),
        )
        second = recovery.recover(
            last_active_at=ANCHOR + timedelta(minutes=4),
            now=NOW,
            schedules=(RecoverySchedule(asset_id, timedelta(minutes=5), ANCHOR),),
        )

    engine.dispose()
    expected = (
        AssetRecoveryResult(
            asset_id,
            RecoveryStatus.DISPATCH_FAILED,
            BOUNDARY,
            "dispatch failed",
        ),
    )
    assert first.results == expected
    assert second.results == expected
    assert dispatches == 2


@pytest.mark.parametrize("run_status", ["failed", "completed"])
def test_existing_terminal_run_prevents_callbacks(tmp_path: Path, run_status: str) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / f'existing-{run_status}.db'}")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    callbacks: list[str] = []

    with session_factory() as session:
        (asset,) = seed_assets(session)
        asset_id = asset.id
        add_run(session, asset_id, BOUNDARY, status=run_status)
        session.commit()

        report = coordinator(
            session,
            reconcile=lambda asset_id: callbacks.append("reconcile"),
            dispatch=lambda asset_id, boundary: callbacks.append("dispatch"),
        ).recover(
            last_active_at=ANCHOR + timedelta(minutes=4),
            now=NOW,
            schedules=(RecoverySchedule(asset_id, timedelta(minutes=5), ANCHOR),),
        )

    engine.dispose()
    assert report.results == (
        AssetRecoveryResult(asset_id, RecoveryStatus.ALREADY_HANDLED, BOUNDARY),
    )
    assert callbacks == []
