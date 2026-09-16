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
