from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from quant_platform.db.models import AssetConfig


class AssetConfigRepository:
    """Persistence operations for configured trading assets."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        exchange_config_id: int,
        symbol: str,
        base_asset: str,
        quote_asset: str,
        enabled: bool = True,
        settings: dict[str, Any] | None = None,
    ) -> AssetConfig:
        config = AssetConfig(
            exchange_config_id=exchange_config_id,
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            enabled=enabled,
            settings={} if settings is None else settings,
        )
        self._session.add(config)
        self._session.flush()
        return config

    def get(self, asset_config_id: int) -> AssetConfig | None:
        return self._session.get(AssetConfig, asset_config_id)

    def list(self, *, enabled: bool | None = None) -> list[AssetConfig]:
        statement = select(AssetConfig).order_by(AssetConfig.id)
        if enabled is not None:
            statement = statement.where(AssetConfig.enabled == enabled)
        return list(self._session.scalars(statement))

    def update(
        self,
        asset_config_id: int,
        *,
        exchange_config_id: int | None = None,
        symbol: str | None = None,
        base_asset: str | None = None,
        quote_asset: str | None = None,
        enabled: bool | None = None,
        settings: dict[str, Any] | None = None,
    ) -> AssetConfig:
        config = self.get(asset_config_id)
        if config is None:
            raise LookupError(f"asset config {asset_config_id} does not exist")
        if exchange_config_id is not None:
            config.exchange_config_id = exchange_config_id
        if symbol is not None:
            config.symbol = symbol
        if base_asset is not None:
            config.base_asset = base_asset
        if quote_asset is not None:
            config.quote_asset = quote_asset
        if enabled is not None:
            config.enabled = enabled
        if settings is not None:
            config.settings = settings
        self._session.flush()
        return config

    def delete(self, asset_config_id: int) -> bool:
        config = self.get(asset_config_id)
        if config is None:
            return False
        self._session.delete(config)
        self._session.flush()
        return True
