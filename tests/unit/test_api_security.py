import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from quant_platform.api.app import create_app, run
from quant_platform.db.base import Base
from quant_platform.db.session import create_engine, create_session_factory


@pytest.fixture
def session_factory(tmp_path: Path) -> Iterator[Callable[[], Session]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'security.db'}")
    Base.metadata.create_all(engine)
    yield create_session_factory(engine)
    engine.dispose()


def test_openapi_exposes_only_read_methods(
    session_factory: Callable[[], Session],
) -> None:
    app = create_app(
        session_factory=session_factory,
        clock=lambda: datetime(2026, 9, 17, tzinfo=UTC),
    )

    paths = app.openapi()["paths"]

    assert set(paths) == {
        "/api/v1/status",
        "/api/v1/assets",
        "/api/v1/assets/{asset_id}",
        "/api/v1/positions",
        "/api/v1/runs",
        "/api/v1/orders",
        "/api/v1/fills",
    }
    assert {method for operations in paths.values() for method in operations} <= {
        "get",
        "head",
        "options",
    }
    assert all(
        operations["get"]["responses"]["200"]["content"]["application/json"]["schema"].get(
            "$ref"
        )
        for operations in paths.values()
    )


def test_run_binds_loopback_by_default_without_opening_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append({"app": app, **kwargs}))
    monkeypatch.delenv("TRADING_API_HOST", raising=False)
    monkeypatch.delenv("TRADING_API_UNSAFE_ALLOW_NON_LOOPBACK", raising=False)

    run()

    assert len(calls) == 1
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8000


def test_run_rejects_non_loopback_without_explicit_unsafe_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append({"app": app, **kwargs}))

    with pytest.raises(ValueError, match="non-loopback"):
        run(host="0.0.0.0")

    assert calls == []
    run(host="0.0.0.0", unsafe_allow_non_loopback=True)
    assert calls[0]["host"] == "0.0.0.0"


def test_run_loads_database_host_and_port_from_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "DATABASE_URL=sqlite:///configured.db\n"
        "TRADING_API_HOST=localhost\n"
        "TRADING_API_PORT=8123\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    for name in ("DATABASE_URL", "TRADING_API_HOST", "TRADING_API_PORT"):
        monkeypatch.delenv(name, raising=False)

    engines: list[str] = []
    calls: list[dict[str, object]] = []

    def fake_create_engine(database_url: str) -> object:
        engines.append(database_url)
        return object()

    monkeypatch.setattr(
        "quant_platform.api.app.create_engine",
        fake_create_engine,
    )
    monkeypatch.setattr(
        "quant_platform.api.app.create_session_factory", lambda engine: lambda: None
    )
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append({"app": app, **kwargs}))

    run()

    assert engines == ["sqlite:///configured.db"]
    assert calls[0]["host"] == "localhost"
    assert calls[0]["port"] == 8123


def test_api_import_isolated_from_exchange_and_ccxt() -> None:
    code = (
        "import sys; import quant_platform.api.app; "
        "assert 'ccxt' not in sys.modules; "
        "assert not any(name == 'quant_platform.exchange' or "
        "name.startswith('quant_platform.exchange.') for name in sys.modules)"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
