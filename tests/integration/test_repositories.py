from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from quant_platform.db.base import Base
from quant_platform.db.models import ExchangeConfig, Order
from quant_platform.db.repositories import (
    AssetConfigRepository,
    CommandRepository,
    CreateOutcome,
    DuplicateFillError,
    FillRepository,
    StrategyRunRepository,
)
from quant_platform.db.session import create_engine, create_session_factory


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'repositories.db'}")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as database_session:
        yield database_session
    engine.dispose()


def add_exchange(session: Session, *, name: str = "primary") -> ExchangeConfig:
    exchange = ExchangeConfig(name=name, exchange="kraken", options={})
    session.add(exchange)
    session.commit()
    return exchange


def add_asset(session: Session) -> int:
    exchange = add_exchange(session)
    repository = AssetConfigRepository(session)
    return repository.create(
        exchange_config_id=exchange.id,
        symbol="BTC/USD",
        base_asset="BTC",
        quote_asset="USD",
    ).id


def add_order(session: Session) -> Order:
    exchange = add_exchange(session)
    order = Order(
        exchange_config_id=exchange.id,
        client_order_id="client-order-1",
        symbol="BTC/USD",
        side="buy",
        order_type="market",
        status="open",
        quantity=Decimal("2"),
    )
    session.add(order)
    session.flush()
    return order


def test_asset_config_repository_crud_and_deterministic_list(session: Session) -> None:
    exchange = add_exchange(session)
    repository = AssetConfigRepository(session)

    eth = repository.create(
        exchange_config_id=exchange.id,
        symbol="ETH/USD",
        base_asset="ETH",
        quote_asset="USD",
        enabled=True,
        settings={"timeframe": "1h"},
    )
    btc = repository.create(
        exchange_config_id=exchange.id,
        symbol="BTC/USD",
        base_asset="BTC",
        quote_asset="USD",
        enabled=False,
        settings={},
    )

    assert repository.get(eth.id) is eth
    assert [config.id for config in repository.list()] == [eth.id, btc.id]
    assert [config.id for config in repository.list(enabled=True)] == [eth.id]

    updated = repository.update(
        eth.id,
        symbol="ETH/USDT",
        base_asset="WETH",
        quote_asset="USDT",
        enabled=False,
        settings={"timeframe": "4h"},
    )
    assert updated.symbol == "ETH/USDT"
    assert updated.base_asset == "WETH"
    assert updated.quote_asset == "USDT"
    assert updated.enabled is False
    assert updated.settings == {"timeframe": "4h"}

    assert repository.delete(btc.id) is True
    assert repository.get(btc.id) is None
    assert repository.delete(btc.id) is False


def test_strategy_run_duplicate_boundary_is_explicit_and_session_recovers(
    session: Session,
) -> None:
    asset_config_id = add_asset(session)
    repository = StrategyRunRepository(session)
    boundary = datetime(2026, 9, 16, 10, tzinfo=UTC)

    created = repository.create_for_boundary(
        asset_config_id=asset_config_id,
        scheduled_boundary=boundary,
        strategy_name="momentum",
        idempotency_key="run-1",
        parameters={"lookback": 20},
    )
    duplicate = repository.create_for_boundary(
        asset_config_id=asset_config_id,
        scheduled_boundary=boundary,
        strategy_name="momentum",
        idempotency_key="run-duplicate-attempt",
        parameters={},
    )

    assert created.outcome is CreateOutcome.CREATED
    assert duplicate.outcome is CreateOutcome.DUPLICATE
    assert duplicate.run.id == created.run.id

    later = repository.create_for_boundary(
        asset_config_id=asset_config_id,
        scheduled_boundary=boundary + timedelta(hours=1),
        strategy_name="momentum",
        idempotency_key="run-2",
        parameters={},
    )
    assert later.outcome is CreateOutcome.CREATED


def test_strategy_run_does_not_swallow_unrelated_integrity_error(session: Session) -> None:
    asset_config_id = add_asset(session)
    repository = StrategyRunRepository(session)
    boundary = datetime(2026, 9, 16, 10, tzinfo=UTC)
    repository.create_for_boundary(
        asset_config_id=asset_config_id,
        scheduled_boundary=boundary,
        strategy_name="momentum",
        idempotency_key="same-key",
        parameters={},
    )

    with pytest.raises(IntegrityError):
        repository.create_for_boundary(
            asset_config_id=asset_config_id,
            scheduled_boundary=boundary + timedelta(hours=1),
            strategy_name="momentum",
            idempotency_key="same-key",
            parameters={},
        )


def test_duplicate_fill_is_rejected_without_poisoning_transaction(session: Session) -> None:
    order = add_order(session)
    repository = FillRepository(session)
    executed_at = datetime(2026, 9, 16, 10, 5, tzinfo=UTC)

    first = repository.append(
        order_id=order.id,
        exchange_fill_id="fill-1",
        quantity=Decimal("1.25"),
        price=Decimal("60000.50"),
        fee_amount=Decimal("2.50"),
        fee_currency="USD",
        executed_at=executed_at,
    )

    with pytest.raises(DuplicateFillError):
        repository.append(
            order_id=order.id,
            exchange_fill_id="fill-1",
            quantity=Decimal("1.25"),
            price=Decimal("60000.50"),
            fee_amount=Decimal("2.50"),
            fee_currency="USD",
            executed_at=executed_at,
        )

    second = repository.append(
        order_id=order.id,
        exchange_fill_id="fill-2",
        quantity=Decimal("0.75"),
        price=Decimal("60001.00"),
        fee_amount=Decimal("1.50"),
        fee_currency="USD",
        executed_at=executed_at + timedelta(seconds=1),
    )

    assert [fill.id for fill in repository.list_for_order(order.id)] == [first.id, second.id]
    assert not hasattr(repository, "update")
    assert not hasattr(repository, "delete")


def test_commands_enqueue_idempotently_and_claim_fifo_across_sessions(
    session: Session,
) -> None:
    repository = CommandRepository(session)
    requested_at = datetime(2026, 9, 16, 11, tzinfo=UTC)

    first = repository.enqueue(
        command_type="start",
        idempotency_key="command-1",
        payload={"asset": 1},
        requested_at=requested_at,
    )
    second = repository.enqueue(
        command_type="stop",
        idempotency_key="command-2",
        payload={"asset": 2},
        requested_at=requested_at,
    )
    duplicate = repository.enqueue(
        command_type="start",
        idempotency_key="command-1",
        payload={"asset": 1},
        requested_at=requested_at + timedelta(minutes=1),
    )
    third = repository.enqueue(
        command_type="rebalance",
        idempotency_key="command-3",
        payload={},
        requested_at=requested_at + timedelta(minutes=1),
    )
    session.commit()

    assert first.outcome is CreateOutcome.CREATED
    assert duplicate.outcome is CreateOutcome.DUPLICATE
    assert duplicate.command.id == first.command.id
    assert third.outcome is CreateOutcome.CREATED

    claimed_first = repository.claim_next()
    assert claimed_first is not None
    assert claimed_first.id == first.command.id
    assert claimed_first.status == "processing"
    session.commit()

    with Session(session.get_bind()) as competing_session:
        competing_repository = CommandRepository(competing_session)
        claimed_second = competing_repository.claim_next()
        assert claimed_second is not None
        assert claimed_second.id == second.command.id
        assert claimed_second.status == "processing"
        competing_session.commit()

    claimed_third = repository.claim_next()
    assert claimed_third is not None
    assert claimed_third.id == third.command.id
    session.commit()
    assert repository.claim_next() is None
