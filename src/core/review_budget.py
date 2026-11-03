from threading import Lock
from time import monotonic


class ReviewBudgetExceeded(RuntimeError):
    pass


class ReviewBudget:
    def __init__(self, configuration=None):
        def value(key, fallback):
            return configuration.value(key) if configuration is not None else fallback

        self.maximum_calls = int(value("review.maximum_model_calls", 64) or 64)
        self.output_tokens = int(value("review.output_tokens", 8192) or 8192)
        self.deadline = monotonic() + int(value("review.time_budget", 900) or 900)
        self.calls = 0
        self.exhausted = False
        self.lock = Lock()

    def remaining(self):
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            self.exhausted = True
            raise ReviewBudgetExceeded(
                "The review reached its time budget. Split the change or increase the review budget in its repository settings."
            )
        return remaining

    def reserve(self):
        with self.lock:
            self.remaining()
            if self.calls >= self.maximum_calls:
                self.exhausted = True
                raise ReviewBudgetExceeded(
                    "The review reached its model-call budget. Split the change or increase the review budget in its repository settings."
                )
            self.calls += 1
