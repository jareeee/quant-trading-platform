import os
import subprocess
from pathlib import Path

from alembic.config import Config
from typer.testing import CliRunner

from alembic import command
from quant_platform.api.app import ApiRuntimeSettings
from quant_platform.cli.core import app
from quant_platform.config import Settings


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
