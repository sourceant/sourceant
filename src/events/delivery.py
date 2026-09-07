"""A webhook delivery, asked for now and done somewhere else.

The job carries the delivery's row id and nothing else. Sending the delivery
itself would put a copy of it in two places able to disagree, and the row is
what everything else reads.

A delivery is done once. Posting a review is not something that can be repeated
harmlessly, so a delivery that fails is recorded as failed rather than tried
again.
"""

from __future__ import annotations

from typing import Optional

from src.config.settings import whole_number
from src.core.jobs.models import (
    BY_REPOSITORY,
    BY_WORKSPACE,
    WITHIN_SECONDS,
    Job,
    JobOutcome,
    JobRequest,
)
from src.models.repository_event import RepositoryEvent as RepositoryEventModel
from src.utils.logger import logger

KIND = "core.delivery"

# How long a delivery may take. A review of a large change asks a model
# several times and outlasts the queue's own default of 180 seconds.
DELIVERY_TIMEOUT = whole_number("QUEUE_DELIVERY_TIMEOUT", 1800)


def tenant_for(repository: str) -> tuple[str, str]:
    """Whose delivery this is, for the purpose of taking turns.

    Most deliveries name no workspace: nobody has connected the repository, or
    two have and neither can be charged for it. Falling back to the repository
    keeps those fair against each other instead of pooling every one of them
    into a single bucket that takes one turn between them.
    """
    from src.core.workspace import workspace_holding

    workspace = workspace_holding(repository)
    if workspace:
        return workspace, BY_WORKSPACE
    return repository, BY_REPOSITORY


def delivery_of(event: RepositoryEventModel) -> JobRequest:
    """The job that does what this delivery asks for."""
    if event.id is None:
        raise ValueError(
            "A delivery is queued by its row, and this one was never written. "
            "A deployment that keeps nothing does its deliveries in the request."
        )
    tenant, kind = tenant_for(event.repository_full_name or "")
    return JobRequest(
        lane=WITHIN_SECONDS,
        kind=KIND,
        payload={"repository_event_id": event.id},
        deadline_seconds=DELIVERY_TIMEOUT,
        max_attempts=1,
    ).for_(tenant, kind)


class Deliveries:
    """Tells whatever subscribed to a delivery that it arrived."""

    kind = KIND

    def run(self, job: Job) -> JobOutcome:
        event = self._event(job)
        if event is None:
            return JobOutcome.failed("this job names no delivery that still exists")

        from src.events.dispatcher import EventDispatcher
        from src.events.repository_event import RepositoryEvent

        EventDispatcher().deliver(RepositoryEvent(event))
        return JobOutcome.ok()

    @staticmethod
    def _event(job: Job) -> Optional[RepositoryEventModel]:
        event_id = job.payload.get("repository_event_id")
        if event_id is None:
            logger.error(f"Job {job.id} says it is a delivery but names none")
            return None
        event = RepositoryEventModel.get(int(event_id))
        if event is None:
            logger.error(f"Delivery {event_id} is gone, so job {job.id} has nothing")
        return event
