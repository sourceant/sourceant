from abc import ABC, abstractmethod
from typing import List, Optional, Union
from src.models.code_review import CodeReview, CodeSuggestion, CodeReviewSummary
from src.utils.diff_parser import ParsedDiff


class LLMInterface(ABC):
    @property
    @abstractmethod
    def token_limit(self) -> int:
        pass

    @property
    @abstractmethod
    def uploads_enabled(self) -> bool:
        pass

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        pass

    @abstractmethod
    def generate_code_review(
        self,
        diff: str,
        parsed_files: Optional[List[ParsedDiff]] = None,
        file_paths: Optional[List[str]] = None,
    ) -> Optional[CodeReview]:
        pass

    @abstractmethod
    def generate_summary(
        self, suggestions: List[CodeSuggestion], as_text: bool = False
    ) -> Union[CodeReviewSummary, str]:
        pass

    @abstractmethod
    def generate_text(self, prompt: str) -> str:
        pass

    @abstractmethod
    def is_summary_different(self, summary_a: str, summary_b: str) -> bool:
        pass
