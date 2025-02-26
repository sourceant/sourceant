from typing import Any


class Event:
    """Base class for events."""

    def __init__(self, data: Any):
        self.data = data

    def __str__(self):
        return f"{self.__class__.__name__}: {self.data}"
