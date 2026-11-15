from datetime import datetime, timezone
from uuid import uuid4

from src.core.event_delivery import FORWARDED_TYPES, forward
from src.core.plugins.event_hooks import event_hooks

event_hooks.subscribe_to_events(
    "sourceant.events.forward", forward, list(FORWARDED_TYPES), transactional=True
)


def emit(*, type: str, workspace: str = "", data: dict | None = None, session=None):
    if not type:
        raise ValueError("An event requires a type")
    event = {
        "version": 1,
        "id": str(uuid4()),
        "type": type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "workspace": str(workspace or ""),
        "data": dict(data or {}),
    }
    return event_hooks.dispatch_event(type, event, session=session)
