from typing import Protocol, runtime_checkable

from .models import SearchQuery, SearchResult


@runtime_checkable
class Searcher(Protocol):
    def search(self, query: SearchQuery) -> SearchResult: ...
