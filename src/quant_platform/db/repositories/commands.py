from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from quant_platform.db.models import Command
from quant_platform.db.repositories.outcomes import CreateOutcome


@dataclass(frozen=True)
class CommandEnqueueResult:
    outcome: CreateOutcome
    command: Command


class CommandRepository:
    """Idempotent FIFO command queue persistence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(
        self,
        *,
        command_type: str,
        idempotency_key: str,
        payload: dict[str, Any],
        requested_at: datetime,
    ) -> CommandEnqueueResult:
        statement = (
            insert(Command)
            .values(
                command_type=command_type,
                status="pending",
                idempotency_key=idempotency_key,
                payload=payload,
                requested_at=requested_at,
            )
            .on_conflict_do_nothing(index_elements=[Command.idempotency_key])
            .returning(Command.id)
        )
        command_id = self._session.execute(statement).scalar_one_or_none()
        if command_id is not None:
            return CommandEnqueueResult(
                outcome=CreateOutcome.CREATED,
                command=self._session.get_one(Command, command_id),
            )

        existing = self._session.scalars(
            select(Command).where(Command.idempotency_key == idempotency_key)
        ).one()
        return CommandEnqueueResult(outcome=CreateOutcome.DUPLICATE, command=existing)

    def claim_next(self) -> Command | None:
        next_pending_id = (
            select(Command.id)
            .where(Command.status == "pending")
            .order_by(Command.requested_at, Command.id)
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            update(Command)
            .where(Command.id == next_pending_id, Command.status == "pending")
            .values(status="processing")
            .returning(Command.id)
        )
        command_id = self._session.execute(statement).scalar_one_or_none()
        if command_id is None:
            return None
        command = self._session.get_one(Command, command_id)
        self._session.refresh(command)
        return command
