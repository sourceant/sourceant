"""What a delivery is, and whether this deployment should act on it."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Delivery:
    """The little of an event that deciding whether to act on it needs."""

    repository: str
    event: str


@dataclass(frozen=True)
class Admission:
    """Whether to act, and what to say when not.

    The reason is written by whoever refused and is repeated verbatim, so what
    a deployment tells somebody is that deployment's to write.
    """

    accepted: bool
    reason: str = ""

    @staticmethod
    def accept() -> "Admission":
        return Admission(accepted=True)

    @staticmethod
    def reject(reason: str = "") -> "Admission":
        return Admission(accepted=False, reason=reason)
