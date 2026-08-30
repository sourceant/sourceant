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
        self._contributions: dict[type[object], list[ServiceRegistration[object]]] = {}

    def contribute(self, interface: type[T], service: T, provider: str) -> None:
        """Add one of many, where the interface is additive rather than a choice.

        MCP tools are the case: every provider's tools belong on the server,
        unlike a reviewer or a knowledge store where a second registration
        would mean two answers to one question.
        """
        self._contributions.setdefault(interface, []).append(
            ServiceRegistration(service, provider)
        )

    def contributions(self, interface: type[T]) -> tuple[T, ...]:
        """Everything contributed for this interface, in registration order."""
        return tuple(
            cast(T, item.service) for item in self._contributions.get(interface, ())
        )

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
        for interface, items in list(self._contributions.items()):
            kept = [item for item in items if item.provider != provider]
            if kept:
                self._contributions[interface] = kept
            else:
                del self._contributions[interface]


service_registry = ServiceRegistry()
