import ipaddress
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from quant_platform.api.schemas import (
    AssetCreateRequest,
    AssetPage,
    AssetResponse,
    AssetUpdateRequest,
    CloseCommandRequest,
    CommandStatusResponse,
    EmptyCommandRequest,
    EnqueueCommandResponse,
    FillPage,
    OrderPage,
    PositionPage,
    ReconcileCommandRequest,
    RunPage,
    StatusResponse,
)
from quant_platform.config import DEFAULT_DATABASE_URL, TradingMode
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
from quant_platform.db.repositories import CommandIdempotencyConflictError, CommandRepository
from quant_platform.db.session import create_engine, create_session_factory

_EMPTY_COMMAND_BODY = Body(default_factory=EmptyCommandRequest)
_CLOSE_COMMAND_BODY = Body(default_factory=CloseCommandRequest)
_RECONCILE_COMMAND_BODY = Body(default_factory=ReconcileCommandRequest)
_IDEMPOTENCY_HEADER = Header(
    alias="Idempotency-Key",
    min_length=1,
    max_length=255,
    pattern=r".*\S.*",
)


class ApiRuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = DEFAULT_DATABASE_URL
    trading_api_host: str = "127.0.0.1"
    trading_api_port: int = 8000
    trading_api_unsafe_allow_non_loopback: bool = False
    trading_mode: TradingMode = TradingMode.PAPER


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


_SECRET_PARTS = (
    "apikey",
    "credential",
    "passphrase",
    "password",
    "privatekey",
    "secret",
    "token",
)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = "".join(character for character in key.lower() if character.isalnum())
            result[key] = (
                "[REDACTED]"
                if any(part in normalized for part in _SECRET_PARTS)
                else _redact(item)
            )
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _asset_payload(asset: AssetConfig) -> dict[str, Any]:
    return {
        "id": asset.id,
        "exchange_config_id": asset.exchange_config_id,
        "symbol": asset.symbol,
        "base_asset": asset.base_asset,
        "quote_asset": asset.quote_asset,
        "enabled": asset.enabled,
        "settings": _redact(asset.settings),
        "created_at": _timestamp(asset.created_at),
        "updated_at": _timestamp(asset.updated_at),
    }


def _position_payload(position: Position) -> dict[str, Any]:
    return {
        "id": position.id,
        "exchange_config_id": position.exchange_config_id,
        "asset_config_id": position.asset_config_id,
        "quantity": str(position.quantity),
        "average_entry_price": str(position.average_entry_price),
        "stored_realized_pnl": str(position.realized_pnl),
        "created_at": _timestamp(position.created_at),
        "updated_at": _timestamp(position.updated_at),
    }


def _run_payload(run: StrategyRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "asset_config_id": run.asset_config_id,
        "strategy_name": run.strategy_name,
        "status": run.status,
        "idempotency_key": run.idempotency_key,
        "parameters": _redact(run.parameters),
        "scheduled_boundary": _timestamp(run.scheduled_boundary),
        "started_at": _timestamp(run.started_at),
        "ended_at": _timestamp(run.ended_at),
        "created_at": _timestamp(run.created_at),
        "updated_at": _timestamp(run.updated_at),
    }


def _order_payload(order: Order) -> dict[str, Any]:
    return {
        "id": order.id,
        "exchange_config_id": order.exchange_config_id,
        "strategy_run_id": order.strategy_run_id,
        "signal_id": order.signal_id,
        "client_order_id": order.client_order_id,
        "exchange_order_id": order.exchange_order_id,
        "symbol": order.symbol,
        "side": order.side,
        "order_type": order.order_type,
        "status": order.status,
        "quantity": str(order.quantity),
        "price": str(order.price) if order.price is not None else None,
        "filled_quantity": str(order.filled_quantity),
        "submitted_at": _timestamp(order.submitted_at),
        "created_at": _timestamp(order.created_at),
        "updated_at": _timestamp(order.updated_at),
    }


def _fill_payload(fill: Fill) -> dict[str, Any]:
    return {
        "id": fill.id,
        "order_id": fill.order_id,
        "exchange_fill_id": fill.exchange_fill_id,
        "quantity": str(fill.quantity),
        "price": str(fill.price),
        "fee_amount": str(fill.fee_amount),
        "fee_currency": fill.fee_currency,
        "executed_at": _timestamp(fill.executed_at),
        "created_at": _timestamp(fill.created_at),
        "updated_at": _timestamp(fill.updated_at),
    }


def create_app(
    *,
    session_factory: Callable[[], Session],
    clock: Callable[[], datetime],
    trading_mode: TradingMode = TradingMode.PAPER,
) -> FastAPI:
    app = FastAPI(title="Quant Trading Platform Read API")

    @app.middleware("http")
    async def browser_origin_guard(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        origin = request.headers.get("origin")
        if origin is not None and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin_host = urlsplit(origin).hostname
            request_host = request.url.hostname
            allowed = origin_host is not None and (
                _is_loopback(origin_host)
                or (request_host is not None and origin_host.lower() == request_host.lower())
            )
            if not allowed:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "browser origin is not allowed"},
                )
        return await call_next(request)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, error: Exception) -> JSONResponse:
        del request, error
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        del request, error
        return JSONResponse(status_code=422, content={"detail": "request validation failed"})

    @app.get("/api/v1/status", response_model=StatusResponse)
    def status() -> dict[str, Any]:
        now = clock()
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("clock must return a UTC-aware datetime")
        with session_factory() as session:
            heartbeat = session.scalar(
                select(Heartbeat).where(Heartbeat.service_name == "trading-core")
            )
            counts = {
                state: session.scalar(
                    select(func.count()).select_from(Command).where(Command.status == state)
                )
                or 0
                for state in ("pending", "processing", "failed")
            }
        if heartbeat is None:
            healthy = False
            state = None
            last_seen_at = None
            age = None
            reason = "missing"
        else:
            state = heartbeat.status
            last_seen_at = heartbeat.last_seen_at
            if last_seen_at.tzinfo is None:
                last_seen_at = last_seen_at.replace(tzinfo=UTC)
            age = now - last_seen_at
            healthy = state == "running" and timedelta(0) <= age <= timedelta(seconds=30)
            if age < timedelta(0):
                reason = "future"
            elif state != "running":
                reason = state
            elif age > timedelta(seconds=30):
                reason = "stale"
            else:
                reason = None
        overall = "healthy" if healthy else (reason or "unhealthy")
        return {
            "status": overall,
            "healthy": healthy,
            "checked_at": _timestamp(now),
            "heartbeat": {
                "service_name": "trading-core",
                "state": state,
                "last_seen_at": _timestamp(last_seen_at),
                "age_seconds": age.total_seconds() if age is not None else None,
                "reason": reason,
            },
            "queue": counts,
        }

    @app.get("/api/v1/assets", response_model=AssetPage)
    def assets(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        with session_factory() as session:
            total = session.scalar(select(func.count()).select_from(AssetConfig)) or 0
            rows = session.scalars(
                select(AssetConfig).order_by(AssetConfig.id).limit(limit).offset(offset)
            ).all()
        return {
            "items": [_asset_payload(row) for row in rows],
            "limit": limit,
            "offset": offset,
            "total": total,
        }

    @app.get("/api/v1/assets/{asset_id}", response_model=AssetResponse)
    def asset(asset_id: int) -> dict[str, Any]:
        with session_factory() as session:
            row = session.get(AssetConfig, asset_id)
            if row is None:
                raise HTTPException(status_code=404, detail="asset not found")
            return _asset_payload(row)

    @app.post("/api/v1/assets", response_model=AssetResponse, status_code=201)
    def create_asset(request: AssetCreateRequest) -> dict[str, Any]:
        try:
            with session_factory() as session, session.begin():
                if session.get(ExchangeConfig, request.exchange_config_id) is None:
                    raise HTTPException(
                        status_code=404, detail="exchange configuration not found"
                    )
                row = AssetConfig(**request.model_dump())
                session.add(row)
                session.flush()
                return _asset_payload(row)
        except IntegrityError:
            raise HTTPException(
                status_code=409, detail="asset configuration already exists"
            ) from None

    @app.patch("/api/v1/assets/{asset_id}", response_model=AssetResponse)
    def update_asset(asset_id: int, request: AssetUpdateRequest) -> dict[str, Any]:
        try:
            with session_factory() as session, session.begin():
                row = session.get(AssetConfig, asset_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="asset not found")
                changes = request.model_dump(exclude_unset=True)
                exchange_id = changes.get("exchange_config_id")
                if exchange_id is not None and session.get(ExchangeConfig, exchange_id) is None:
                    raise HTTPException(
                        status_code=404, detail="exchange configuration not found"
                    )
                symbol = changes.get("symbol", row.symbol)
                base_asset = changes.get("base_asset", row.base_asset)
                quote_asset = changes.get("quote_asset", row.quote_asset)
                if symbol != f"{base_asset}/{quote_asset}":
                    raise HTTPException(status_code=422, detail="request validation failed")
                for field, value in changes.items():
                    setattr(row, field, value)
                session.flush()
                return _asset_payload(row)
        except IntegrityError:
            raise HTTPException(
                status_code=409, detail="asset configuration already exists"
            ) from None

    def enqueue_command(
        command_type: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            with session_factory() as session, session.begin():
                asset_id = payload.get("asset_id")
                if asset_id is not None and session.get(AssetConfig, asset_id) is None:
                    raise HTTPException(status_code=404, detail="asset not found")
                result = CommandRepository(session).enqueue(
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=payload,
                    requested_at=clock(),
                )
                return {
                    "id": result.command.id,
                    "status": result.command.status,
                    "type": result.command.command_type,
                    "requested_at": _timestamp(result.command.requested_at),
                    "outcome": result.outcome.value,
                }
        except CommandIdempotencyConflictError:
            raise HTTPException(
                status_code=409,
                detail="idempotency key conflicts with existing command",
            ) from None

    @app.post(
        "/api/v1/assets/{asset_id}/commands/pause",
        response_model=EnqueueCommandResponse,
        status_code=202,
    )
    def pause_asset(
        asset_id: int,
        request: EmptyCommandRequest = _EMPTY_COMMAND_BODY,
        idempotency_key: str = _IDEMPOTENCY_HEADER,
    ) -> dict[str, Any]:
        del request
        return enqueue_command("pause", idempotency_key, {"asset_id": asset_id})

    @app.post(
        "/api/v1/assets/{asset_id}/commands/resume",
        response_model=EnqueueCommandResponse,
        status_code=202,
    )
    def resume_asset(
        asset_id: int,
        request: EmptyCommandRequest = _EMPTY_COMMAND_BODY,
        idempotency_key: str = _IDEMPOTENCY_HEADER,
    ) -> dict[str, Any]:
        del request
        return enqueue_command("resume", idempotency_key, {"asset_id": asset_id})

    @app.post(
        "/api/v1/assets/{asset_id}/commands/close",
        response_model=EnqueueCommandResponse,
        status_code=202,
    )
    def close_asset(
        asset_id: int,
        request: CloseCommandRequest = _CLOSE_COMMAND_BODY,
        idempotency_key: str = _IDEMPOTENCY_HEADER,
    ) -> dict[str, Any]:
        with session_factory() as session:
            if session.get(AssetConfig, asset_id) is None:
                raise HTTPException(status_code=404, detail="asset not found")
        expected = f"CLOSE {asset_id}"
        if trading_mode is TradingMode.LIVE and request.confirmation != expected:
            raise HTTPException(
                status_code=409,
                detail=f"confirmation must exactly match {expected}",
            )
        return enqueue_command(
            "close",
            idempotency_key,
            {"asset_id": asset_id, "trading_mode": trading_mode.value},
        )

    @app.post(
        "/api/v1/core/commands/reconcile",
        response_model=EnqueueCommandResponse,
        status_code=202,
    )
    def reconcile_core(
        request: ReconcileCommandRequest = _RECONCILE_COMMAND_BODY,
        idempotency_key: str = _IDEMPOTENCY_HEADER,
    ) -> dict[str, Any]:
        return enqueue_command(
            "reconcile", idempotency_key, {"asset_id": request.asset_id}
        )

    @app.get("/api/v1/commands/{command_id}", response_model=CommandStatusResponse)
    def command_status(command_id: int) -> dict[str, Any]:
        with session_factory() as session:
            command = session.get(Command, command_id)
            if command is None:
                raise HTTPException(status_code=404, detail="command not found")
            return {
                "id": command.id,
                "status": command.status,
                "type": command.command_type,
                "requested_at": _timestamp(command.requested_at),
                "processed_at": _timestamp(command.processed_at),
                "error": "command processing failed" if command.error is not None else None,
            }

    @app.get("/api/v1/positions", response_model=PositionPage)
    def positions(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        with session_factory() as session:
            total = session.scalar(select(func.count()).select_from(Position)) or 0
            rows = session.scalars(
                select(Position).order_by(Position.id).limit(limit).offset(offset)
            ).all()
        return {
            "items": [_position_payload(row) for row in rows],
            "limit": limit,
            "offset": offset,
            "total": total,
        }

    @app.get("/api/v1/runs", response_model=RunPage)
    def runs(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        with session_factory() as session:
            total = session.scalar(select(func.count()).select_from(StrategyRun)) or 0
            rows = session.scalars(
                select(StrategyRun).order_by(StrategyRun.id).limit(limit).offset(offset)
            ).all()
        return {
            "items": [_run_payload(row) for row in rows],
            "limit": limit,
            "offset": offset,
            "total": total,
        }

    @app.get("/api/v1/orders", response_model=OrderPage)
    def orders(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        with session_factory() as session:
            total = session.scalar(select(func.count()).select_from(Order)) or 0
            rows = session.scalars(
                select(Order).order_by(Order.id).limit(limit).offset(offset)
            ).all()
        return {
            "items": [_order_payload(row) for row in rows],
            "limit": limit,
            "offset": offset,
            "total": total,
        }

    @app.get("/api/v1/fills", response_model=FillPage)
    def fills(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        with session_factory() as session:
            total = session.scalar(select(func.count()).select_from(Fill)) or 0
            rows = session.scalars(select(Fill).order_by(Fill.id).limit(limit).offset(offset)).all()
        return {
            "items": [_fill_payload(row) for row in rows],
            "limit": limit,
            "offset": offset,
            "total": total,
        }

    return app


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def run(
    host: str | None = None,
    *,
    unsafe_allow_non_loopback: bool = False,
) -> None:
    """Run the local read API, refusing remote exposure unless explicitly opted in."""
    import uvicorn

    settings = ApiRuntimeSettings()
    selected_host = host or settings.trading_api_host
    unsafe_enabled = (
        unsafe_allow_non_loopback or settings.trading_api_unsafe_allow_non_loopback
    )
    if not _is_loopback(selected_host) and not unsafe_enabled:
        raise ValueError("non-loopback API host requires explicit unsafe opt-in")

    engine = create_engine(settings.database_url)
    app = create_app(
        session_factory=create_session_factory(engine),
        clock=lambda: datetime.now(UTC),
        trading_mode=settings.trading_mode,
    )
    uvicorn.run(app, host=selected_host, port=settings.trading_api_port)
