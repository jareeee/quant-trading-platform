from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Annotated, ParamSpec
from uuid import uuid4

import typer
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from quant_platform.config import Settings, TradingMode
from quant_platform.db.models import AssetConfig, Command, Heartbeat, Position
from quant_platform.db.repositories import CommandIdempotencyConflictError, CommandRepository
from quant_platform.db.session import create_engine, create_session_factory

SessionFactory = Callable[[], Session]
Clock = Callable[[], datetime]
P = ParamSpec("P")
_SENSITIVE_FIELDS = frozenset(
    {
        "accesstoken",
        "apikey",
        "passphrase",
        "password",
        "privatekey",
        "refreshtoken",
        "secret",
        "secretkey",
        "token",
    }
)


@dataclass(frozen=True, slots=True)
class CliServices:
    session_factory: SessionFactory
    trading_mode: TradingMode
    clock: Clock


def _default_services() -> CliServices:
    settings = Settings()
    engine = create_engine(settings.database_url)
    return CliServices(
        session_factory=create_session_factory(engine),
        trading_mode=settings.trading_mode,
        clock=lambda: datetime.now(UTC),
    )


def _normalized_field_name(key: object) -> str:
    return "".join(character for character in str(key).lower() if character.isalnum())


def _redact(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if _normalized_field_name(key) in _SENSITIVE_FIELDS
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def _write_json(value: object) -> None:
    typer.echo(json.dumps(_redact(value), sort_keys=True, separators=(",", ":")))


def _sanitize_database_errors(function: Callable[P, None]) -> Callable[P, None]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            function(*args, **kwargs)
        except SQLAlchemyError:
            _write_json({"error": "database_operation_failed"})
            raise typer.Exit(code=1) from None

    return wrapped


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def create_app(services: CliServices | None = None) -> typer.Typer:
    cli = typer.Typer(no_args_is_help=True)

    def get_services() -> CliServices:
        return services if services is not None else _default_services()

    @cli.callback()
    def main() -> None:
        """Inspect local state and enqueue operational commands."""

    @cli.command()
    @_sanitize_database_errors
    def status(
        max_age_seconds: Annotated[
            float,
            typer.Option(min=0.001, help="Maximum healthy heartbeat age in seconds."),
        ] = 30.0,
    ) -> None:
        resolved = get_services()
        now = resolved.clock()
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("clock must return a UTC-aware datetime")
        counts = {name: 0 for name in ("completed", "failed", "pending", "processing")}
        with resolved.session_factory() as session:
            heartbeat = session.scalar(
                select(Heartbeat).where(Heartbeat.service_name == "core")
            )
            rows = session.execute(
                select(Command.status, func.count(Command.id)).group_by(Command.status)
            )
            for command_status, count in rows:
                if command_status in counts:
                    counts[command_status] = count
        last_seen_at = heartbeat.last_seen_at if heartbeat is not None else None
        if last_seen_at is not None and last_seen_at.tzinfo is None:
            last_seen_at = last_seen_at.replace(tzinfo=UTC)
        age = now - last_seen_at if last_seen_at is not None else None
        if heartbeat is None:
            reason = "missing"
        elif age is not None and age < timedelta(0):
            reason = "future"
        elif heartbeat.status != "running":
            reason = heartbeat.status
        elif age is not None and age > timedelta(seconds=max_age_seconds):
            reason = "stale"
        else:
            reason = None
        healthy = reason is None
        _write_json(
            {
                "command_counts": counts,
                "healthy": healthy,
                "heartbeat": {
                    "age_seconds": age.total_seconds() if age is not None else None,
                    "last_seen_at": _iso_utc(last_seen_at),
                    "reason": reason,
                    "service_name": "core",
                    "status": heartbeat.status if heartbeat is not None else None,
                },
            }
        )
        if not healthy:
            raise typer.Exit(code=1)

    @cli.command()
    @_sanitize_database_errors
    def assets() -> None:
        resolved = get_services()
        with resolved.session_factory() as session:
            rows = session.execute(
                select(AssetConfig, Position)
                .outerjoin(
                    Position,
                    (Position.asset_config_id == AssetConfig.id)
                    & (Position.exchange_config_id == AssetConfig.exchange_config_id),
                )
                .order_by(AssetConfig.id)
            ).all()
            output = []
            for asset, position in rows:
                position_output = None
                if position is not None:
                    position_output = {
                        "average_entry_price": str(position.average_entry_price),
                        "quantity": str(position.quantity),
                        "realized_pnl": str(position.realized_pnl),
                    }
                output.append(
                    {
                        "base_asset": asset.base_asset,
                        "enabled": asset.enabled,
                        "exchange_config_id": asset.exchange_config_id,
                        "id": asset.id,
                        "position": position_output,
                        "quote_asset": asset.quote_asset,
                        "settings": asset.settings,
                        "symbol": asset.symbol,
                    }
                )
        _write_json({"assets": output})

    def enqueue_command(
        command_type: str,
        asset_id: int | None,
        idempotency_key: str | None,
        *,
        include_trading_mode: bool = False,
    ) -> None:
        resolved = get_services()
        key = idempotency_key or str(uuid4())
        payload: dict[str, object] = {"asset_id": asset_id}
        if include_trading_mode:
            payload["trading_mode"] = resolved.trading_mode.value
        with resolved.session_factory() as session:
            if asset_id is not None and session.get(AssetConfig, asset_id) is None:
                _write_json({"asset_id": asset_id, "error": "asset_not_found"})
                raise typer.Exit(code=1)
            try:
                result = CommandRepository(session).enqueue(
                    command_type=command_type,
                    idempotency_key=key,
                    payload=payload,
                    requested_at=resolved.clock(),
                )
            except CommandIdempotencyConflictError:
                session.rollback()
                _write_json({"error": "idempotency_conflict"})
                raise typer.Exit(code=1) from None
            session.commit()
        _write_json(
            {
                "command_id": result.command.id,
                "command_type": result.command.command_type,
                "idempotency_key": result.command.idempotency_key,
                "outcome": result.outcome.value,
            }
        )

    @cli.command()
    @_sanitize_database_errors
    def pause(
        asset_id: int,
        idempotency_key: Annotated[str | None, typer.Option()] = None,
    ) -> None:
        enqueue_command("pause", asset_id, idempotency_key)

    @cli.command()
    @_sanitize_database_errors
    def resume(
        asset_id: int,
        idempotency_key: Annotated[str | None, typer.Option()] = None,
    ) -> None:
        enqueue_command("resume", asset_id, idempotency_key)

    @cli.command()
    @_sanitize_database_errors
    def reconcile(
        asset_id: Annotated[int | None, typer.Option()] = None,
        idempotency_key: Annotated[str | None, typer.Option()] = None,
    ) -> None:
        enqueue_command("reconcile", asset_id, idempotency_key)

    @cli.command()
    @_sanitize_database_errors
    def close(
        asset_id: int,
        idempotency_key: Annotated[str | None, typer.Option()] = None,
    ) -> None:
        resolved = get_services()
        if resolved.trading_mode is TradingMode.LIVE:
            with resolved.session_factory() as session:
                if session.get(AssetConfig, asset_id) is None:
                    _write_json({"asset_id": asset_id, "error": "asset_not_found"})
                    raise typer.Exit(code=1)
            expected = f"CLOSE {asset_id}"
            confirmation = typer.prompt(f"Type {expected} to confirm live close")
            if confirmation != expected:
                _write_json({"error": "confirmation_mismatch"})
                raise typer.Exit(code=1)
        enqueue_command(
            "close",
            asset_id,
            idempotency_key,
            include_trading_mode=True,
        )

    return cli


app = create_app()
