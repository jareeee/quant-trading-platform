import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer

from quant_platform.config import Settings
from quant_platform.core.daemon import AlreadyRunningError, CoreDaemon, InstanceLock
from quant_platform.core.heartbeat import HeartbeatService
from quant_platform.core.runtime import CoreConfigurationError, build_paper_core

app = typer.Typer(
    name="trading-core",
    help="Run the independent deterministic paper trading core.",
    no_args_is_help=True,
)


def _parse_at(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise typer.BadParameter("must be an ISO-8601 UTC timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise typer.BadParameter("must be an ISO-8601 UTC timestamp")
    return parsed.astimezone(UTC)


def _configuration_error(error: Exception) -> None:
    typer.echo(str(error), err=True)
    raise typer.Exit(code=1)


@app.command()
def once(
    at: Annotated[
        str | None,
        typer.Option(help="Deterministic UTC iteration time (ISO-8601)."),
    ] = None,
) -> None:
    """Perform one genuine command, reconciliation, and schedule iteration."""
    now = _parse_at(at)
    try:
        core = build_paper_core(settings=Settings(), clock=lambda: now)
    except (CoreConfigurationError, ValueError) as error:
        _configuration_error(error)
        return
    try:
        report = core.iterate()
        typer.echo(
            json.dumps(
                {
                    "commands_completed": report.commands_completed,
                    "commands_failed": report.commands_failed,
                    "dispatched": report.dispatched,
                    "duplicates": report.duplicates,
                    "invalid_asset_ids": list(report.invalid_asset_ids),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        if report.commands_failed or report.invalid_asset_ids:
            raise typer.Exit(code=1)
    finally:
        core.close()


@app.command()
def run(
    poll_seconds: Annotated[
        float,
        typer.Option(min=0.01, help="Seconds between core iterations."),
    ] = 1.0,
    lock_file: Annotated[
        Path,
        typer.Option(help="Single-instance advisory lock path."),
    ] = Path("data/trading-core.lock"),
) -> None:
    """Run continuously under lock, heartbeat, and graceful signal handling."""
    try:
        settings = Settings()
        core = build_paper_core(settings=settings)
    except (CoreConfigurationError, ValueError) as error:
        _configuration_error(error)
        return
    heartbeat = HeartbeatService(
        core.session_factory,
        "trading-core",
        clock=lambda: datetime.now(UTC),
    )
    daemon = CoreDaemon(
        InstanceLock(lock_file),
        heartbeat,
        core.daemon_iteration,
        poll_interval=timedelta(seconds=poll_seconds),
    )
    try:
        daemon.run()
    except AlreadyRunningError as error:
        _configuration_error(error)
    finally:
        core.close()


if __name__ == "__main__":  # pragma: no cover
    app()
