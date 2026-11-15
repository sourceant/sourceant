from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
    return enqueue(
        JobRequest(
            BACKGROUND,
            EventDelivery.type,
            payload=event,
            max_attempts=12,
            deadline_seconds=30,
        ).for_(event["workspace"]),
        session=session,
    )


class EventDelivery:
    type = "core.event.deliver"

    def run(self, job) -> JobOutcome:
        url = os.getenv("EVENTS_URL")
        secret = os.getenv("EVENTS_SECRET")
        if not url or not secret:
            return JobOutcome.failed("Event destination is unavailable", retry=True)
        body = json.dumps(dict(job.payload), separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        signature = hmac.new(
            secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        request = Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Sourceant-Timestamp": timestamp,
                "X-Sourceant-Signature": signature,
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                if 200 <= response.status < 300:
                    return JobOutcome.ok()
        except (HTTPError, URLError, TimeoutError, OSError):
            return JobOutcome.failed("Event delivery was not acknowledged", retry=True)
        return JobOutcome.failed("Event delivery was not acknowledged", retry=True)
