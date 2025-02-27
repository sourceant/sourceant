from abc import ABC, abstractmethod


class LLMInterface(ABC):
    @abstractmethod
    def generate_code_review(self, diff: str, context: dict = None) -> str:
        """Generates a code review based on the given diff."""
        pass
