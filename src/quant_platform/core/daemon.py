import fcntl
import os
import signal
import threading
from collections.abc import Callable, Mapping
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from types import FrameType, TracebackType
from typing import Any, Protocol, cast

from quant_platform.core.heartbeat import HeartbeatStatus

_DEFAULT_SIGNAL_MODULE = cast("SignalModule", signal)


class AlreadyRunningError(RuntimeError):
    """Raised when another process holds the daemon instance lock."""


class InstanceLock:
    """A process-wide advisory lock held for this object's acquired lifetime."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(fd)
            message = f"daemon already running; lock held at {self.path}"
            raise AlreadyRunningError(message) from error
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode())
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


class HeartbeatPublisher(Protocol):
    def publish(
        self,
        status: HeartbeatStatus,
        details: Mapping[str, Any] | None = None,
    ) -> HeartbeatStatus: ...


class StopEvent(Protocol):
    def set(self) -> None: ...

    def is_set(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...


class SignalModule(Protocol):
    @property
    def SIGINT(self) -> int: ...

    @property
    def SIGTERM(self) -> int: ...

    def signal(self, signum: int, handler: Any) -> Any: ...


class DaemonState(StrEnum):
    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


class DaemonAlreadyRunError(RuntimeError):
    """Raised when a daemon object is run more than once."""


class CoreDaemon:
    """Run a callback loop under a single-instance lock and durable heartbeat."""

    def __init__(
        self,
        instance_lock: InstanceLock,
        heartbeat: HeartbeatPublisher,
        iteration: Callable[[], None],
        *,
        poll_interval: timedelta,
        stop_event: StopEvent | None = None,
        signal_module: SignalModule | None = _DEFAULT_SIGNAL_MODULE,
    ) -> None:
        if poll_interval <= timedelta(0):
            raise ValueError("poll_interval must be positive")
        self._instance_lock = instance_lock
        self._heartbeat = heartbeat
        self._iteration = iteration
        self._poll_interval = poll_interval.total_seconds()
        self._stop_event: StopEvent = stop_event if stop_event is not None else threading.Event()
        self._signal_module = signal_module
        self._state = DaemonState.NEW
        self._run_invoked = False

    @property
    def state(self) -> DaemonState:
        return self._state

    def request_stop(self) -> DaemonState:
        self._stop_event.set()
        return self._state

    def run(self) -> DaemonState:
        if self._run_invoked:
            raise DaemonAlreadyRunError("daemon objects may only be run once")
        self._run_invoked = True
        self._instance_lock.acquire()
        previous_handlers: dict[int, Any] = {}
        try:
            previous_handlers = self._install_signal_handlers()
            self._state = DaemonState.STARTING
            self._heartbeat.publish(HeartbeatStatus.STARTING)
            self._state = DaemonState.RUNNING
            self._heartbeat.publish(HeartbeatStatus.RUNNING)
            while not self._stop_event.is_set():
                try:
                    self._iteration()
                except Exception as error:
                    self._heartbeat.publish(
                        HeartbeatStatus.DEGRADED,
                        {"error": str(error), "error_type": type(error).__name__},
                    )
                else:
                    if not self._stop_event.is_set():
                        self._heartbeat.publish(HeartbeatStatus.RUNNING)
                if not self._stop_event.is_set():
                    self._stop_event.wait(self._poll_interval)
            self._state = DaemonState.STOPPING
            self._heartbeat.publish(HeartbeatStatus.STOPPING)
            self._state = DaemonState.STOPPED
            self._heartbeat.publish(HeartbeatStatus.STOPPED)
            return self._state
        finally:
            try:
                self._restore_signal_handlers(previous_handlers)
            finally:
                self._instance_lock.release()

    def _install_signal_handlers(self) -> dict[int, Any]:
        if self._signal_module is None or threading.current_thread() is not threading.main_thread():
            return {}
        previous: dict[int, Any] = {}
        try:
            for signum in (self._signal_module.SIGINT, self._signal_module.SIGTERM):
                previous[signum] = self._signal_module.signal(signum, self._handle_signal)
        except BaseException:
            self._restore_signal_handlers(previous)
            raise
        return previous

    def _restore_signal_handlers(self, previous: Mapping[int, Any]) -> None:
        if self._signal_module is None:
            return
        for signum, handler in previous.items():
            self._signal_module.signal(signum, handler)

    def _handle_signal(self, signum: int, frame: FrameType | None) -> None:
        del signum, frame
        self.request_stop()
