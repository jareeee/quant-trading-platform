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
~/.hermes/bin/uv venv .venv --python 3.11
~/.hermes/bin/uv pip install --python .venv/bin/python -e '.[dev]'
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python -m pytest
```
