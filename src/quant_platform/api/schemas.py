from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SECRET_KEY_MARKERS = (
    "apikey",
    "credential",
    "passphrase",
    "password",
    "privatekey",
    "secret",
    "token",
)


def _reject_secret_keys(value: Any) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = "".join(character for character in key.lower() if character.isalnum())
            if any(marker in normalized for marker in _SECRET_KEY_MARKERS):
                raise ValueError("secret-like settings keys are forbidden")
            _reject_secret_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_secret_keys(item)
    return value


def _validate_asset_code(value: str | None) -> str | None:
    invalid = value is not None and (
        not value or len(value) > 20 or not value.isalnum() or not value.isupper()
    )
    if invalid:
        raise ValueError("asset codes must be uppercase alphanumeric values")
    return value


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AssetCreateRequest(StrictRequestModel):
    exchange_config_id: int = Field(gt=0)
    symbol: str
    base_asset: str
    quote_asset: str
    enabled: bool = True
    settings: dict[str, Any] = Field(default_factory=dict)

    _base_asset_is_valid = field_validator("base_asset")(_validate_asset_code)
    _quote_asset_is_valid = field_validator("quote_asset")(_validate_asset_code)
    _settings_have_no_secrets = field_validator("settings")(_reject_secret_keys)

    @model_validator(mode="after")
    def symbol_matches_assets(self) -> "AssetCreateRequest":
        if self.symbol != f"{self.base_asset}/{self.quote_asset}":
            raise ValueError("symbol must match base_asset/quote_asset")
        return self


class AssetUpdateRequest(StrictRequestModel):
    exchange_config_id: int | None = Field(default=None, gt=0)
    symbol: str | None = None
    base_asset: str | None = None
    quote_asset: str | None = None
    enabled: bool | None = None
    settings: dict[str, Any] | None = None

    _base_asset_is_valid = field_validator("base_asset")(_validate_asset_code)
    _quote_asset_is_valid = field_validator("quote_asset")(_validate_asset_code)
    _settings_have_no_secrets = field_validator("settings")(_reject_secret_keys)

    @model_validator(mode="after")
    def supplied_values_are_not_null(self) -> "AssetUpdateRequest":
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("updated fields cannot be null")
        return self


class EmptyCommandRequest(StrictRequestModel):
    pass


class ReconcileCommandRequest(StrictRequestModel):
    asset_id: int | None = Field(default=None, gt=0)


class CloseCommandRequest(StrictRequestModel):
    confirmation: str | None = None


class EnqueueCommandResponse(ApiModel):
    id: int
    status: str
    type: str
    requested_at: str
    outcome: Literal["created", "duplicate"]


class CommandStatusResponse(ApiModel):
    id: int
    status: str
    type: str
    requested_at: str
    processed_at: str | None
    error: str | None


class QueueCounts(ApiModel):
    pending: int
    processing: int
    failed: int


class HeartbeatState(ApiModel):
    service_name: str
    state: str | None
    last_seen_at: str | None
    age_seconds: float | None
    reason: str | None


class StatusResponse(ApiModel):
    status: str
    healthy: bool
    checked_at: str
    heartbeat: HeartbeatState
    queue: QueueCounts


class PageMetadata(ApiModel):
    limit: int
    offset: int
    total: int


class AssetResponse(ApiModel):
    id: int
    exchange_config_id: int
    symbol: str
    base_asset: str
    quote_asset: str
    enabled: bool
    settings: dict[str, Any]
    created_at: str
    updated_at: str


class AssetPage(PageMetadata):
    items: list[AssetResponse]


class PositionResponse(ApiModel):
    id: int
    exchange_config_id: int
    asset_config_id: int
    quantity: str
    average_entry_price: str
    stored_realized_pnl: str
    created_at: str
    updated_at: str


class PositionPage(PageMetadata):
    items: list[PositionResponse]


class RunResponse(ApiModel):
    id: int
    asset_config_id: int
    strategy_name: str
    status: str
    idempotency_key: str
    parameters: dict[str, Any]
    scheduled_boundary: str
    started_at: str
    ended_at: str | None
    created_at: str
    updated_at: str


class RunPage(PageMetadata):
    items: list[RunResponse]


class OrderResponse(ApiModel):
    id: int
    exchange_config_id: int
    strategy_run_id: int | None
    signal_id: int | None
    client_order_id: str
    exchange_order_id: str | None
    symbol: str
    side: str
    order_type: str
    status: str
    quantity: str
    price: str | None
    filled_quantity: str
    submitted_at: str | None
    created_at: str
    updated_at: str


class OrderPage(PageMetadata):
    items: list[OrderResponse]


class FillResponse(ApiModel):
    id: int
    order_id: int
    exchange_fill_id: str
    quantity: str
    price: str
    fee_amount: str
    fee_currency: str | None
    executed_at: str
    created_at: str
    updated_at: str


class FillPage(PageMetadata):
    items: list[FillResponse]
