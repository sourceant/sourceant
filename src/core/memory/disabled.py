from .models import EventPage


class DisabledMemory:
    configured = False

    def record(self, scope, events):
        return []

    def events(self, scope, since=None, until=None, after=None):
        return EventPage()

    def recall(self, scope, text, limit=10):
        return []

    def links(self, scope, since=None, until=None, **options):
        return []
