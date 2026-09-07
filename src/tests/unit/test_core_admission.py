"""Whether a deployment acts on a delivery at all."""

from src.core.admission import Admission, Delivery, DeliveryGate, admits
from src.core.services import ServiceRegistry

A_DELIVERY = Delivery(repository="someone/somewhere", event="pull_request.opened")


class Refuses:
    def __init__(self, reason: str = "") -> None:
        self._reason = reason

    def admits(self, delivery: Delivery) -> Admission:
        return Admission.reject(self._reason)


class Accepts:
    def admits(self, delivery: Delivery) -> Admission:
        return Admission.accept()


class Breaks:
    def admits(self, delivery: Delivery) -> Admission:
        raise RuntimeError("this gate cannot tell")


def test_a_deployment_with_no_gate_acts_on_everything():
    """Which is what an instance serving one team wants, and it is the only
    thing the core ships."""
    assert admits(A_DELIVERY, ServiceRegistry()).accepted


def test_a_refusal_carries_what_to_say():
    services = ServiceRegistry()
    services.contribute(
        DeliveryGate, Refuses("nothing here says whose this is"), "test"
    )

    answered = admits(A_DELIVERY, services)

    assert not answered.accepted
    assert answered.reason == "nothing here says whose this is"


def test_one_refusal_is_enough():
    services = ServiceRegistry()
    services.contribute(DeliveryGate, Accepts(), "test")
    services.contribute(DeliveryGate, Refuses("no"), "test")

    assert not admits(A_DELIVERY, services).accepted


def test_a_gate_that_cannot_tell_does_not_refuse():
    """Refusing on its behalf would stop work nobody meant to stop."""
    services = ServiceRegistry()
    services.contribute(DeliveryGate, Breaks(), "test")

    assert admits(A_DELIVERY, services).accepted
