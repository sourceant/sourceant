"""Whether this deployment acts on a delivery.

Nothing here decides anything. A deployment that contributes no gate acts on
everything, which is what an instance serving one team wants. A deployment
serving many contributes a gate that knows whose repositories are whose.
"""

from __future__ import annotations

from src.core.services import ServiceRegistry, service_registry
from src.utils.logger import logger

from .interfaces import DeliveryGate
from .models import Admission, Delivery


def gates(services: ServiceRegistry = service_registry) -> tuple[DeliveryGate, ...]:
    """Everything contributed that has a say."""
    return services.contributions(DeliveryGate)


def admits(
    delivery: Delivery, services: ServiceRegistry = service_registry
) -> Admission:
    """Whether to act on this delivery, and what to say when not.

    The first refusal is the answer. A gate that raises is not a refusal: it
    could not decide, and refusing on its behalf would stop work nobody meant
    to stop.
    """
    for gate in gates(services):
        try:
            answered = gate.admits(delivery)
        except Exception:
            logger.warning(
                f"{type(gate).__name__} could not say whether to act on "
                f"{delivery.repository}",
                exc_info=True,
            )
            continue
        if not answered.accepted:
            return answered
    return Admission.accept()


__all__ = [
    "Admission",
    "Delivery",
    "DeliveryGate",
    "admits",
    "gates",
]
