from sourceant_events import Event

from src.core.event_delivery import FORWARDED_TYPES, forward
from src.core.plugins.event_hooks import event_hooks

event_hooks.subscribe_to_events(
    "sourceant.events.forward", forward, list(FORWARDED_TYPES), transactional=True
)


def emit(*, type: str, workspace: str = "", data: dict | None = None, session=None):
    event = Event(
        type=type, data=dict(data or {}), metadata={"workspace": str(workspace or "")}
    )
    payload = event.to_dict()
    payload["workspace"] = payload["metadata"]["workspace"]
    return event_hooks.dispatch_event(type, payload, session=session)
