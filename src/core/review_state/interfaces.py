from typing import Protocol, runtime_checkable

from src.core.scope import Scope

from .models import FindingQuery, FindingResult, ReviewFinding


@runtime_checkable
class ReviewStateReader(Protocol):
    def search(self, query: FindingQuery) -> FindingResult: ...


@runtime_checkable
class ReviewStateWriter(Protocol):
    def put_finding(self, scope: Scope, finding: ReviewFinding) -> None: ...


@runtime_checkable
class ReviewStateRepository(ReviewStateReader, ReviewStateWriter, Protocol):
    pass
