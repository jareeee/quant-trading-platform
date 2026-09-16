from enum import StrEnum

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(StrEnum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    trading_mode: TradingMode = TradingMode.PAPER
    database_url: str = "sqlite:///data/trading.db"
    log_level: str = "INFO"
    live_trading_enabled: bool = False
    exchange_api_key: SecretStr | None = None
    exchange_secret: SecretStr | None = None
    exchange_passphrase: SecretStr | None = None

    @field_validator("live_trading_enabled", mode="before")
    @classmethod
    def require_literal_live_trading_flag(cls, value: object) -> object:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        raise ValueError("LIVE_TRADING_ENABLED must be exactly true or false")

    @model_validator(mode="after")
    def require_live_trading_enablement(self) -> "Settings":
        if self.trading_mode is not TradingMode.LIVE:
            return self
        if not self.live_trading_enabled:
            raise ValueError("LIVE_TRADING_ENABLED=true is required for live mode")

        credentials = {
            "EXCHANGE_API_KEY": self.exchange_api_key,
            "EXCHANGE_SECRET": self.exchange_secret,
            "EXCHANGE_PASSPHRASE": self.exchange_passphrase,
        }
        missing = [
            name
            for name, value in credentials.items()
            if value is None or not value.get_secret_value().strip()
        ]
        if missing:
            raise ValueError(f"Missing live exchange credentials: {', '.join(missing)}")
        return self
