import math
from decimal import Decimal

import pytest
from pydantic import ValidationError

from quant_platform.core.runtime import PaperAssetSettings


def valid_settings() -> dict[str, object]:
    return {
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
        "parameters": {},
    }


def test_paper_asset_settings_parse_exact_decimal_contract() -> None:
    parsed = PaperAssetSettings.model_validate(valid_settings())

    assert parsed.timeframe == "1h"
    assert parsed.paper_close == Decimal("100")
    assert parsed.interval_seconds == 3600


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_paper_asset_settings_reject_nonfinite_decimals(value: str) -> None:
    settings = valid_settings()
    settings["paper_close"] = value

    with pytest.raises(ValidationError, match="finite"):
        PaperAssetSettings.model_validate(settings)


def test_paper_asset_settings_reject_secret_like_keys_recursively() -> None:
    settings = valid_settings()
    settings["parameters"] = {"nested": {"apiKey": "must-not-enter-runtime"}}

    with pytest.raises(ValidationError, match="secret-like"):
        PaperAssetSettings.model_validate(settings)


def test_paper_asset_settings_reject_unknown_fields_and_untrusted_strategy() -> None:
    settings = valid_settings()
    settings["strategy"] = "import-path-strategy"
    settings["unexpected"] = True

    with pytest.raises(ValidationError):
        PaperAssetSettings.model_validate(settings)


def test_paper_asset_settings_reject_nonfinite_nested_parameters() -> None:
    settings = valid_settings()
    settings["parameters"] = {"unsafe": math.inf}

    with pytest.raises(ValidationError, match="finite JSON"):
        PaperAssetSettings.model_validate(settings)
