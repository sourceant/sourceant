from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import Admission, Delivery


@runtime_checkable
class DeliveryGate(Protocol):
    """Something that decides whether a delivery is acted on.

    Asked before anything is fetched or generated, so a delivery nobody wants
    costs a decision rather than a diff and a model call.
    """

    def admits(self, delivery: Delivery) -> Admission: ...
