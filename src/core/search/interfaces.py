from typing import Protocol, runtime_checkable

from .models import CodeTextQuery, CodeTextResult


@runtime_checkable
class CodeTextSearcher(Protocol):
    def search_text(self, query: CodeTextQuery) -> CodeTextResult: ...
