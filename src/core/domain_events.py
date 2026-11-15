from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from src.core.jobs import BACKGROUND, JobOutcome, JobRequest, enqueue

EVENT_TYPES = frozenset(
    {
        "review.failed",
        "review.coverage_incomplete",
        "provider.credits_exhausted",
        "provider.authentication_failed",
    }
)


def emit(
    *,
    type: str,
    workspace: str,
    review: str = "",
    repository: str = "",
    provider: str = "",
    model: str = "",
    session=None,
) -> int | None:
    if type not in EVENT_TYPES:
        raise ValueError("Unsupported domain event type")
    if not workspace or not os.getenv("DOMAIN_EVENTS_URL"):
        return None
    if not os.getenv("DOMAIN_EVENTS_SECRET"):
        raise ValueError("Domain event delivery requires a signing secret")
    event = {
        "version": 1,
        "id": str(uuid4()),
        "type": type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "workspace": str(workspace),
        "data": {
            "review": str(review),
            "repository": repository,
            "provider": provider,
            "model": model,
        },
    }
    return enqueue(
        JobRequest(
            BACKGROUND,
            DomainEventDelivery.type,
            payload=event,
            max_attempts=12,
            deadline_seconds=30,
        ).for_(workspace),
        session=session,
    )


class DomainEventDelivery:
    type = "core.domain_event.deliver"

    def run(self, job) -> JobOutcome:
        url = os.getenv("DOMAIN_EVENTS_URL")
        secret = os.getenv("DOMAIN_EVENTS_SECRET")
        if not url or not secret:
            return JobOutcome.failed(
                "Domain event destination is unavailable", retry=True
            )
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
            return JobOutcome.failed(
                "Domain event delivery was not acknowledged", retry=True
            )
        return JobOutcome.failed(
            "Domain event delivery was not acknowledged", retry=True
        )


def report_failure(
    error: Exception, *, workspace=None, repository=None, review="", session=None
) -> None:
    if not workspace:
        return
    message = str(error).lower()
    type = "review.failed"
    if "insufficient_balance_error" in message or "insufficient balance" in message:
        type = "provider.credits_exhausted"
    else:
        from litellm.exceptions import AuthenticationError

        from src.llms.errors import LLMError

        cause = error
        visited = set()
        while cause is not None and id(cause) not in visited:
            visited.add(id(cause))
            if isinstance(cause, AuthenticationError):
                type = "provider.authentication_failed"
                break
            cause = cause.cause if isinstance(cause, LLMError) else cause.__cause__
    try:
        emit(
            type=type,
            workspace=str(workspace),
            repository=repository or "",
            review=review,
            session=session,
        )
    except Exception:
        if session is not None:
            raise
        from src.utils.logger import logger

        logger.error("Could not persist review failure event", exc_info=True)
