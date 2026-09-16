from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from quant_platform.core.command_processor import (
    CommandEnvelope,
    CommandProcessor,
    ProcessStatus,
)
from quant_platform.db.base import Base
from quant_platform.db.models import Command
from quant_platform.db.repositories import CommandRepository, CreateOutcome
from quant_platform.db.session import create_engine, create_session_factory

REQUESTED_AT = datetime(2026, 9, 17, 8, tzinfo=UTC)
PROCESSED_AT = REQUESTED_AT + timedelta(seconds=5)


@pytest.fixture
def session_factory(tmp_path: Path) -> Iterator[Any]:
    engine = create_engine(f"sqlite:///{tmp_path / 'commands.db'}")
    Base.metadata.create_all(engine)
    yield create_session_factory(engine)
    engine.dispose()


def enqueue(
    session: Session,
    *,
    command_type: str = "start",
    key: str = "command-1",
    payload: dict[str, Any] | None = None,
    requested_at: datetime = REQUESTED_AT,
) -> Command:
    result = CommandRepository(session).enqueue(
        command_type=command_type,
        idempotency_key=key,
        payload=payload or {"asset_id": 7},
        requested_at=requested_at,
    )
    assert result.outcome is CreateOutcome.CREATED
    session.commit()
    return result.command


def test_process_next_commits_claim_before_handler_and_completes(session_factory: Any) -> None:
    with session_factory() as session:
        command = enqueue(session)
        handled: list[CommandEnvelope] = []

        def handle(envelope: CommandEnvelope) -> None:
            with session_factory() as competing_session:
                competing = CommandProcessor(competing_session, {}, clock=lambda: PROCESSED_AT)
                assert competing.process_next().status is ProcessStatus.EMPTY
            handled.append(envelope)

        processor = CommandProcessor(session, {"start": handle}, clock=lambda: PROCESSED_AT)
        result = processor.process_next()

        assert result.status is ProcessStatus.COMPLETED
        assert result.command == handled[0]
        assert result.command is not None
        assert result.command.id == command.id
        assert dict(result.command.payload) == {"asset_id": 7}

    with session_factory() as verification_session:
        stored = verification_session.scalars(select(Command).where(Command.id == command.id)).one()
        assert stored.status == "completed"
        assert stored.processed_at == PROCESSED_AT.replace(tzinfo=None)
        assert stored.error is None


def test_handler_database_failure_is_sanitized_and_session_recovers(session_factory: Any) -> None:
    with session_factory() as session:
        failed_command = enqueue(session, command_type="explode", key="first")
        enqueue(
            session,
            command_type="start",
            key="second",
            requested_at=REQUESTED_AT + timedelta(seconds=1),
        )
        handled: list[int] = []

        def poison_session(envelope: CommandEnvelope) -> None:
            session.add(
                Command(
                    command_type="duplicate",
                    status="pending",
                    idempotency_key=envelope.idempotency_key,
                    payload={"api_secret": "must-not-leak"},
                    requested_at=REQUESTED_AT,
                )
            )
            session.flush()

        processor = CommandProcessor(
            session,
            {"explode": poison_session, "start": lambda envelope: handled.append(envelope.id)},
            clock=lambda: PROCESSED_AT,
        )

        failed = processor.process_next()
        completed = processor.process_next()

        assert failed.status is ProcessStatus.FAILED
        assert failed.error == "command processing failed"
        assert completed.status is ProcessStatus.COMPLETED
        assert completed.command is not None
        assert handled == [completed.command.id]
        stored = session.get_one(Command, failed_command.id)
        assert stored.status == "failed"
        assert stored.error == "command processing failed"
        assert "secret" not in stored.error


def test_unknown_command_is_failed_without_exposing_payload(session_factory: Any) -> None:
    with session_factory() as session:
        command = enqueue(
            session,
            command_type="missing",
            payload={"password": "do-not-record"},
        )
        result = CommandProcessor(session, {}, clock=lambda: PROCESSED_AT).process_next()

        assert result.status is ProcessStatus.FAILED
        assert result.error == "command processing failed"
        stored = session.get_one(Command, command.id)
        assert stored.status == "failed"
        assert stored.error == "command processing failed"
        assert "do-not-record" not in stored.error


def test_handler_receives_deeply_isolated_immutable_payload(session_factory: Any) -> None:
    original = {"settings": {"symbols": ["BTC/USD"]}}
    with session_factory() as session:
        enqueue(session, payload=original)
        original["settings"]["symbols"].append("ETH/USD")

        def inspect(envelope: CommandEnvelope) -> None:
            assert tuple(envelope.payload["settings"]["symbols"]) == ("BTC/USD",)
            with pytest.raises(TypeError):
                envelope.payload["settings"]["new"] = True

        result = CommandProcessor(
            session,
            {"start": inspect},
            clock=lambda: PROCESSED_AT,
        ).process_next()

        assert result.status is ProcessStatus.COMPLETED


def test_stale_worker_cannot_overwrite_terminal_status(session_factory: Any) -> None:
    with session_factory() as session:
        command = enqueue(session)

        def supersede(envelope: CommandEnvelope) -> None:
            with session_factory() as competing_session:
                competing_session.execute(
                    update(Command)
                    .where(Command.id == envelope.id)
                    .values(status="failed", error="superseded", processed_at=PROCESSED_AT)
                )
                competing_session.commit()

        result = CommandProcessor(
            session,
            {"start": supersede},
            clock=lambda: PROCESSED_AT,
        ).process_next()

        assert result.status is ProcessStatus.CLAIM_LOST
        session.expire_all()
        stored = session.get_one(Command, command.id)
        assert stored.status == "failed"
        assert stored.error == "superseded"


def test_command_timestamps_must_be_utc(session_factory: Any) -> None:
    non_utc = datetime(2026, 9, 17, 9, tzinfo=timezone(timedelta(hours=1)))
    with session_factory() as session:
        repository = CommandRepository(session)
        for invalid in (datetime(2026, 9, 17, 8), non_utc):
            with pytest.raises(ValueError, match="UTC-aware"):
                repository.enqueue(
                    command_type="start",
                    idempotency_key=f"invalid-{invalid!r}",
                    payload={},
                    requested_at=invalid,
                )

        command = enqueue(session)
        processor = CommandProcessor(session, {}, clock=lambda: non_utc)
        with pytest.raises(ValueError, match="UTC-aware"):
            processor.process_next()
        session.expire_all()
        assert session.get_one(Command, command.id).status == "pending"
