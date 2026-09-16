import json
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from quant_platform.cli.client import CliServices, create_app
from quant_platform.config import TradingMode
from quant_platform.core.heartbeat import HeartbeatService, HeartbeatStatus
from quant_platform.db.base import Base
from quant_platform.db.models import AssetConfig, Command, ExchangeConfig, Position
from quant_platform.db.session import create_engine, create_session_factory

NOW = datetime(2026, 9, 17, 8, 30, tzinfo=UTC)


@pytest.fixture
def session_factory(tmp_path: Path) -> Iterator[Callable[[], Session]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'cli.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


def app_for(
    session_factory: Callable[[], Session],
    *,
    mode: TradingMode = TradingMode.PAPER,
) -> Any:
    return create_app(
        CliServices(session_factory=session_factory, trading_mode=mode, clock=lambda: NOW)
    )


def add_asset_config(session_factory: Callable[[], Session]) -> int:
    with session_factory() as session:
        exchange = ExchangeConfig(name="primary", exchange="kraken", sandbox=True, options={})
        session.add(exchange)
        session.flush()
        asset = AssetConfig(
            exchange_config_id=exchange.id,
            symbol="BTC/USD",
            base_asset="BTC",
            quote_asset="USD",
            enabled=True,
            settings={},
        )
        session.add(asset)
        session.commit()
        return asset.id


def test_status_reports_fresh_core_heartbeat_and_queue_counts(
    session_factory: Callable[[], Session],
) -> None:
    HeartbeatService(session_factory, "core", clock=lambda: NOW).publish(HeartbeatStatus.RUNNING)
    with session_factory() as session:
        session.add_all(
            [
                Command(
                    command_type="pause",
                    status="pending",
                    idempotency_key="pending-1",
                    payload={"asset_id": 1},
                    requested_at=NOW,
                ),
                Command(
                    command_type="resume",
                    status="completed",
                    idempotency_key="completed-1",
                    payload={"asset_id": 1},
                    requested_at=NOW,
                    processed_at=NOW,
                ),
            ]
        )
        session.commit()

    result = CliRunner().invoke(app_for(session_factory), ["status", "--max-age-seconds", "30"])

    assert result.exit_code == 0
    assert result.stdout == (
        '{"command_counts":{"completed":1,"failed":0,"pending":1,"processing":0},'
        '"healthy":true,"heartbeat":{"age_seconds":0.0,"last_seen_at":'
        '"2026-09-17T08:30:00Z","reason":null,"service_name":"core",'
        '"status":"running"}}\n'
    )
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Command)) == 2


def test_status_reports_missing_heartbeat_and_exits_nonzero(
    session_factory: Callable[[], Session],
) -> None:
    result = CliRunner().invoke(app_for(session_factory), ["status"])

    assert result.exit_code == 1
    assert result.stdout == (
        '{"command_counts":{"completed":0,"failed":0,"pending":0,"processing":0},'
        '"healthy":false,"heartbeat":{"age_seconds":null,"last_seen_at":null,'
        '"reason":"missing","service_name":"core","status":null}}\n'
    )


def test_status_reports_stale_heartbeat_and_exits_nonzero(
    session_factory: Callable[[], Session],
) -> None:
    heartbeat_at = NOW - timedelta(seconds=31)
    HeartbeatService(session_factory, "core", clock=lambda: heartbeat_at).publish(
        HeartbeatStatus.RUNNING
    )

    result = CliRunner().invoke(
        app_for(session_factory),
        ["status", "--max-age-seconds", "30"],
    )

    assert result.exit_code == 1
    assert '"healthy":false' in result.stdout
    assert '"age_seconds":31.0' in result.stdout
    assert '"reason":"stale"' in result.stdout


def test_assets_lists_config_and_local_position_without_mark_or_unrealized_pnl(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        exchange = ExchangeConfig(
            name="primary",
            exchange="kraken",
            sandbox=True,
            options={"api_secret": "must-not-leak"},
        )
        session.add(exchange)
        session.flush()
        eth = AssetConfig(
            exchange_config_id=exchange.id,
            symbol="ETH/USD",
            base_asset="ETH",
            quote_asset="USD",
            enabled=True,
            settings={"timeframe": "1h"},
        )
        btc = AssetConfig(
            exchange_config_id=exchange.id,
            symbol="BTC/USD",
            base_asset="BTC",
            quote_asset="USD",
            enabled=False,
            settings={},
        )
        session.add_all([eth, btc])
        session.flush()
        session.add(
            Position(
                exchange_config_id=exchange.id,
                asset_config_id=eth.id,
                quantity=Decimal("1.250000000000"),
                average_entry_price=Decimal("3200.500000000000"),
                realized_pnl=Decimal("12.340000000000"),
            )
        )
        session.commit()

    result = CliRunner().invoke(app_for(session_factory), ["assets"])

    assert result.exit_code == 0
    assert result.stdout == (
        '{"assets":[{"base_asset":"ETH","enabled":true,"exchange_config_id":1,"id":1,'
        '"position":{"average_entry_price":"3200.500000000000","quantity":'
        '"1.250000000000","realized_pnl":"12.340000000000"},"quote_asset":"USD",'
        '"settings":{"timeframe":"1h"},"symbol":"ETH/USD"},{"base_asset":"BTC",'
        '"enabled":false,"exchange_config_id":1,"id":2,"position":null,'
        '"quote_asset":"USD","settings":{},"symbol":"BTC/USD"}]}\n'
    )
    assert "mark" not in result.stdout
    assert "unrealized" not in result.stdout
    assert "must-not-leak" not in result.stdout


def test_assets_redacts_nested_secret_settings(
    session_factory: Callable[[], Session],
) -> None:
    asset_id = add_asset_config(session_factory)
    with session_factory() as session:
        asset = session.get_one(AssetConfig, asset_id)
        asset.settings = {
            "strategy": {"apiKey": "visible-secret", "lookback": 20},
            "access_token": "another-secret",
        }
        session.commit()

    result = CliRunner().invoke(app_for(session_factory), ["assets"])

    assert result.exit_code == 0
    assert "visible-secret" not in result.stdout
    assert "another-secret" not in result.stdout
    assert result.stdout.count("[REDACTED]") == 2
    assert '"lookback":20' in result.stdout


def test_pause_enqueues_and_commits_explicit_command(
    session_factory: Callable[[], Session],
) -> None:
    asset_id = add_asset_config(session_factory)

    result = CliRunner().invoke(
        app_for(session_factory),
        ["pause", str(asset_id), "--idempotency-key", "pause-key"],
    )

    assert result.exit_code == 0
    assert result.stdout == (
        '{"command_id":1,"command_type":"pause","idempotency_key":"pause-key",'
        '"outcome":"created"}\n'
    )
    with session_factory() as session:
        command = session.scalars(select(Command)).one()
        assert command.command_type == "pause"
        assert command.status == "pending"
        assert command.idempotency_key == "pause-key"
        assert command.payload == {"asset_id": asset_id}
        assert command.requested_at == NOW.replace(tzinfo=None)


def test_resume_reports_duplicate_idempotency_without_second_command(
    session_factory: Callable[[], Session],
) -> None:
    asset_id = add_asset_config(session_factory)
    app = app_for(session_factory)

    created = CliRunner().invoke(
        app,
        ["resume", str(asset_id), "--idempotency-key", "resume-key"],
    )
    duplicate = CliRunner().invoke(
        app,
        ["resume", str(asset_id), "--idempotency-key", "resume-key"],
    )

    assert created.exit_code == 0
    assert duplicate.exit_code == 0
    assert created.stdout == (
        '{"command_id":1,"command_type":"resume","idempotency_key":"resume-key",'
        '"outcome":"created"}\n'
    )
    assert duplicate.stdout == (
        '{"command_id":1,"command_type":"resume","idempotency_key":"resume-key",'
        '"outcome":"duplicate"}\n'
    )
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Command)) == 1


def test_reconcile_enqueues_all_assets_or_one_asset_explicitly(
    session_factory: Callable[[], Session],
) -> None:
    asset_id = add_asset_config(session_factory)
    app = app_for(session_factory)

    all_assets = CliRunner().invoke(
        app,
        ["reconcile", "--idempotency-key", "reconcile-all"],
    )
    one_asset = CliRunner().invoke(
        app,
        ["reconcile", "--asset-id", str(asset_id), "--idempotency-key", "reconcile-one"],
    )

    assert all_assets.exit_code == 0
    assert one_asset.exit_code == 0
    assert all_assets.stdout == (
        '{"command_id":1,"command_type":"reconcile","idempotency_key":"reconcile-all",'
        '"outcome":"created"}\n'
    )
    assert one_asset.stdout == (
        '{"command_id":2,"command_type":"reconcile","idempotency_key":"reconcile-one",'
        '"outcome":"created"}\n'
    )
    with session_factory() as session:
        commands = list(session.scalars(select(Command).order_by(Command.id)))
        assert [command.payload for command in commands] == [
            {"asset_id": None},
            {"asset_id": asset_id},
        ]


def test_paper_close_enqueues_without_live_confirmation_language(
    session_factory: Callable[[], Session],
) -> None:
    asset_id = add_asset_config(session_factory)

    result = CliRunner().invoke(
        app_for(session_factory, mode=TradingMode.PAPER),
        ["close", str(asset_id), "--idempotency-key", "close-paper"],
    )

    assert result.exit_code == 0
    assert "confirm" not in result.stdout.lower()
    assert "live" not in result.stdout.lower()
    assert result.stdout == (
        '{"command_id":1,"command_type":"close","idempotency_key":"close-paper",'
        '"outcome":"created"}\n'
    )
    with session_factory() as session:
        command = session.scalars(select(Command)).one()
        assert command.payload == {"asset_id": asset_id, "trading_mode": "paper"}


def test_testnet_close_does_not_request_live_confirmation(
    session_factory: Callable[[], Session],
) -> None:
    asset_id = add_asset_config(session_factory)

    result = CliRunner().invoke(
        app_for(session_factory, mode=TradingMode.TESTNET),
        ["close", str(asset_id), "--idempotency-key", "close-testnet"],
    )

    assert result.exit_code == 0
    assert "confirm" not in result.stdout.lower()
    with session_factory() as session:
        command = session.scalars(select(Command)).one()
        assert command.payload == {"asset_id": asset_id, "trading_mode": "testnet"}


def test_live_close_requires_exact_typed_confirmation_before_enqueue(
    session_factory: Callable[[], Session],
) -> None:
    asset_id = add_asset_config(session_factory)
    app = app_for(session_factory, mode=TradingMode.LIVE)

    mismatch = CliRunner().invoke(
        app,
        ["close", str(asset_id), "--idempotency-key", "close-live"],
        input="yes\n",
    )

    assert mismatch.exit_code == 1
    assert f"CLOSE {asset_id}" in mismatch.stdout
    assert '{"error":"confirmation_mismatch"}' in mismatch.stdout
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Command)) == 0

    confirmed = CliRunner().invoke(
        app,
        ["close", str(asset_id), "--idempotency-key", "close-live"],
        input=f"CLOSE {asset_id}\n",
    )

    assert confirmed.exit_code == 0
    assert f"CLOSE {asset_id}" in confirmed.stdout
    assert confirmed.stdout.endswith(
        '{"command_id":1,"command_type":"close","idempotency_key":"close-live",'
        '"outcome":"created"}\n'
    )
    with session_factory() as session:
        command = session.scalars(select(Command)).one()
        assert command.payload == {"asset_id": asset_id, "trading_mode": "live"}


@pytest.mark.parametrize(
    "arguments",
    [
        ["pause", "999"],
        ["resume", "999"],
        ["reconcile", "--asset-id", "999"],
        ["close", "999"],
    ],
)
def test_asset_commands_reject_unknown_asset_without_enqueue(
    session_factory: Callable[[], Session],
    arguments: list[str],
) -> None:
    result = CliRunner().invoke(app_for(session_factory), arguments)

    assert result.exit_code == 1
    assert result.stdout == '{"asset_id":999,"error":"asset_not_found"}\n'
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Command)) == 0


def test_default_idempotency_keys_are_distinct_uuids(
    session_factory: Callable[[], Session],
) -> None:
    asset_id = add_asset_config(session_factory)
    app = app_for(session_factory)

    first = CliRunner().invoke(app, ["pause", str(asset_id)])
    second = CliRunner().invoke(app, ["pause", str(asset_id)])

    assert first.exit_code == 0
    assert second.exit_code == 0
    first_key = json.loads(first.stdout)["idempotency_key"]
    second_key = json.loads(second.stdout)["idempotency_key"]
    assert UUID(first_key).version == 4
    assert UUID(second_key).version == 4
    assert first_key != second_key
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Command)) == 2


def test_cancelled_live_close_creates_no_command(
    session_factory: Callable[[], Session],
) -> None:
    asset_id = add_asset_config(session_factory)

    result = CliRunner().invoke(
        app_for(session_factory, mode=TradingMode.LIVE),
        ["close", str(asset_id)],
        input="",
    )

    assert result.exit_code != 0
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Command)) == 0


def test_database_error_is_sanitized_and_later_invocation_recovers(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'uninitialized-secret-name.db'}")
    factory = create_session_factory(engine)
    app = app_for(factory)

    failed = CliRunner().invoke(app, ["status"])

    assert failed.exit_code == 1
    assert failed.stdout == '{"error":"database_operation_failed"}\n'
    assert "uninitialized-secret-name" not in failed.stdout

    Base.metadata.create_all(engine)
    recovered = CliRunner().invoke(app, ["status"])

    assert recovered.exit_code == 1
    assert '"reason":"missing"' in recovered.stdout
    assert "database_operation_failed" not in recovered.stdout
    engine.dispose()


def test_cli_import_does_not_load_api_exchange_or_ccxt() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import quant_platform.cli.client; "
                "blocked = [name for name in sys.modules "
                "if name == 'ccxt' or name.startswith('quant_platform.api') "
                "or name.startswith('quant_platform.exchange')]; "
                "assert blocked == [], blocked"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
