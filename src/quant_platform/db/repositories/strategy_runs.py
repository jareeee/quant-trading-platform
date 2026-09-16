from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from quant_platform.db.models import StrategyRun
from quant_platform.db.repositories.outcomes import CreateOutcome


@dataclass(frozen=True)
class StrategyRunCreateResult:
    outcome: CreateOutcome
    run: StrategyRun


class StrategyRunRepository:
    """Atomically starts strategy runs at unique asset boundaries."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_for_boundary(
        self,
        *,
        asset_config_id: int,
        scheduled_boundary: datetime,
        strategy_name: str,
        idempotency_key: str,
        parameters: dict[str, Any],
    ) -> StrategyRunCreateResult:
        statement = (
            insert(StrategyRun)
            .values(
                asset_config_id=asset_config_id,
                scheduled_boundary=scheduled_boundary,
                strategy_name=strategy_name,
                status="running",
                idempotency_key=idempotency_key,
                parameters=parameters,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    StrategyRun.asset_config_id,
                    StrategyRun.scheduled_boundary,
                ]
            )
            .returning(StrategyRun.id)
        )
        run_id = self._session.execute(statement).scalar_one_or_none()
        if run_id is not None:
            return StrategyRunCreateResult(
                outcome=CreateOutcome.CREATED,
                run=self._session.get_one(StrategyRun, run_id),
            )

        existing = self._session.scalars(
            select(StrategyRun).where(
                StrategyRun.asset_config_id == asset_config_id,
                StrategyRun.scheduled_boundary == scheduled_boundary,
            )
        ).one()
        return StrategyRunCreateResult(outcome=CreateOutcome.DUPLICATE, run=existing)
