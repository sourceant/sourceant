from __future__ import annotations

from typing import Any, Callable, Protocol, Sequence, runtime_checkable

from src.core.change_context import ChangeSet
from src.core.scope import Scope
from src.models.code_review import CodeReview

from .findings import FindingQuery, FindingResult, ReviewFinding
from .models import Told
from .records import ReviewRecord


@runtime_checkable
class Reviewer(Protocol):
    """Reads a change and says what is wrong with it.

    Implemented by the code_reviewer plugin. Arguments are what only a caller
    knows: which model to ask, how to read a file at the revision under review,
    what has already been said about it, and anything else it wants passed on.
    """

    def review(
        self,
        changes: ChangeSet,
        *,
        provider: Any,
        read_content: Callable[[str], str | None] | None = None,
        existing_comments: Sequence[dict] | None = None,
        told: Sequence[Told] = (),
        code_scope: Scope | None = None,
        metadata: dict | None = None,
    ) -> CodeReview | None: ...


@runtime_checkable
class WorkingTreeReviewer(Protocol):
    """Reviews a checkout by the name it is registered under.

    The other way in to the same reviewer; a pull request is the first.
    """

    def review(
        self,
        *,
        repository: str,
        against: str = "",
        title: str = "",
        description: str = "",
        skills: Sequence[str] = (),
        use_model: bool = True,
    ) -> dict: ...


@runtime_checkable
class ReviewStore(Protocol):
    """Where reviews are kept, so a link to one still opens it."""

    def put(self, review: ReviewRecord) -> ReviewRecord: ...

    def get(self, identifier: str) -> ReviewRecord | None: ...

    def recent(self, *, repository: str = "", limit: int = 20) -> list: ...


@runtime_checkable
class FindingReader(Protocol):
    def search(self, query: FindingQuery) -> FindingResult: ...


@runtime_checkable
class FindingWriter(Protocol):
    def put_finding(self, scope: Scope, finding: ReviewFinding) -> None:
        """File one. Never changes a state somebody already decided."""
        ...

    def set_state(self, scope: Scope, identifier: str, state: str) -> bool:
        """Decide about one. The only thing that changes a state."""
        ...


@runtime_checkable
class FindingStore(FindingReader, FindingWriter, Protocol):
    """Where findings are kept between reviews.

    Registered by whoever can keep them. Unregistered, a review still runs and
    simply forgets, which is what it did before this existed.
    """
