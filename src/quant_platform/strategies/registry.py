from collections.abc import Callable

from quant_platform.strategies.protocol import Strategy

StrategyFactory = Callable[[], Strategy]


class StrategyRegistry:
    """Explicit registry of trusted strategy factories."""

    def __init__(self) -> None:
        self._factories: dict[str, StrategyFactory] = {}

    def register(self, name: str, factory: StrategyFactory) -> None:
        if not name.strip():
            raise ValueError("strategy name must not be empty")
        if name in self._factories:
            raise ValueError(f"strategy {name!r} is already registered")
        self._factories[name] = factory

    def get(self, name: str) -> StrategyFactory:
        try:
            return self._factories[name]
        except KeyError:
            raise KeyError(f"strategy {name!r} is not registered") from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def create(self, name: str) -> Strategy:
        strategy = self.get(name)()
        if not isinstance(strategy, Strategy):
            raise TypeError(f"factory for {name!r} did not create a Strategy")
        return strategy
