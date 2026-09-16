import io
import json
import logging

from quant_platform.logging import setup_logging


def test_setup_logging_emits_structured_json() -> None:
    stream = io.StringIO()
    setup_logging(level="INFO", stream=stream)

    logging.getLogger("quant_platform.test").info(
        "order submitted",
        extra={"symbol": "BTC/USD"},
    )

    event = json.loads(stream.getvalue())
    assert event["level"] == "INFO"
    assert event["logger"] == "quant_platform.test"
    assert event["message"] == "order submitted"
    assert event["symbol"] == "BTC/USD"
    assert "timestamp" in event


def test_setup_logging_recursively_redacts_secrets() -> None:
    stream = io.StringIO()
    setup_logging(stream=stream)

    logging.getLogger("quant_platform.test").info(
        "credentials loaded",
        extra={
            "api_key": "top-secret-key",
            "context": {
                "secret": "exchange-secret",
                "accounts": [
                    {
                        "passphrase": "exchange-passphrase",
                        "password": "account-password",
                        "token": "session-token",
                    }
                ],
            },
        },
    )

    event = json.loads(stream.getvalue())
    assert event["api_key"] == "[REDACTED]"
    assert event["context"]["secret"] == "[REDACTED]"
    account = event["context"]["accounts"][0]
    assert account == {
        "passphrase": "[REDACTED]",
        "password": "[REDACTED]",
        "token": "[REDACTED]",
    }
    assert "top-secret-key" not in stream.getvalue()
