import json
import logging
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TextIO

_SENSITIVE_FIELDS = frozenset(
    {
        "apikey",
        "secret",
        "secretkey",
        "passphrase",
        "password",
        "token",
        "accesstoken",
        "refreshtoken",
        "privatekey",
    }
)
_STANDARD_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "asctime",
    "message",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in _STANDARD_LOG_RECORD_FIELDS
            }
        )
        return json.dumps(_redact(event), default=str)


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


def setup_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level.upper())
