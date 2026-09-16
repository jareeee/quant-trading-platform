import fcntl
import os
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from quant_platform.core.daemon import (
    AlreadyRunningError,
    CoreDaemon,
    DaemonAlreadyRunError,
    DaemonState,
    InstanceLock,
)
from quant_platform.core.heartbeat import HeartbeatStatus


class RecordingHeartbeat:
    def __init__(self) -> None:
        self.published: list[tuple[HeartbeatStatus, Mapping[str, Any] | None]] = []

    def publish(
        self,
        status: HeartbeatStatus,
        details: Mapping[str, Any] | None = None,
    ) -> HeartbeatStatus:
        self.published.append((status, details))
        return status


class FakeEvent:
    def __init__(self) -> None:
        self.stopped = False
        self.waits: list[float] = []

    def set(self) -> None:
        self.stopped = True

    def is_set(self) -> bool:
        return self.stopped

    def wait(self, timeout: float | None = None) -> bool:
        assert timeout is not None
        self.waits.append(timeout)
        return self.stopped


class FakeSignalModule:
    SIGINT = 2
    SIGTERM = 15

    def __init__(self) -> None:
        self.original_int = object()
        self.original_term = object()
        self.handlers: dict[int, Any] = {
            self.SIGINT: self.original_int,
            self.SIGTERM: self.original_term,
        }
        self.calls: list[tuple[int, Any]] = []

    def signal(self, signum: int, handler: Any) -> Any:
        previous = self.handlers[signum]
        self.handlers[signum] = handler
        self.calls.append((signum, handler))
        return previous


def test_second_instance_lock_is_refused(tmp_path: Path) -> None:
    lock_path = tmp_path / "nested" / "core.lock"
    first = InstanceLock(lock_path)
    second = InstanceLock(lock_path)

    first.acquire()
    try:
        with pytest.raises(AlreadyRunningError, match=str(lock_path)):
            second.acquire()
    finally:
        first.release()


def test_instance_lock_writes_pid_and_context_releases_without_deleting_file(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "core.lock"

    with InstanceLock(lock_path):
        assert lock_path.read_text() == str(os.getpid())

    assert lock_path.exists()
    with InstanceLock(lock_path):
        pass


def test_run_publishes_clean_lifecycle_and_releases_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "daemon.lock"
    heartbeat = RecordingHeartbeat()
    daemon: CoreDaemon

    def iteration() -> None:
        daemon.request_stop()

    daemon = CoreDaemon(
        InstanceLock(lock_path),
        heartbeat,
        iteration,
        poll_interval=timedelta(seconds=1),
        signal_module=None,
    )

    assert daemon.run() is DaemonState.STOPPED
    assert [status for status, _ in heartbeat.published] == [
        HeartbeatStatus.STARTING,
        HeartbeatStatus.RUNNING,
        HeartbeatStatus.STOPPING,
        HeartbeatStatus.STOPPED,
    ]
    with InstanceLock(lock_path):
        pass


@pytest.mark.parametrize("signal_name", ["SIGINT", "SIGTERM"])
def test_signal_requests_clean_stop_and_handlers_are_restored(
    tmp_path: Path, signal_name: str
) -> None:
    heartbeat = RecordingHeartbeat()
    signals = FakeSignalModule()
    published_inside_handler: list[int] = []

    def iteration() -> None:
        signum = getattr(signals, signal_name)
        handler = signals.handlers[signum]
        assert callable(handler)
        before = len(heartbeat.published)
        handler(signum, None)
        published_inside_handler.append(len(heartbeat.published) - before)

    daemon = CoreDaemon(
        InstanceLock(tmp_path / "signals.lock"),
        heartbeat,
        iteration,
        poll_interval=timedelta(seconds=1),
        signal_module=signals,
    )

    assert daemon.run() is DaemonState.STOPPED
    assert published_inside_handler == [0]
    assert signals.handlers == {
        signals.SIGINT: signals.original_int,
        signals.SIGTERM: signals.original_term,
    }
    assert [signum for signum, _ in signals.calls] == [2, 15, 2, 15]


def test_callback_failure_publishes_degraded_then_continues_without_lock_leak(
    tmp_path: Path,
) -> None:
    heartbeat = RecordingHeartbeat()
    event = FakeEvent()
    attempts = 0
    daemon: CoreDaemon

    def iteration() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("api_key=top-secret")
        daemon.request_stop()

    lock_path = tmp_path / "callback.lock"
    daemon = CoreDaemon(
        InstanceLock(lock_path),
        heartbeat,
        iteration,
        poll_interval=timedelta(seconds=3),
        stop_event=event,
        signal_module=None,
    )

    assert daemon.run() is DaemonState.STOPPED
    assert attempts == 2
    assert event.waits == [3.0]
    assert [status for status, _ in heartbeat.published] == [
        HeartbeatStatus.STARTING,
        HeartbeatStatus.RUNNING,
        HeartbeatStatus.DEGRADED,
        HeartbeatStatus.STOPPING,
        HeartbeatStatus.STOPPED,
    ]
    assert heartbeat.published[2][1] == {
        "error": "daemon iteration failed",
        "error_type": "RuntimeError",
    }
    with InstanceLock(lock_path):
        pass


def test_heartbeat_failure_releases_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "heartbeat-failure.lock"

    class FailingHeartbeat(RecordingHeartbeat):
        def publish(
            self,
            status: HeartbeatStatus,
            details: Mapping[str, Any] | None = None,
        ) -> HeartbeatStatus:
            raise RuntimeError("database unavailable")

    daemon = CoreDaemon(
        InstanceLock(lock_path),
        FailingHeartbeat(),
        lambda: None,
        poll_interval=timedelta(seconds=1),
        signal_module=None,
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        daemon.run()
    with InstanceLock(lock_path):
        pass


def test_second_run_on_same_daemon_is_rejected(tmp_path: Path) -> None:
    heartbeat = RecordingHeartbeat()
    daemon: CoreDaemon

    def stop() -> None:
        daemon.request_stop()
        daemon.request_stop()

    daemon = CoreDaemon(
        InstanceLock(tmp_path / "one-shot.lock"),
        heartbeat,
        stop,
        poll_interval=timedelta(seconds=1),
        signal_module=None,
    )
    daemon.run()

    with pytest.raises(RuntimeError, match="only be run once"):
        daemon.run()


def test_failed_lock_acquisition_still_consumes_daemon_run(tmp_path: Path) -> None:
    lock_path = tmp_path / "contended.lock"
    owner = InstanceLock(lock_path)
    owner.acquire()
    daemon: CoreDaemon

    def stop() -> None:
        daemon.request_stop()

    daemon = CoreDaemon(
        InstanceLock(lock_path),
        RecordingHeartbeat(),
        stop,
        poll_interval=timedelta(seconds=1),
        signal_module=None,
    )
    try:
        with pytest.raises(AlreadyRunningError):
            daemon.run()
    finally:
        owner.release()

    with pytest.raises(DaemonAlreadyRunError):
        daemon.run()


def test_instance_lock_closes_fd_when_pid_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "write-failure.lock"

    def fail_write(fd: int, data: bytes) -> int:
        del fd, data
        raise OSError("disk failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "write", fail_write)
        with pytest.raises(OSError, match="disk failure"):
            InstanceLock(lock_path).acquire()

    with InstanceLock(lock_path):
        pass


def test_signal_restoration_failure_does_not_leak_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "restore-failure.lock"
    heartbeat = RecordingHeartbeat()

    class RestoreFailingSignals(FakeSignalModule):
        def signal(self, signum: int, handler: Any) -> Any:
            if handler in (self.original_int, self.original_term):
                raise RuntimeError("restore failed")
            return super().signal(signum, handler)

    signals = RestoreFailingSignals()

    def iteration() -> None:
        handler = signals.handlers[signals.SIGTERM]
        assert callable(handler)
        handler(signals.SIGTERM, None)

    daemon = CoreDaemon(
        InstanceLock(lock_path),
        heartbeat,
        iteration,
        poll_interval=timedelta(seconds=1),
        signal_module=signals,
    )

    with pytest.raises(RuntimeError, match="restore failed"):
        daemon.run()
    with InstanceLock(lock_path):
        pass


def test_instance_lock_closes_fd_when_unlock_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "unlock-failure.lock"
    lock = InstanceLock(lock_path)
    lock.acquire()
    real_flock = fcntl.flock

    def fail_unlock(fd: int, operation: int) -> Any:
        if operation == fcntl.LOCK_UN:
            raise OSError("unlock failure")
        return real_flock(fd, operation)

    with monkeypatch.context() as patch:
        patch.setattr(fcntl, "flock", fail_unlock)
        with pytest.raises(OSError, match="unlock failure"):
            lock.release()

    lock.release()
    with InstanceLock(lock_path):
        pass
