from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from quant_platform.db.models import Fill


class DuplicateFillError(ValueError):
    """Raised when an exchange fill was already appended for an order."""


class FillRepository:
    """Append-only persistence for exchange fills."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(
        self,
        *,
        order_id: int,
        exchange_fill_id: str,
        quantity: Decimal,
        price: Decimal,
        fee_amount: Decimal,
        fee_currency: str | None,
        executed_at: datetime,
    ) -> Fill:
        statement = (
            insert(Fill)
            .values(
                order_id=order_id,
                exchange_fill_id=exchange_fill_id,
                quantity=quantity,
                price=price,
                fee_amount=fee_amount,
                fee_currency=fee_currency,
                executed_at=executed_at,
            )
            .on_conflict_do_nothing(
                index_elements=[Fill.order_id, Fill.exchange_fill_id]
            )
            .returning(Fill.id)
        )
        fill_id = self._session.execute(statement).scalar_one_or_none()
        if fill_id is None:
            raise DuplicateFillError(
                f"fill {exchange_fill_id!r} already exists for order {order_id}"
            )
        return self._session.get_one(Fill, fill_id)

    def list_for_order(self, order_id: int) -> list[Fill]:
        statement = select(Fill).where(Fill.order_id == order_id).order_by(Fill.id)
        return list(self._session.scalars(statement))
