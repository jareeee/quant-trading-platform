from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quant_platform.api.app import create_app
from quant_platform.config import TradingMode
from quant_platform.db.base import Base
from quant_platform.db.models import AssetConfig, Command, ExchangeConfig
from quant_platform.db.session import create_engine, create_session_factory

NOW = datetime(2026, 9, 17, 11, 0, tzinfo=UTC)


@pytest.fixture
def session_factory(tmp_path: Path) -> Iterator[Callable[[], Session]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'api-mutations.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def exchange_id(session_factory: Callable[[], Session]) -> int:
    with session_factory() as session:
        exchange = ExchangeConfig(name="primary", exchange="kraken", options={})
        session.add(exchange)
        session.commit()
        return exchange.id


@pytest.fixture
def client(session_factory: Callable[[], Session]) -> TestClient:
    return TestClient(create_app(session_factory=session_factory, clock=lambda: NOW))


def add_asset(session_factory: Callable[[], Session], exchange_id: int) -> int:
    with session_factory() as session:
        asset = AssetConfig(
            exchange_config_id=exchange_id,
            symbol="BTC/USD",
            base_asset="BTC",
            quote_asset="USD",
            settings={},
        )
        session.add(asset)
        session.commit()
        return asset.id


def test_create_asset_commits_valid_non_secret_configuration(
    client: TestClient,
    session_factory: Callable[[], Session],
    exchange_id: int,
) -> None:
    response = client.post(
        "/api/v1/assets",
        json={
            "exchange_config_id": exchange_id,
            "symbol": "BTC/USD",
            "base_asset": "BTC",
            "quote_asset": "USD",
            "enabled": True,
            "settings": {"timeframe": "1h", "risk": {"allocation": "0.05"}},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] > 0
    assert body["symbol"] == "BTC/USD"
    assert body["settings"] == {"timeframe": "1h", "risk": {"allocation": "0.05"}}
    with session_factory() as session:
        stored = session.scalars(select(AssetConfig)).one()
        assert stored.id == body["id"]
        assert stored.settings == body["settings"]


def test_update_asset_commits_only_supplied_fields(
    client: TestClient,
    session_factory: Callable[[], Session],
    exchange_id: int,
) -> None:
    with session_factory() as session:
        asset = AssetConfig(
            exchange_config_id=exchange_id,
            symbol="BTC/USD",
            base_asset="BTC",
            quote_asset="USD",
            enabled=True,
            settings={"timeframe": "1h"},
        )
        session.add(asset)
        session.commit()
        asset_id = asset.id

    response = client.patch(
        f"/api/v1/assets/{asset_id}",
        json={"enabled": False, "settings": {"timeframe": "4h"}},
    )

    assert response.status_code == 200
    assert response.json()["symbol"] == "BTC/USD"
    assert response.json()["enabled"] is False
    assert response.json()["settings"] == {"timeframe": "4h"}
    with session_factory() as session:
        stored = session.get_one(AssetConfig, asset_id)
        assert stored.enabled is False
        assert stored.settings == {"timeframe": "4h"}


@pytest.mark.parametrize(
    "settings",
    [
        {"strategy": {"apiKey": "do-not-store"}},
        {"feeds": [{"credentials": {"token": "do-not-store"}}]},
        {"exchange": [{"private-key": "do-not-store"}]},
        {"auth": {"passphrase": "do-not-store"}},
    ],
)
def test_asset_mutations_reject_recursive_secret_like_keys(
    client: TestClient,
    session_factory: Callable[[], Session],
    exchange_id: int,
    settings: dict[str, object],
) -> None:
    response = client.post(
        "/api/v1/assets",
        json={
            "exchange_config_id": exchange_id,
            "symbol": "BTC/USD",
            "base_asset": "BTC",
            "quote_asset": "USD",
            "settings": settings,
        },
    )

    assert response.status_code == 422
    assert "do-not-store" not in response.text
    with session_factory() as session:
        assert list(session.scalars(select(AssetConfig))) == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"symbol": "BTC-USDT"},
        {"symbol": "ETH/USD"},
        {"base_asset": ""},
        {"quote_asset": "usd"},
        {"settings": []},
        {"exchange_config_id": "1"},
        {"unexpected": True},
    ],
)
def test_create_asset_strictly_validates_symbol_assets_and_settings(
    client: TestClient,
    exchange_id: int,
    overrides: dict[str, object],
) -> None:
    payload: dict[str, object] = {
        "exchange_config_id": exchange_id,
        "symbol": "BTC/USD",
        "base_asset": "BTC",
        "quote_asset": "USD",
        "settings": {},
    }
    payload.update(overrides)

    response = client.post("/api/v1/assets", json=payload)

    assert response.status_code == 422


def test_pause_requires_key_and_enqueues_idempotently(
    client: TestClient,
    session_factory: Callable[[], Session],
    exchange_id: int,
) -> None:
    asset_id = add_asset(session_factory, exchange_id)
    path = f"/api/v1/assets/{asset_id}/commands/pause"

    missing = client.post(path, json={})
    blank = client.post(path, json={}, headers={"Idempotency-Key": "   "})
    created = client.post(path, json={}, headers={"Idempotency-Key": "pause-once"})
    duplicate = client.post(path, json={}, headers={"Idempotency-Key": "pause-once"})

    assert missing.status_code == 422
    assert blank.status_code == 422
    assert created.status_code == 202
    assert created.json() == {
        "id": 1,
        "status": "pending",
        "type": "pause",
        "requested_at": "2026-09-17T11:00:00Z",
        "outcome": "created",
    }
    assert duplicate.status_code == 202
    assert duplicate.json() == {**created.json(), "outcome": "duplicate"}
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Command)) == 1


def test_resume_and_reconcile_enqueue_exact_intents_and_detect_conflict(
    client: TestClient,
    session_factory: Callable[[], Session],
    exchange_id: int,
) -> None:
    asset_id = add_asset(session_factory, exchange_id)
    headers = {"Idempotency-Key": "shared-intent-key"}

    resume = client.post(
        f"/api/v1/assets/{asset_id}/commands/resume", json={}, headers=headers
    )
    conflict = client.post(
        f"/api/v1/assets/{asset_id}/commands/pause", json={}, headers=headers
    )
    reconcile = client.post(
        "/api/v1/core/commands/reconcile",
        json={"asset_id": asset_id},
        headers={"Idempotency-Key": "reconcile-one"},
    )
    reconcile_all = client.post(
        "/api/v1/core/commands/reconcile",
        json={},
        headers={"Idempotency-Key": "reconcile-all"},
    )

    assert resume.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "idempotency key conflicts with existing command"}
    assert reconcile.status_code == 202
    assert reconcile_all.status_code == 202
    with session_factory() as session:
        commands = list(session.scalars(select(Command).order_by(Command.id)))
        assert [(command.command_type, command.payload) for command in commands] == [
            ("resume", {"asset_id": asset_id}),
            ("reconcile", {"asset_id": asset_id}),
            ("reconcile", {"asset_id": None}),
        ]


def test_live_close_requires_exact_confirmation_before_enqueue(
    session_factory: Callable[[], Session], exchange_id: int
) -> None:
    asset_id = add_asset(session_factory, exchange_id)
    live_client = TestClient(
        create_app(
            session_factory=session_factory,
            clock=lambda: NOW,
            trading_mode=TradingMode.LIVE,
        )
    )
    path = f"/api/v1/assets/{asset_id}/commands/close"
    headers = {"Idempotency-Key": "close-live"}

    mismatch = live_client.post(path, json={"confirmation": "yes"}, headers=headers)

    assert mismatch.status_code == 409
    assert mismatch.json() == {"detail": f"confirmation must exactly match CLOSE {asset_id}"}
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Command)) == 0

    confirmed = live_client.post(
        path, json={"confirmation": f"CLOSE {asset_id}"}, headers=headers
    )

    assert confirmed.status_code == 202
    with session_factory() as session:
        command = session.scalars(select(Command)).one()
        assert command.command_type == "close"
        assert command.payload == {"asset_id": asset_id, "trading_mode": "live"}


def test_get_command_supports_polling_with_generic_terminal_error(
    client: TestClient,
    session_factory: Callable[[], Session],
    exchange_id: int,
) -> None:
    asset_id = add_asset(session_factory, exchange_id)
    created = client.post(
        f"/api/v1/assets/{asset_id}/commands/pause",
        json={},
        headers={"Idempotency-Key": "poll-me"},
    )
    command_id = created.json()["id"]
    processed_at = datetime(2026, 9, 17, 11, 5, tzinfo=UTC)
    with session_factory() as session:
        command = session.get_one(Command, command_id)
        command.status = "failed"
        command.processed_at = processed_at
        command.error = "sqlite path /secret.db token=must-not-leak"
        session.commit()

    response = client.get(f"/api/v1/commands/{command_id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": command_id,
        "status": "failed",
        "type": "pause",
        "requested_at": "2026-09-17T11:00:00Z",
        "processed_at": "2026-09-17T11:05:00Z",
        "error": "command processing failed",
    }
    assert "must-not-leak" not in response.text
    assert client.get("/api/v1/commands/999999").status_code == 404


def test_mutations_reject_non_loopback_browser_origins(
    client: TestClient,
    session_factory: Callable[[], Session],
    exchange_id: int,
) -> None:
    asset_id = add_asset(session_factory, exchange_id)
    path = f"/api/v1/assets/{asset_id}/commands/pause"

    rejected = client.post(
        path,
        json={},
        headers={"Idempotency-Key": "evil-origin", "Origin": "https://evil.example"},
    )
    same_host = client.post(
        path,
        json={},
        headers={"Idempotency-Key": "same-host", "Origin": "http://testserver"},
    )
    loopback = client.post(
        path,
        json={},
        headers={"Idempotency-Key": "loopback", "Origin": "http://127.0.0.1:3000"},
    )

    assert rejected.status_code == 403
    assert rejected.json() == {"detail": "browser origin is not allowed"}
    assert same_host.status_code == 202
    assert loopback.status_code == 202
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Command)) == 2


def test_update_rejects_null_and_inconsistent_partial_configuration(
    client: TestClient,
    session_factory: Callable[[], Session],
    exchange_id: int,
) -> None:
    asset_id = add_asset(session_factory, exchange_id)

    null_value = client.patch(f"/api/v1/assets/{asset_id}", json={"enabled": None})
    inconsistent = client.patch(
        f"/api/v1/assets/{asset_id}", json={"symbol": "ETH/USD"}
    )

    assert null_value.status_code == 422
    assert inconsistent.status_code == 422
    with session_factory() as session:
        asset = session.get_one(AssetConfig, asset_id)
        assert asset.enabled is True
        assert asset.symbol == "BTC/USD"


def test_unknown_exchange_and_asset_mutations_return_not_found(
    client: TestClient,
    session_factory: Callable[[], Session],
) -> None:
    create_response = client.post(
        "/api/v1/assets",
        json={
            "exchange_config_id": 999999,
            "symbol": "BTC/USD",
            "base_asset": "BTC",
            "quote_asset": "USD",
            "settings": {},
        },
    )
    update_response = client.patch("/api/v1/assets/999999", json={"enabled": False})
    command_response = client.post(
        "/api/v1/assets/999999/commands/pause",
        json={},
        headers={"Idempotency-Key": "missing-asset"},
    )
    reconcile_response = client.post(
        "/api/v1/core/commands/reconcile",
        json={"asset_id": 999999},
        headers={"Idempotency-Key": "missing-reconcile"},
    )

    assert create_response.status_code == 404
    assert update_response.status_code == 404
    assert command_response.status_code == 404
    assert reconcile_response.status_code == 404
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Command)) == 0


def test_duplicate_asset_returns_conflict_and_later_mutation_succeeds(
    session_factory: Callable[[], Session], exchange_id: int
) -> None:
    add_asset(session_factory, exchange_id)
    error_client = TestClient(
        create_app(session_factory=session_factory, clock=lambda: NOW),
        raise_server_exceptions=False,
    )
    duplicate_payload = {
        "exchange_config_id": exchange_id,
        "symbol": "BTC/USD",
        "base_asset": "BTC",
        "quote_asset": "USD",
        "settings": {"private_label": "not-sensitive"},
    }

    failed = error_client.post("/api/v1/assets", json=duplicate_payload)
    recovered_payload = {
        **duplicate_payload,
        "symbol": "ETH/USD",
        "base_asset": "ETH",
    }
    recovered = error_client.post("/api/v1/assets", json=recovered_payload)

    assert failed.status_code == 409
    assert failed.json() == {"detail": "asset configuration already exists"}
    assert "sqlite" not in failed.text.lower()
    assert recovered.status_code == 201
    with session_factory() as session:
        assets = list(session.scalars(select(AssetConfig).order_by(AssetConfig.id)))
        assert [asset.symbol for asset in assets] == ["BTC/USD", "ETH/USD"]


def test_update_to_existing_exchange_symbol_returns_conflict(
    client: TestClient,
    session_factory: Callable[[], Session],
    exchange_id: int,
) -> None:
    add_asset(session_factory, exchange_id)
    with session_factory() as session:
        eth = AssetConfig(
            exchange_config_id=exchange_id,
            symbol="ETH/USD",
            base_asset="ETH",
            quote_asset="USD",
            settings={},
        )
        session.add(eth)
        session.commit()
        eth_id = eth.id

    response = client.patch(
        f"/api/v1/assets/{eth_id}",
        json={"symbol": "BTC/USD", "base_asset": "BTC"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "asset configuration already exists"}
    with session_factory() as session:
        unchanged = session.get_one(AssetConfig, eth_id)
        assert unchanged.symbol == "ETH/USD"
        assert unchanged.base_asset == "ETH"


def test_paper_close_requires_no_confirmation_body(
    client: TestClient,
    session_factory: Callable[[], Session],
    exchange_id: int,
) -> None:
    asset_id = add_asset(session_factory, exchange_id)

    response = client.post(
        f"/api/v1/assets/{asset_id}/commands/close",
        headers={"Idempotency-Key": "paper-close"},
    )

    assert response.status_code == 202
    with session_factory() as session:
        command = session.scalars(select(Command)).one()
        assert command.payload == {"asset_id": asset_id, "trading_mode": "paper"}
