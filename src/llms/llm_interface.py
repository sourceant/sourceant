from abc import ABC, abstractmethod
from src.models.code_review import CodeReview


class LLMInterface(ABC):
    @property
    @abstractmethod
    def token_limit(self) -> int:
        """The token limit for the model."""
        pass

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Counts the number of tokens in a given text string."""
        pass

    @abstractmethod
    def generate_code_review(self, diff: str, context: str = "None") -> CodeReview:
        """Generates a code review based on the given diff."""
        pass
