from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from sqlalchemy.orm import Session

from quant_platform.db.models import Command
from quant_platform.db.repositories.commands import CommandRepository

GENERIC_PROCESSING_ERROR = "command processing failed"


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class CommandEnvelope:
    id: int
    command_type: str
    idempotency_key: str
    payload: Mapping[str, Any]
    requested_at: datetime

    def __post_init__(self) -> None:
        if self.requested_at.tzinfo is None or self.requested_at.utcoffset() != timedelta(0):
            raise ValueError("requested_at must be UTC-aware")
        object.__setattr__(self, "payload", _freeze(deepcopy(dict(self.payload))))


class ProcessStatus(StrEnum):
    EMPTY = "empty"
    COMPLETED = "completed"
    FAILED = "failed"
    CLAIM_LOST = "claim_lost"


@dataclass(frozen=True)
class ProcessResult:
    status: ProcessStatus
    command: CommandEnvelope | None = None
    error: str | None = None


CommandHandler = Callable[[CommandEnvelope], None]


class CommandProcessor:
    def __init__(
        self,
        session: Session,
        handlers: Mapping[str, CommandHandler],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session = session
        self._repository = CommandRepository(session)
        self._handlers = dict(handlers)
        self._clock = clock

    def process_next(self) -> ProcessResult:
        self._validated_now()

        command = self._repository.claim_next()
        if command is None:
            self._session.commit()
            return ProcessResult(ProcessStatus.EMPTY)

        envelope = self._to_envelope(command)
        self._session.commit()
        handler = self._handlers.get(envelope.command_type)
        if handler is None:
            return self._fail(envelope, self._validated_now())

        try:
            handler(envelope)
        except Exception:
            return self._fail(envelope, self._validated_now())

        processed_at = self._validated_now()
        updated = self._repository.mark_completed(envelope.id, processed_at=processed_at)
        self._session.commit()
        status = ProcessStatus.COMPLETED if updated else ProcessStatus.CLAIM_LOST
        return ProcessResult(status, envelope)

    def _fail(self, envelope: CommandEnvelope, processed_at: datetime) -> ProcessResult:
        self._session.rollback()
        updated = self._repository.mark_failed(
            envelope.id,
            processed_at=processed_at,
            error=GENERIC_PROCESSING_ERROR,
        )
        self._session.commit()
        status = ProcessStatus.FAILED if updated else ProcessStatus.CLAIM_LOST
        error = GENERIC_PROCESSING_ERROR if updated else None
        return ProcessResult(status, envelope, error)

    def _validated_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("clock must return a UTC-aware datetime")
        return value

    @staticmethod
    def _to_envelope(command: Command) -> CommandEnvelope:
        requested_at = command.requested_at
        if requested_at.tzinfo is None:
            requested_at = requested_at.replace(tzinfo=UTC)
        return CommandEnvelope(
            id=command.id,
            command_type=command.command_type,
            idempotency_key=command.idempotency_key,
            payload=command.payload,
            requested_at=requested_at,
        )