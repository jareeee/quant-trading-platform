from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from quant_platform.api.app import create_app
from quant_platform.db.base import Base
from quant_platform.db.models import (
    AssetConfig,
    Command,
    ExchangeConfig,
    Fill,
    Heartbeat,
    Order,
    Position,
    StrategyRun,
)
from quant_platform.db.session import create_engine, create_session_factory

NOW = datetime(2026, 9, 17, 10, 30, tzinfo=UTC)


@pytest.fixture
def session_factory(tmp_path: Path) -> Iterator[Callable[[], Session]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def client(session_factory: Callable[[], Session]) -> TestClient:
    return TestClient(create_app(session_factory=session_factory, clock=lambda: NOW))


def test_status_reports_missing_heartbeat_and_empty_queue(client: TestClient) -> None:
    response = client.get("/api/v1/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "missing",
        "healthy": False,
        "checked_at": "2026-09-17T10:30:00Z",
        "heartbeat": {
            "service_name": "trading-core",
            "state": None,
            "last_seen_at": None,
            "age_seconds": None,
            "reason": "missing",
        },
        "queue": {"pending": 0, "processing": 0, "failed": 0},
    }


def test_status_reports_stale_heartbeat_and_queue_counts(
    client: TestClient, session_factory: Callable[[], Session]
) -> None:
    stale_at = datetime(2026, 9, 17, 10, 29, tzinfo=UTC)
    with session_factory() as session:
        session.add(
            Heartbeat(
                service_name="trading-core",
                status="running",
                last_seen_at=stale_at,
                details={},
                created_at=stale_at,
                updated_at=stale_at,
            )
        )
        for index, state in enumerate(("pending", "pending", "processing", "failed")):
            session.add(
                Command(
                    command_type="noop",
                    status=state,
                    idempotency_key=f"command-{index}",
                    payload={},
                    requested_at=stale_at,
                    created_at=stale_at,
                    updated_at=stale_at,
                )
            )
        session.commit()

    body = client.get("/api/v1/status").json()

    assert body["status"] == "stale"
    assert body["healthy"] is False
    assert body["heartbeat"] == {
        "service_name": "trading-core",
        "state": "running",
        "last_seen_at": "2026-09-17T10:29:00Z",
        "age_seconds": 60.0,
        "reason": "stale",
    }
    assert body["queue"] == {"pending": 2, "processing": 1, "failed": 1}


def test_status_reports_fresh_running_heartbeat_as_healthy(
    client: TestClient, session_factory: Callable[[], Session]
) -> None:
    fresh_at = datetime(2026, 9, 17, 10, 29, 45, tzinfo=UTC)
    with session_factory() as session:
        session.add(
            Heartbeat(
                service_name="trading-core",
                status="running",
                last_seen_at=fresh_at,
                details={},
                created_at=fresh_at,
                updated_at=fresh_at,
            )
        )
        session.commit()

    body = client.get("/api/v1/status").json()

    assert body["status"] == "healthy"
    assert body["healthy"] is True
    assert body["heartbeat"]["reason"] is None


def test_assets_are_ordered_paginated_and_recursively_redacted(
    client: TestClient, session_factory: Callable[[], Session]
) -> None:
    created = datetime(2026, 9, 17, 9, tzinfo=UTC)
    with session_factory() as session:
        exchange = ExchangeConfig(name="primary", exchange="kraken", options={})
        session.add(exchange)
        session.flush()
        eth = AssetConfig(
            exchange_config_id=exchange.id,
            symbol="ETH/USD",
            base_asset="ETH",
            quote_asset="USD",
            settings={
                "timeframe": "1h",
                "apiKey": "must-not-leak",
                "nested": {"access_token": "hidden", "window": 20},
                "entries": [{"secretKey": "hidden-too"}],
            },
            created_at=created,
            updated_at=created,
        )
        btc = AssetConfig(
            exchange_config_id=exchange.id,
            symbol="BTC/USD",
            base_asset="BTC",
            quote_asset="USD",
            enabled=False,
            settings={},
            created_at=created,
            updated_at=created,
        )
        session.add_all([eth, btc])
        session.commit()
        eth_id, btc_id = eth.id, btc.id

    response = client.get("/api/v1/assets", params={"limit": 1, "offset": 0})

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": eth_id,
                "exchange_config_id": exchange.id,
                "symbol": "ETH/USD",
                "base_asset": "ETH",
                "quote_asset": "USD",
                "enabled": True,
                "settings": {
                    "timeframe": "1h",
                    "apiKey": "[REDACTED]",
                    "nested": {"access_token": "[REDACTED]", "window": 20},
                    "entries": [{"secretKey": "[REDACTED]"}],
                },
                "created_at": "2026-09-17T09:00:00Z",
                "updated_at": "2026-09-17T09:00:00Z",
            }
        ],
        "limit": 1,
        "offset": 0,
        "total": 2,
    }
    assert client.get(f"/api/v1/assets/{btc_id}").json()["id"] == btc_id
    assert client.get("/api/v1/assets/999999").status_code == 404


def test_asset_pagination_is_bounded_and_validated(client: TestClient) -> None:
    assert client.get("/api/v1/assets", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/assets", params={"limit": 101}).status_code == 422
    assert client.get("/api/v1/assets", params={"offset": -1}).status_code == 422


@pytest.mark.parametrize("path", ["positions", "runs", "orders", "fills"])
def test_read_collections_are_empty_and_paginated(client: TestClient, path: str) -> None:
    response = client.get(f"/api/v1/{path}")

    assert response.status_code == 200
    assert response.json() == {"items": [], "limit": 50, "offset": 0, "total": 0}


def test_trading_records_expose_only_persisted_values(
    client: TestClient, session_factory: Callable[[], Session]
) -> None:
    at = datetime(2026, 9, 17, 10, tzinfo=UTC)
    with session_factory() as session:
        exchange = ExchangeConfig(name="primary", exchange="kraken", options={})
        session.add(exchange)
        session.flush()
        asset = AssetConfig(
            exchange_config_id=exchange.id,
            symbol="BTC/USD",
            base_asset="BTC",
            quote_asset="USD",
            settings={},
            created_at=at,
            updated_at=at,
        )
        session.add(asset)
        session.flush()
        run = StrategyRun(
            asset_config_id=asset.id,
            strategy_name="momentum",
            status="completed",
            idempotency_key="run-1",
            parameters={"lookback": 20},
            scheduled_boundary=at,
            started_at=at,
            ended_at=at,
            created_at=at,
            updated_at=at,
        )
        position = Position(
            exchange_config_id=exchange.id,
            asset_config_id=asset.id,
            quantity=Decimal("2.5"),
            average_entry_price=Decimal("60000.125"),
            realized_pnl=Decimal("12.75"),
            created_at=at,
            updated_at=at,
        )
        session.add_all([run, position])
        session.flush()
        order = Order(
            exchange_config_id=exchange.id,
            strategy_run_id=run.id,
            client_order_id="client-1",
            exchange_order_id="exchange-1",
            symbol="BTC/USD",
            side="buy",
            order_type="market",
            status="closed",
            quantity=Decimal("2.5"),
            price=None,
            filled_quantity=Decimal("2.5"),
            submitted_at=at,
            created_at=at,
            updated_at=at,
        )
        session.add(order)
        session.flush()
        fill = Fill(
            order_id=order.id,
            exchange_fill_id="fill-1",
            quantity=Decimal("2.5"),
            price=Decimal("60000.125"),
            fee_amount=Decimal("1.25"),
            fee_currency="USD",
            executed_at=at,
            created_at=at,
            updated_at=at,
        )
        session.add(fill)
        session.commit()

    assert client.get("/api/v1/positions").json() == {
        "items": [
            {
                "id": position.id,
                "exchange_config_id": exchange.id,
                "asset_config_id": asset.id,
                "quantity": "2.500000000000",
                "average_entry_price": "60000.125000000000",
                "stored_realized_pnl": "12.750000000000",
                "created_at": "2026-09-17T10:00:00Z",
                "updated_at": "2026-09-17T10:00:00Z",
            }
        ],
        "limit": 50,
        "offset": 0,
        "total": 1,
    }
    assert client.get("/api/v1/runs").json() == {
        "items": [
            {
                "id": run.id,
                "asset_config_id": asset.id,
                "strategy_name": "momentum",
                "status": "completed",
                "idempotency_key": "run-1",
                "parameters": {"lookback": 20},
                "scheduled_boundary": "2026-09-17T10:00:00Z",
                "started_at": "2026-09-17T10:00:00Z",
                "ended_at": "2026-09-17T10:00:00Z",
                "created_at": "2026-09-17T10:00:00Z",
                "updated_at": "2026-09-17T10:00:00Z",
            }
        ],
        "limit": 50,
        "offset": 0,
        "total": 1,
    }
    assert client.get("/api/v1/orders").json() == {
        "items": [
            {
                "id": order.id,
                "exchange_config_id": exchange.id,
                "strategy_run_id": run.id,
                "signal_id": None,
                "client_order_id": "client-1",
                "exchange_order_id": "exchange-1",
                "symbol": "BTC/USD",
                "side": "buy",
                "order_type": "market",
                "status": "closed",
                "quantity": "2.500000000000",
                "price": None,
                "filled_quantity": "2.500000000000",
                "submitted_at": "2026-09-17T10:00:00Z",
                "created_at": "2026-09-17T10:00:00Z",
                "updated_at": "2026-09-17T10:00:00Z",
            }
        ],
        "limit": 50,
        "offset": 0,
        "total": 1,
    }
    assert client.get("/api/v1/fills").json() == {
        "items": [
            {
                "id": fill.id,
                "order_id": order.id,
                "exchange_fill_id": "fill-1",
                "quantity": "2.500000000000",
                "price": "60000.125000000000",
                "fee_amount": "1.250000000000",
                "fee_currency": "USD",
                "executed_at": "2026-09-17T10:00:00Z",
                "created_at": "2026-09-17T10:00:00Z",
                "updated_at": "2026-09-17T10:00:00Z",
            }
        ],
        "limit": 50,
        "offset": 0,
        "total": 1,
    }


def test_database_errors_are_generic_and_later_sessions_work(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        bind = session.get_bind()
        session.execute(text("DROP TABLE asset_configs"))
        session.commit()
    error_client = TestClient(
        create_app(session_factory=session_factory, clock=lambda: NOW),
        raise_server_exceptions=False,
    )

    response = error_client.get("/api/v1/assets")

    assert response.status_code == 500
    assert response.json() == {"detail": "internal server error"}
    assert "sqlite" not in response.text.lower()
    assert "asset_configs" not in response.text

    Base.metadata.create_all(bind)
    recovered = error_client.get("/api/v1/assets")
    assert recovered.status_code == 200
