from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    options: Mapping[str, Any] = field(default_factory=dict)


class UnknownProviderError(LookupError):
    pass


class ProviderRegistry[T]:
    kind = "provider"

    def __init__(self, factories: Mapping[str, Callable[..., T]] | None = None) -> None:
        self._factories: dict[str, Callable[..., T]] = dict(factories or {})

    def register(self, name: str, factory: Callable[..., T]) -> None:
        if name in self._factories:
            raise ValueError(f"{self.kind} provider '{name}' is already registered")
        self._factories[name] = factory

    def create(self, spec: ProviderSpec) -> T:
        try:
            factory = self._factories[spec.provider]
        except KeyError:
            raise UnknownProviderError(
                f"{self.kind} provider '{spec.provider}' is not registered, "
                f"known: {', '.join(self._factories)}"
            ) from None
        return factory(**spec.options)
