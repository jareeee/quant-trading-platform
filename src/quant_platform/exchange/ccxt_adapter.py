from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import ccxt  # type: ignore[import-untyped]

from quant_platform.exchange.errors import (
    AuthenticationExchangeError,
    ExchangeError,
    MalformedExchangeResponse,
    NotFoundExchangeError,
    RejectedExchangeError,
    TransientExchangeError,
)
from quant_platform.exchange.models import (
    Balance,
    Candle,
    Fill,
    MarginMode,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)


@dataclass(frozen=True, slots=True)
class CcxtExchangeOptions:
    """Exchange-specific CCXT parameters, kept outside the generic adapter."""

    client_order_id_key: str = "clientOrderId"
    order_id_key: str = "order"
    method_params: Mapping[str, Mapping[str, object]] = field(default_factory=dict)


class CcxtExchange:
    """Synchronous Exchange adapter around an injected CCXT client."""

    def __init__(
        self,
        client: Any,
        *,
        clock: Callable[[], datetime],
        options: CcxtExchangeOptions | None = None,
    ) -> None:
        self._client = client
        self._clock = clock
        self._options = options or CcxtExchangeOptions()

    def _params(self, method: str) -> dict[str, object]:
        return dict(self._options.method_params.get(method, {}))

    def test_connection(self) -> bool:
        try:
            response = self._client.fetch_status()
            if not isinstance(response, Mapping):
                raise MalformedExchangeResponse("malformed exchange status")
            status = _text(response.get("status"), "exchange status")
            if status not in {"ok", "maintenance", "shutdown"}:
                raise MalformedExchangeResponse("malformed exchange status")
            return status == "ok"
        except Exception as error:
            raise _normalize_error(error) from None

    def fetch_closed_candles(
        self, symbol: str, timeframe: str, limit: int
    ) -> tuple[Candle, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        try:
            duration_seconds = _decimal(
                self._client.parse_timeframe(timeframe), "timeframe duration"
            )
            if duration_seconds <= 0:
                raise MalformedExchangeResponse("malformed timeframe duration")
            duration = timedelta(seconds=float(duration_seconds))
            rows = self._client.fetch_ohlcv(symbol, timeframe, limit=limit + 1)
            candles = tuple(_parse_candle(row, symbol, timeframe) for row in rows)
        except Exception as error:
            raise _normalize_error(error) from None
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("clock must return a UTC-aware datetime")
        return tuple(candle for candle in candles if candle.opened_at + duration <= now)[-limit:]

    def fetch_balance(self) -> tuple[Balance, ...]:
        try:
            params = self._params("fetch_balance")
            response = (
                self._client.fetch_balance(params=params)
                if params
                else self._client.fetch_balance()
            )
            if not isinstance(response, Mapping):
                raise MalformedExchangeResponse("malformed balance response")
            totals = response.get("total")
            available = response.get("free")
            if not isinstance(totals, Mapping) or not isinstance(available, Mapping):
                raise MalformedExchangeResponse("malformed balance response")
            if set(totals) != set(available):
                raise MalformedExchangeResponse("malformed balance response")
            try:
                return tuple(
                    Balance(
                        currency=_text(currency, "balance currency"),
                        total=_decimal(totals[currency], "balance total"),
                        available=_decimal(available[currency], "balance available"),
                    )
                    for currency in sorted(totals, key=str)
                )
            except ValueError as error:
                raise MalformedExchangeResponse("malformed balance response") from error
        except Exception as error:
            raise _normalize_error(error) from None

    def fetch_positions(self) -> tuple[Position, ...]:
        try:
            response = self._client.fetch_positions()
            if not isinstance(response, Sequence) or isinstance(response, (str, bytes)):
                raise MalformedExchangeResponse("malformed positions response")
            return tuple(_parse_position(item) for item in response)
        except Exception as error:
            raise _normalize_error(error) from None

    def fetch_open_orders(self, symbol: str | None = None) -> tuple[OrderResult, ...]:
        try:
            response = self._client.fetch_open_orders(
                symbol, params=self._params("fetch_open_orders")
            )
            return _parse_orders(response)
        except Exception as error:
            raise _normalize_error(error) from None

    def submit_order(self, request: OrderRequest) -> OrderResult:
        params = self._params("create_order")
        params[self._options.client_order_id_key] = request.client_order_id
        price = None if request.price is None else str(request.price)
        try:
            response = self._client.create_order(
                request.symbol,
                request.order_type.value,
                request.side.value,
                str(request.quantity),
                price,
                params=params,
            )
            return _parse_order(response)
        except Exception as error:
            raise _normalize_error(error) from None

    def cancel_order(self, exchange_order_id: str, symbol: str) -> OrderResult:
        try:
            response = self._client.cancel_order(
                exchange_order_id, symbol, params=self._params("cancel_order")
            )
            return _parse_order(response)
        except Exception as error:
            raise _normalize_error(error) from None

    def fetch_order_fills(self, exchange_order_id: str, symbol: str) -> tuple[Fill, ...]:
        params = self._params("fetch_my_trades")
        params[self._options.order_id_key] = exchange_order_id
        try:
            response = self._client.fetch_my_trades(symbol, params=params)
            if not isinstance(response, Sequence) or isinstance(response, (str, bytes)):
                raise MalformedExchangeResponse("malformed fills response")
            fills = tuple(_parse_fill(item) for item in response)
            return tuple(fill for fill in fills if fill.exchange_order_id == exchange_order_id)
        except Exception as error:
            raise _normalize_error(error) from None

    def set_leverage(self, symbol: str, leverage: Decimal) -> None:
        if not leverage.is_finite() or leverage <= 0:
            raise ValueError("leverage must be positive")
        value: int | float = (
            int(leverage) if leverage == leverage.to_integral() else float(leverage)
        )
        try:
            self._client.set_leverage(
                value, symbol, params=self._params("set_leverage")
            )
        except Exception as error:
            raise _normalize_error(error) from None

    def set_margin_mode(self, symbol: str, margin_mode: MarginMode) -> None:
        try:
            self._client.set_margin_mode(
                margin_mode.value, symbol, params=self._params("set_margin_mode")
            )
        except Exception as error:
            raise _normalize_error(error) from None


def _parse_orders(response: object) -> tuple[OrderResult, ...]:
    if not isinstance(response, Sequence) or isinstance(response, (str, bytes)):
        raise MalformedExchangeResponse("malformed orders response")
    return tuple(_parse_order(item) for item in response)


def _parse_order(item: object) -> OrderResult:
    if not isinstance(item, Mapping):
        raise MalformedExchangeResponse("malformed order")
    statuses = {
        "open": OrderStatus.OPEN,
        "closed": OrderStatus.FILLED,
        "canceled": OrderStatus.CANCELED,
        "cancelled": OrderStatus.CANCELED,
        "rejected": OrderStatus.REJECTED,
    }
    try:
        raw_status = _text(item["status"], "order status")
        status = statuses[raw_status]
        filled = _decimal(item["filled"], "order filled")
        average_value = item["average"]
        average = None if average_value is None else _decimal(average_value, "order average")
        return OrderResult(
            exchange_order_id=_text(item["id"], "order id"),
            client_order_id=_text(item["clientOrderId"], "client order id"),
            symbol=_text(item["symbol"], "order symbol"),
            side=OrderSide(_text(item["side"], "order side")),
            order_type=OrderType(_text(item["type"], "order type")),
            status=status,
            quantity=_decimal(item["amount"], "order amount"),
            filled_quantity=filled,
            average_fill_price=average,
            submitted_at=_timestamp(item["timestamp"], "order timestamp"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise MalformedExchangeResponse("malformed order") from error


def _parse_fill(item: object) -> Fill:
    if not isinstance(item, Mapping):
        raise MalformedExchangeResponse("malformed fill")
    try:
        fee = item["fee"]
        if not isinstance(fee, Mapping):
            raise MalformedExchangeResponse("malformed fill fee")
        return Fill(
            exchange_fill_id=_text(item["id"], "fill id"),
            exchange_order_id=_text(item["order"], "fill order id"),
            symbol=_text(item["symbol"], "fill symbol"),
            side=OrderSide(_text(item["side"], "fill side")),
            quantity=_decimal(item["amount"], "fill amount"),
            price=_decimal(item["price"], "fill price"),
            fee=_decimal(fee["cost"], "fill fee"),
            fee_currency=_text(fee["currency"], "fill fee currency"),
            executed_at=_timestamp(item["timestamp"], "fill timestamp"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise MalformedExchangeResponse("malformed fill") from error


def _parse_position(item: object) -> Position:
    if not isinstance(item, Mapping):
        raise MalformedExchangeResponse("malformed position")
    try:
        quantity = abs(_decimal(item["contracts"], "position contracts"))
        side = _text(item["side"], "position side")
        if side == "short":
            quantity = -quantity
        elif side != "long":
            raise MalformedExchangeResponse("malformed position side")
        return Position(
            symbol=_text(item["symbol"], "position symbol"),
            quantity=quantity,
            entry_price=_decimal(item["entryPrice"], "position entry price"),
            leverage=_decimal(item["leverage"], "position leverage"),
            margin_mode=MarginMode(_text(item["marginMode"], "position margin mode")),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise MalformedExchangeResponse("malformed position") from error


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MalformedExchangeResponse(f"malformed {field}")
    return value


def _parse_candle(row: object, symbol: str, timeframe: str) -> Candle:
    if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) < 6:
        raise MalformedExchangeResponse("malformed OHLCV row")
    try:
        return Candle(
            symbol=symbol,
            timeframe=timeframe,
            opened_at=_timestamp(row[0], "OHLCV timestamp"),
            open=_decimal(row[1], "OHLCV open"),
            high=_decimal(row[2], "OHLCV high"),
            low=_decimal(row[3], "OHLCV low"),
            close=_decimal(row[4], "OHLCV close"),
            volume=_decimal(row[5], "OHLCV volume"),
        )
    except (TypeError, ValueError) as error:
        raise MalformedExchangeResponse("malformed OHLCV row") from error


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise MalformedExchangeResponse(f"malformed {field}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise MalformedExchangeResponse(f"malformed {field}") from None
    if not result.is_finite():
        raise MalformedExchangeResponse(f"malformed {field}")
    return result


def _timestamp(value: object, field: str) -> datetime:
    if isinstance(value, bool):
        raise MalformedExchangeResponse(f"malformed {field}")
    try:
        return datetime.fromtimestamp(float(Decimal(str(value)) / 1000), tz=UTC)
    except (InvalidOperation, TypeError, ValueError, OSError, OverflowError):
        raise MalformedExchangeResponse(f"malformed {field}") from None


def _normalize_error(error: Exception) -> ExchangeError:
    message = "exchange request failed"
    if isinstance(error, ccxt.AuthenticationError):
        return AuthenticationExchangeError(message)
    if isinstance(error, ccxt.OrderNotFound):
        return NotFoundExchangeError(message)
    if isinstance(error, (ccxt.InvalidOrder, ccxt.InsufficientFunds, ccxt.PermissionDenied)):
        return RejectedExchangeError(message)
    if isinstance(error, (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout)):
        return TransientExchangeError(message)
    if isinstance(error, ExchangeError):
        return error
    return ExchangeError(message)
