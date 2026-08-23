from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

T = TypeVar("T")


@dataclass(frozen=True)
class ServiceRegistration(Generic[T]):
    service: T
    provider: str


class ServiceRegistry:
    def __init__(self) -> None:
        self._registrations: dict[type[object], ServiceRegistration[object]] = {}

    def register(self, interface: type[T], service: T, provider: str) -> None:
        if interface in self._registrations:
            current = self._registrations[interface]
            raise ValueError(
                f"{interface.__name__} is already provided by {current.provider}"
            )
        self._registrations[interface] = ServiceRegistration(service, provider)

    def resolve(self, interface: type[T]) -> T:
        try:
            registration = self._registrations[interface]
        except KeyError as error:
            message = f"No provider registered for {interface.__name__}"
            raise LookupError(message) from error
        return cast(T, registration.service)

    def unregister_provider(self, provider: str) -> None:
        interfaces = [
            interface
            for interface, registration in self._registrations.items()
            if registration.provider == provider
        ]
        for interface in interfaces:
            del self._registrations[interface]


service_registry = ServiceRegistry()
