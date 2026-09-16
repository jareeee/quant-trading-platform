import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from quant_platform.db.models import Heartbeat


class HeartbeatStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass(frozen=True)
class HealthReport:
    service_name: str
    healthy: bool
    status: HeartbeatStatus | None
    last_seen_at: datetime | None
    checked_at: datetime
    age: timedelta | None
    details: Mapping[str, Any]
    reason: str | None


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


class HeartbeatService:
    """Persist and inspect the current lifecycle state of one service."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        service_name: str,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        if not service_name.strip():
            raise ValueError("service_name must not be blank")
        self._session_factory = session_factory
        self.service_name = service_name
        self._clock = clock

    def publish(
        self,
        status: HeartbeatStatus,
        details: Mapping[str, Any] | None = None,
    ) -> HeartbeatStatus:
        now = self._validated_now()
        copied_details = json.loads(json.dumps(dict(details or {})))
        statement = insert(Heartbeat).values(
            service_name=self.service_name,
            status=status.value,
            last_seen_at=now,
            details=copied_details,
            created_at=now,
            updated_at=now,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[Heartbeat.service_name],
            set_={
                "status": status.value,
                "last_seen_at": now,
                "details": copied_details,
                "updated_at": now,
            },
        )
        with self._session_factory() as session:
            session.execute(statement)
            session.commit()
        return status

    def health(self, max_age: timedelta) -> HealthReport:
        if max_age <= timedelta(0):
            raise ValueError("max_age must be positive")
        now = self._validated_now()
        with self._session_factory() as session:
            row = session.scalar(
                select(Heartbeat).where(Heartbeat.service_name == self.service_name)
            )
        if row is None:
            return HealthReport(
                service_name=self.service_name,
                healthy=False,
                status=None,
                last_seen_at=None,
                checked_at=now,
                age=None,
                details=MappingProxyType({}),
                reason="missing",
            )
        last_seen_at = row.last_seen_at
        if last_seen_at.tzinfo is None:
            last_seen_at = last_seen_at.replace(tzinfo=UTC)
        age = now - last_seen_at
        status = HeartbeatStatus(row.status)
        healthy = status is HeartbeatStatus.RUNNING and timedelta(0) <= age <= max_age
        if age < timedelta(0):
            reason = "future"
        elif status is not HeartbeatStatus.RUNNING:
            reason = status.value
        elif age > max_age:
            reason = "stale"
        else:
            reason = None
        return HealthReport(
            service_name=self.service_name,
            healthy=healthy,
            status=status,
            last_seen_at=last_seen_at,
            checked_at=now,
            age=age,
            details=_freeze(row.details),
            reason=reason,
        )

    def _validated_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("clock must return a UTC-aware datetime")
        return value
