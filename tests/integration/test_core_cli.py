import os
import subprocess
from pathlib import Path

from alembic.config import Config
from pytest import MonkeyPatch
from typer.testing import CliRunner

from alembic import command
from quant_platform.api.app import ApiRuntimeSettings
from quant_platform.cli.core import app
from quant_platform.config import Settings
from quant_platform.db.models import Command, Heartbeat, Signal, StrategyRun
from quant_platform.db.session import create_engine, create_session_factory


def test_core_and_api_share_default_database_contract() -> None:
    core = Settings.model_construct()
    api = ApiRuntimeSettings.model_construct()

    assert core.database_url == api.database_url


def test_trading_core_help_is_operational() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "once" in result.stdout
    assert "run" in result.stdout


def test_once_fails_safely_when_schema_is_missing(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'unmigrated.db'}"
    result = CliRunner().invoke(
        app,
        ["once", "--at", "2026-09-23T12:00:00Z"],
        env={"DATABASE_URL": database_url, "TRADING_MODE": "paper"},
    )

    assert result.exit_code == 1
    assert "run alembic upgrade head" in result.stderr
    assert "Traceback" not in result.stderr


def test_check_constructs_and_closes_without_runtime_writes(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    database_url = f"sqlite:///{tmp_path / 'check.db'}"
    config = Config(root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    result = CliRunner().invoke(
        app,
        ["check"],
        env={"DATABASE_URL": database_url, "TRADING_MODE": "paper"},
    )

    assert result.exit_code == 0
    assert result.stdout == "configuration and schema are ready\n"
    engine = create_engine(database_url)
    with create_session_factory(engine)() as session:
        assert session.query(Command).count() == 0
        assert session.query(Heartbeat).count() == 0
        assert session.query(Signal).count() == 0
        assert session.query(StrategyRun).count() == 0
    engine.dispose()


def test_check_rejects_missing_schema_without_traceback(tmp_path: Path) -> None:
    database_path = tmp_path / "missing.db"
    result = CliRunner().invoke(
        app,
        ["check"],
        env={"DATABASE_URL": f"sqlite:///{database_path}", "TRADING_MODE": "paper"},
    )

    assert result.exit_code == 1
    assert "run alembic upgrade head" in result.stderr
    assert "Traceback" not in result.stderr
    assert not database_path.exists()


def test_check_rejects_non_paper_mode_without_exposing_credentials(tmp_path: Path) -> None:
    credential = "must-not-appear"
    result = CliRunner().invoke(
        app,
        ["check"],
        env={
            "DATABASE_URL": f"sqlite:///{tmp_path / 'unused.db'}",
            "TRADING_MODE": "live",
            "LIVE_TRADING_ENABLED": "true",
            "EXCHANGE_API_KEY": credential,
            "EXCHANGE_SECRET": credential,
            "EXCHANGE_PASSPHRASE": credential,
        },
    )

    assert result.exit_code == 1
    assert "paper mode only" in result.stderr
    assert credential not in result.stderr
    assert "Traceback" not in result.stderr


def test_check_sanitizes_settings_validation_errors(tmp_path: Path) -> None:
    invalid = "invalid-mode-must-not-be-echoed"
    result = CliRunner().invoke(
        app,
        ["check"],
        env={"DATABASE_URL": f"sqlite:///{tmp_path / 'unused.db'}", "TRADING_MODE": invalid},
    )

    assert result.exit_code == 1
    assert result.stderr == "invalid core configuration\n"
    assert invalid not in result.stderr


def test_check_rejects_non_sqlite_database_without_connecting_or_leaking_url() -> None:
    secret_url = "postgresql://operator:must-not-appear@example.invalid/trading"
    result = CliRunner().invoke(
        app,
        ["check"],
        env={"DATABASE_URL": secret_url, "TRADING_MODE": "paper"},
    )

    assert result.exit_code == 1
    assert result.stderr == "only SQLite database URLs are supported\n"
    assert secret_url not in result.stderr


def test_check_sanitizes_malformed_database_url() -> None:
    malformed = "not-a-database-url-must-not-appear"
    result = CliRunner().invoke(
        app,
        ["check"],
        env={"DATABASE_URL": malformed, "TRADING_MODE": "paper"},
    )

    assert result.exit_code == 1
    assert result.stderr == "invalid core configuration\n"
    assert malformed not in result.stderr


def test_check_sanitizes_composition_close_failure(monkeypatch: MonkeyPatch) -> None:
    class BrokenCore:
        def close(self) -> None:
            raise RuntimeError("close-detail-must-not-appear")

    monkeypatch.setattr("quant_platform.cli.core.build_paper_core", lambda **_: BrokenCore())

    result = CliRunner().invoke(app, ["check"])

    assert result.exit_code == 1
    assert result.stderr == "invalid core configuration\n"
    assert "close-detail" not in result.stderr


def test_installed_entry_point_help_does_not_import_web_stack() -> None:
    executable = Path(__file__).parents[2] / ".venv" / "bin" / "trading-core"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")

    result = subprocess.run(
        [str(executable), "--help"],
        cwd=Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert result.returncode == 0
    assert "once" in result.stdout
    assert "fastapi" not in result.stdout.lower()


def test_installed_entry_point_performs_bounded_real_iteration(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    database_url = f"sqlite:///{tmp_path / 'entry-point.db'}"
    config = Config(root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    environment = os.environ.copy()
    environment.update({"DATABASE_URL": database_url, "TRADING_MODE": "paper"})

    result = subprocess.run(
        [
            str(root / ".venv" / "bin" / "trading-core"),
            "once",
            "--at",
            "2026-09-23T12:00:00Z",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert result.returncode == 0
    assert '"commands_completed":0' in result.stdout
    assert '"dispatched":0' in result.stdout
    assert "Traceback" not in result.stderr
