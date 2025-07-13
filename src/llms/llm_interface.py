from abc import ABC, abstractmethod
from typing import List, Optional
from src.models.code_review import CodeReview
from src.utils.diff_parser import ParsedDiff


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
    def generate_code_review(
        self,
        diff: str,
        parsed_files: Optional[List[ParsedDiff]] = None,
        file_paths: Optional[List[str]] = None,
    ) -> Optional[CodeReview]:
        """Generates a code review based on the given diff."""
        pass

    @abstractmethod
    def is_summary_different(self, summary_a: str, summary_b: str) -> bool:
        """Compares two summaries to see if they are semantically different."""
        pass
