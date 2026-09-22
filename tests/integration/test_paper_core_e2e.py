from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text

from quant_platform.api.app import create_app
from quant_platform.config import Settings, TradingMode
from quant_platform.core.runtime import CoreIterationDegraded, PaperCore, build_paper_core
from quant_platform.db.base import Base
from quant_platform.db.models import (
    AssetConfig,
    Command,
    ExchangeConfig,
    Order,
    Position,
    Signal,
    StrategyRun,
)
from quant_platform.db.repositories import CommandRepository
from quant_platform.db.session import create_engine, create_session_factory
from quant_platform.exchange import FakeExchange

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)


def paper_settings() -> dict[str, object]:
    return {
        "timeframe": "1h",
        "strategy": "no-trade",
        "paper_open": "100",
        "paper_high": "101",
        "paper_low": "99",
        "paper_close": "100",
        "paper_volume": "10",
        "quantity": "0.01",
        "available_balance": "1000",
        "current_exposure": "0",
        "leverage": "1",
        "minimum_quantity": "0.001",
        "maximum_quantity": "10",
        "quantity_step": "0.001",
        "minimum_notional": "1",
        "max_notional": "1000",
        "max_position_quantity": "10",
        "max_data_age_seconds": 3600,
        "balance_usage_fraction": "0.95",
        "taker_fee_rate": "0",
        "parameters": {},
    }


@pytest.fixture
def migrated_database(tmp_path: Path) -> Iterator[tuple[str, Engine, Callable[..., Any], int]]:
    database_url = f"sqlite:///{tmp_path / 'paper-core.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('0001_initial')"))
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        exchange = ExchangeConfig(name="paper", exchange="fake", options={})
        session.add(exchange)
        session.flush()
        asset = AssetConfig(
            exchange_config_id=exchange.id,
            symbol="BTC/USD",
            base_asset="BTC",
            quote_asset="USD",
            enabled=True,
            settings=paper_settings(),
        )
        session.add(asset)
        session.commit()
        asset_id = asset.id
    yield database_url, engine, session_factory, asset_id
    engine.dispose()


def build(database_url: str) -> PaperCore:
    settings = Settings(
        _env_file=None,
        trading_mode=TradingMode.PAPER,
        database_url=database_url,
    )
    return build_paper_core(settings=settings, clock=lambda: NOW)


def test_real_paper_iteration_persists_completed_hold_without_order(
    migrated_database: tuple[str, Engine, Callable[..., Any], int],
) -> None:
    database_url, _engine, session_factory, asset_id = migrated_database

    report = build(database_url).iterate()

    assert report.dispatched == 1
    assert report.invalid_asset_ids == ()
    with session_factory() as session:
        run = session.scalars(select(StrategyRun)).one()
        signal = session.scalars(select(Signal)).one()
        assert run.asset_config_id == asset_id
        assert run.status == "completed"
        assert run.strategy_name == "no-trade"
        assert signal.strategy_run_id == run.id
        assert signal.side == "hold"
        assert session.scalar(select(func.count()).select_from(Order)) == 0


def test_fresh_composition_root_same_boundary_is_durable_duplicate_free(
    migrated_database: tuple[str, Engine, Callable[..., Any], int],
) -> None:
    database_url, _engine, session_factory, _asset_id = migrated_database

    first = build(database_url).iterate()
    restarted = build(database_url).iterate()

    assert first.dispatched == 1
    assert restarted.duplicates == 1
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(StrategyRun)) == 1
        assert session.scalar(select(func.count()).select_from(Signal)) == 1
        assert session.scalar(select(func.count()).select_from(Order)) == 0


def test_non_paper_mode_is_rejected_before_exchange_creation(tmp_path: Path) -> None:
    created = False

    def exchange_factory() -> FakeExchange:
        nonlocal created
        created = True
        return FakeExchange()

    settings = Settings(
        _env_file=None,
        trading_mode=TradingMode.TESTNET,
        database_url=f"sqlite:///{tmp_path / 'must-not-open.db'}",
    )
    with pytest.raises(ValueError, match="paper mode only"):
        build_paper_core(
            settings=settings,
            clock=lambda: NOW,
            exchange_factory=exchange_factory,
        )

    assert created is False
    assert not (tmp_path / "must-not-open.db").exists()


def enqueue(
    session_factory: Callable[..., Any],
    command_type: str,
    key: str,
    payload: dict[str, object],
) -> None:
    with session_factory() as session:
        CommandRepository(session).enqueue(
            command_type=command_type,
            idempotency_key=key,
            payload=payload,
            requested_at=NOW,
        )
        session.commit()


def test_durable_pause_resume_reconcile_and_unsupported_close_are_safe(
    migrated_database: tuple[str, Engine, Callable[..., Any], int],
) -> None:
    database_url, _engine, session_factory, asset_id = migrated_database
    enqueue(session_factory, "pause", "pause-1", {"asset_id": asset_id})

    paused = build(database_url)
    pause_report = paused.iterate()
    assert pause_report.commands_completed == 1
    assert pause_report.dispatched == 0
    paused.close()

    enqueue(session_factory, "resume", "resume-1", {"asset_id": asset_id})
    enqueue(session_factory, "reconcile", "reconcile-1", {"asset_id": asset_id})
    enqueue(
        session_factory,
        "close",
        "close-unsupported",
        {"asset_id": asset_id, "password": "must-not-leak"},
    )

    resumed = build(database_url)
    report = resumed.iterate()

    assert report.commands_completed == 2
    assert report.commands_failed == 1
    assert report.dispatched == 1
    assert any(call.method == "fetch_positions" for call in resumed.exchange.calls)
    with session_factory() as session:
        asset = session.get_one(AssetConfig, asset_id)
        commands = session.scalars(select(Command).order_by(Command.id)).all()
        assert asset.enabled is True
        assert [command.status for command in commands] == [
            "completed",
            "completed",
            "completed",
            "failed",
        ]
        assert commands[-1].error == "command processing failed"
        assert "must-not-leak" not in commands[-1].error
        assert session.scalar(select(func.count()).select_from(Position)) == 1


def test_bad_asset_is_isolated_but_daemon_iteration_is_degraded(
    migrated_database: tuple[str, Engine, Callable[..., Any], int],
) -> None:
    database_url, _engine, session_factory, _asset_id = migrated_database
    with session_factory() as session:
        exchange_id = session.scalars(select(ExchangeConfig.id)).one()
        bad = AssetConfig(
            exchange_config_id=exchange_id,
            symbol="ETH/USD",
            base_asset="ETH",
            quote_asset="USD",
            enabled=True,
            settings={"strategy": "no-trade", "apiToken": "forbidden"},
        )
        session.add(bad)
        session.commit()
        bad_id = bad.id

    core = build(database_url)
    report = core.iterate()
    assert report.dispatched == 1
    assert report.invalid_asset_ids == (bad_id,)

    restarted = build(database_url)
    with pytest.raises(CoreIterationDegraded, match="isolated failures"):
        restarted.daemon_iteration()


def test_optional_api_start_stop_does_not_change_core_result(
    migrated_database: tuple[str, Engine, Callable[..., Any], int],
) -> None:
    database_url, _engine, session_factory, _asset_id = migrated_database
    assert build(database_url).iterate().dispatched == 1

    app = create_app(session_factory=session_factory, clock=lambda: NOW)
    with TestClient(app) as client:
        assert client.get("/api/v1/status").status_code == 200

    after_web_stop = build(database_url).iterate()
    assert after_web_stop.duplicates == 1
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(StrategyRun)) == 1
        assert session.scalar(select(func.count()).select_from(Signal)) == 1
        assert session.scalar(select(func.count()).select_from(Order)) == 0
