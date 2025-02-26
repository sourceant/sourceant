from src.events.event import Event
from src.models.repository_event import RepositoryEvent as RepositoryEventModel


class RepositoryEvent(Event):
    """Event for repository changes."""

    def __init__(self, repository_event_object: RepositoryEventModel):
        super().__init__(repository_event_object)

    def __str__(self):
        return f"RepositoryEvent: {self.data.type} on {self.data.repository}"
