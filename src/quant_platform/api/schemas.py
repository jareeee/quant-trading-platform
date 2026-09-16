from typing import Any

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
