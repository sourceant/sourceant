from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import ModelUsage


@runtime_checkable
class UsageRecorder(Protocol):
    """Where what a model call consumed is kept.

    Recording is not the caller's job and must never be able to stop it, so an
    implementation is expected to absorb its own failures.
    """

    def record(self, usage: ModelUsage) -> None: ...
