from abc import ABC, abstractmethod
from src.models.code_review import CodeReview


class LLMInterface(ABC):
    @abstractmethod
    def generate_code_review(self, diff: str, context: str = "None") -> CodeReview:
        """Generates a code review based on the given diff."""
        pass
