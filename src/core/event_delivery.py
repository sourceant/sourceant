import os

from sourceant_events import Event
from sourceant_events.http import SignedHTTP
from sourceant_events.jobs import EventHandler, QueuedListener

from src.core.jobs import BACKGROUND, JobOutcome, JobRequest, enqueue

FORWARDED_TYPES = (
    "review.failed",
    "review.coverage_incomplete",
    "provider.credits_exhausted",
    "provider.authentication_failed",
)


def forward(event_type, event, *, session=None):
    if not event.get("workspace") or not os.getenv("EVENTS_URL"):
        return None
    if not os.getenv("EVENTS_SECRET"):
        raise ValueError("Event delivery requires a signing secret")
    queued = QueuedListener(
        enqueue,
        lambda occurrence: JobRequest(
            BACKGROUND,
            EventDelivery.type,
            max_attempts=12,
            deadline_seconds=30,
        ).for_(event["workspace"]),
    )
    payload = dict(event)
    payload["metadata"] = {
        **event.get("metadata", {}),
        "workspace": str(event["workspace"]),
    }
    return queued(Event.from_dict(payload), session)


def gateway_payload(event):
    payload = event.to_dict()
    payload["workspace"] = event.metadata.get("workspace", "")
    return payload


class EventDelivery:
    type = "core.event.deliver"

    def run(self, job) -> JobOutcome:
        url = os.getenv("EVENTS_URL")
        secret = os.getenv("EVENTS_SECRET")
        if not url or not secret:
            return JobOutcome.failed("Event destination is unavailable", retry=True)
        delivery = SignedHTTP(
            url,
            secret,
            timestamp_header="X-Sourceant-Timestamp",
            signature_header="X-Sourceant-Signature",
            encode=gateway_payload,
        )
        outcome = EventHandler(self.type, delivery).run(job)
        return JobOutcome(
            succeeded=outcome.succeeded,
            error=outcome.error,
            retry=outcome.retry,
            retry_in=outcome.retry_in,
        )
