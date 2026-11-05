from .engine import _validate, _validate_recall
from .interfaces import RememberedEvent, RecallResult, HistoryPage


class DisabledMemory:
    def remember(self, scope, observation):
        _validate(observation)
        return RememberedEvent(
            "",
            observation.when,
            observation.why,
            observation.who,
            observation.what,
            observation.type,
            configured=False,
        )

    def recall(self, scope, text, limit=10):
        _validate_recall(text, limit)
        return RecallResult(configured=False)

    def history(self, scope, since=None, until=None, after=None):
        return HistoryPage(configured=False)

    def record_knowledge_change(self, scope, change):
        pass

    def record(self, scope, occurrences):
        pass
