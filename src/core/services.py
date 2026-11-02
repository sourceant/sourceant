from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, cast
from threading import RLock

T = TypeVar("T")


@dataclass(frozen=True)
class ServiceRegistration(Generic[T]):
    service: T
    provider: str


class ServiceRegistry:
    def __init__(self) -> None:
        self._registrations: dict[type[object], ServiceRegistration[object]] = {}
        self._contributions: dict[type[object], list[ServiceRegistration[object]]] = {}

    def with_service(
        self, interface: type[T], service: T, provider: str
    ) -> ServiceRegistry:
        result = ServiceRegistry()
        result._registrations = dict(self._registrations)
        result._contributions = {
            key: list(values) for key, values in self._contributions.items()
        }
        result._registrations[interface] = ServiceRegistration(service, provider)
        return result

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


_memory_lock = RLock()


def memory_services(registry: ServiceRegistry = service_registry):
    with _memory_lock:
        return _memory_services(registry)


def _memory_services(registry):
    import os
    from src.core.memory import EngineMemory, InMemoryMemory, EventReader, EventRecorder
    from src.core.memory.interfaces import BucketBinder

    try:
        recorder = registry.resolve(EventRecorder)
    except LookupError:
        url, key = os.getenv("MEMORY_ENGINE_URL"), os.getenv("MEMORY_ENGINE_KEY")
        if bool(url) != bool(key):
            raise ValueError(
                "MEMORY_ENGINE_URL and MEMORY_ENGINE_KEY must be configured together"
            )
        recorder = EngineMemory(url, key) if url else InMemoryMemory()
        registry.register(EventRecorder, recorder, "core.memory")
    try:
        reader = registry.resolve(EventReader)
    except LookupError:
        if not isinstance(recorder, EventReader):
            raise LookupError("The memory recorder needs a registered EventReader")
        reader = recorder
        registry.register(EventReader, reader, "core.memory")
    try:
        registry.resolve(BucketBinder)
    except LookupError:
        if not isinstance(recorder, BucketBinder):
            raise LookupError("The memory recorder needs a registered BucketBinder")
        registry.register(BucketBinder, recorder, "core.memory")
    return recorder, reader
