from pathlib import Path

import pytest
from pydantic import ValidationError

from quant_platform.config import Settings, TradingMode


def make_settings(*, env_file: Path | None = None, **values: object) -> Settings:
    return Settings(_env_file=env_file, **values)  # type: ignore[call-arg, arg-type]


def test_settings_loads_trading_mode_from_dotenv(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("TRADING_MODE=testnet\n")

    settings = make_settings(env_file=env_file)

    assert settings.trading_mode is TradingMode.TESTNET


def test_settings_loads_runtime_values_from_dotenv(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=sqlite:///tmp/test.db\nLOG_LEVEL=DEBUG\n")

    settings = make_settings(env_file=env_file)

    assert settings.database_url == "sqlite:///tmp/test.db"
    assert settings.log_level == "DEBUG"


def test_live_mode_requires_explicit_enablement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)

    with pytest.raises(ValidationError, match="LIVE_TRADING_ENABLED"):
        make_settings(trading_mode=TradingMode.LIVE)


def test_live_mode_rejects_nonliteral_enablement(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TRADING_MODE=live\n"
        "LIVE_TRADING_ENABLED=yes\n"
        "EXCHANGE_API_KEY=api-key\n"
        "EXCHANGE_SECRET=secret\n"
        "EXCHANGE_PASSPHRASE=passphrase\n"
    )

    with pytest.raises(ValidationError, match="must be exactly true"):
        make_settings(env_file=env_file)


def test_live_mode_requires_exchange_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    credential_names = (
        "EXCHANGE_API_KEY",
        "EXCHANGE_SECRET",
        "EXCHANGE_PASSPHRASE",
    )
    for name in credential_names:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError) as error:
        make_settings(
            trading_mode=TradingMode.LIVE,
            live_trading_enabled=True,
        )

    message = str(error.value)
    assert all(name in message for name in credential_names)


def test_live_mode_rejects_blank_exchange_credentials() -> None:
    with pytest.raises(ValidationError, match="EXCHANGE_SECRET"):
        make_settings(
            trading_mode=TradingMode.LIVE,
            live_trading_enabled=True,
            exchange_api_key="api-key",
            exchange_secret="   ",
            exchange_passphrase="passphrase",
        )
