from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import func, select

import quant_platform.core as core
from quant_platform.core.heartbeat import HealthReport, HeartbeatService, HeartbeatStatus
from quant_platform.db.base import Base
from quant_platform.db.models import Heartbeat
from quant_platform.db.session import create_engine, create_session_factory

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_core_exports_daemon_and_heartbeat_contracts() -> None:
    assert core.HeartbeatService is HeartbeatService
    assert core.HeartbeatStatus is HeartbeatStatus
    assert core.HealthReport is HealthReport
    assert all(
        (
            core.AlreadyRunningError,
            core.CoreDaemon,
            core.DaemonAlreadyRunError,
            core.DaemonState,
            core.InstanceLock,
        )
    )


def test_publish_atomically_upserts_and_copies_details(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'heartbeat.db'}")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    service = HeartbeatService(session_factory, "core", clock=lambda: NOW)
    details: dict[str, object] = {"workers": ["btc"]}

    service.publish(HeartbeatStatus.STARTING, details)
    details["workers"] = []
    with session_factory() as session:
        first_row = session.scalar(select(Heartbeat).where(Heartbeat.service_name == "core"))
        assert first_row is not None
        assert first_row.details == {"workers": ["btc"]}

    service.publish(HeartbeatStatus.RUNNING, {"iteration": 1})

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Heartbeat)) == 1
        row = session.scalar(select(Heartbeat).where(Heartbeat.service_name == "core"))
        assert row is not None
        assert row.status == "running"
        assert row.last_seen_at == NOW.replace(tzinfo=None)
        assert row.details == {"iteration": 1}
    engine.dispose()


def test_running_fresh_heartbeat_is_healthy_and_report_is_immutable(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'healthy.db'}")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    service = HeartbeatService(session_factory, "core", clock=lambda: NOW)
    service.publish(HeartbeatStatus.RUNNING, {"nested": ["safe"]})

    report = service.health(timedelta(seconds=30))

    assert report.healthy is True
    assert report.status is HeartbeatStatus.RUNNING
    assert report.last_seen_at == NOW
    assert report.age == timedelta(0)
    assert report.reason is None
    assert report.details == {"nested": ("safe",)}
    with pytest.raises(FrozenInstanceError):
        report.healthy = False  # type: ignore[misc]
    mutable_details = cast(Any, report.details)
    with pytest.raises(TypeError):
        mutable_details["changed"] = True
    engine.dispose()


def test_missing_heartbeat_fails_closed(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'missing.db'}")
    Base.metadata.create_all(engine)
    service = HeartbeatService(create_session_factory(engine), "core", clock=lambda: NOW)

    report = service.health(timedelta(seconds=30))

    assert report.healthy is False
    assert report.status is None
    assert report.last_seen_at is None
    assert report.age is None
    assert report.reason == "missing"
    engine.dispose()


def test_stale_running_heartbeat_fails_closed(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'stale.db'}")
    Base.metadata.create_all(engine)
    current = [NOW]
    service = HeartbeatService(create_session_factory(engine), "core", clock=lambda: current[0])
    service.publish(HeartbeatStatus.RUNNING)
    current[0] = NOW + timedelta(seconds=31)

    report = service.health(timedelta(seconds=30))

    assert report.healthy is False
    assert report.age == timedelta(seconds=31)
    assert report.reason == "stale"
    engine.dispose()


@pytest.mark.parametrize("status", [HeartbeatStatus.DEGRADED, HeartbeatStatus.STOPPED])
def test_non_running_heartbeat_fails_closed(tmp_path: Path, status: HeartbeatStatus) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / f'{status.value}.db'}")
    Base.metadata.create_all(engine)
    service = HeartbeatService(create_session_factory(engine), "core", clock=lambda: NOW)
    service.publish(status)

    report = service.health(timedelta(seconds=30))

    assert report.healthy is False
    assert report.status is status
    assert report.reason == status.value
    engine.dispose()


def test_health_rejects_non_positive_max_age(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'max-age.db'}")
    Base.metadata.create_all(engine)
    service = HeartbeatService(create_session_factory(engine), "core", clock=lambda: NOW)
    service.publish(HeartbeatStatus.RUNNING)

    with pytest.raises(ValueError, match="max_age must be positive"):
        service.health(timedelta(0))
    engine.dispose()


def test_clock_must_return_utc_aware_datetime(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'clock.db'}")
    Base.metadata.create_all(engine)
    service = HeartbeatService(
        create_session_factory(engine),
        "core",
        clock=lambda: datetime(2026, 1, 1),
    )

    with pytest.raises(ValueError, match="UTC-aware"):
        service.publish(HeartbeatStatus.RUNNING)
    engine.dispose()


def test_publish_rejects_non_json_safe_details_without_writing(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'json.db'}")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    service = HeartbeatService(session_factory, "core", clock=lambda: NOW)

    with pytest.raises(TypeError):
        service.publish(HeartbeatStatus.RUNNING, {"invalid": object()})
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Heartbeat)) == 0
    engine.dispose()


def test_future_heartbeat_fails_closed(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'future.db'}")
    Base.metadata.create_all(engine)
    current = [NOW + timedelta(seconds=1)]
    service = HeartbeatService(create_session_factory(engine), "core", clock=lambda: current[0])
    service.publish(HeartbeatStatus.RUNNING)
    current[0] = NOW

    report = service.health(timedelta(seconds=30))

    assert report.healthy is False
    assert report.reason == "future"
    engine.dispose()
