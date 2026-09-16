class ExchangeError(Exception):
    """Base error raised by exchange adapters."""


class TransientExchangeError(ExchangeError):
    """A temporary exchange or transport failure."""


class AuthenticationExchangeError(ExchangeError):
    """Exchange credentials are missing or invalid."""


class RejectedExchangeError(ExchangeError):
    """The exchange rejected a validly formed request."""


class NotFoundExchangeError(ExchangeError):
    """The requested exchange resource does not exist."""


class MalformedExchangeResponse(ExchangeError):
    """The exchange response is missing or contains invalid data."""
