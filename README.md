# Quant Trading Platform

Local-first automated quant trading platform with an independent Python core and an optional React dashboard.

> The project is under active development. Do not use it with live funds until the live-readiness checklist has passed.

## Components

- `trading-core`: always-on scheduler and execution daemon
- `trading-api`: optional localhost FastAPI server
- `trading-cli`: local operational interface
- `frontend`: optional React + TypeScript dashboard

## Development

```bash
uv venv .venv --python 3.11
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m pytest
```

## Paper core

Task 21's composition root is deliberately paper-only. Both `testnet` and `live` are rejected
before an exchange object or daemon is created. Apply the schema and exercise one bounded real
iteration with:

```bash
.venv/bin/alembic upgrade head
.venv/bin/trading-core once
.venv/bin/trading-core run --poll-seconds 1
```

`trading-core` does not import or require FastAPI or the frontend. The API and dashboard are
optional processes and use the same `DATABASE_URL` contract. Production startup validates the
Alembic revision and never creates tables automatically.

Each enabled `asset_configs.settings` object must contain exactly this non-secret paper contract
(no unknown fields are accepted):

```json
{
  "timeframe": "1h",
  "strategy": "no-trade",
  "paper_open": "100",
  "paper_high": "101",
  "paper_low": "99",
  "paper_close": "100",
  "paper_volume": "10",
  "quantity": "0.01",
  "available_balance": "1000",
  "current_exposure": "0",
  "leverage": "1",
  "minimum_quantity": "0.001",
  "maximum_quantity": "10",
  "quantity_step": "0.001",
  "minimum_notional": "1",
  "max_notional": "1000",
  "max_position_quantity": "10",
  "max_data_age_seconds": 3600,
  "balance_usage_fraction": "0.95",
  "taker_fee_rate": "0",
  "parameters": {}
}
```

Decimal values may be JSON strings or numbers, but must be finite and satisfy the positive or
nonnegative constraints implied by their names. `timeframe` is a positive fixed `s/m/h/d/w`
interval. Only the trusted built-in `no-trade` strategy is registered. Secret-like keys are
rejected recursively. The `paper_*` OHLCV values are explicitly synthetic inputs to
`FakeExchange`; they are not market facts.

Every iteration first drains durable commands, refreshes enabled schedules, and then dispatches
only the latest UTC boundary. Dispatch reconciles exchange state before strategy execution. This
means startup does not replay a backlog of missed boundaries. A malformed asset is isolated while
valid assets continue, but the daemon heartbeat is degraded. `close` commands are currently
unsupported and fail with the generic sanitized command error; they are never marked complete.
