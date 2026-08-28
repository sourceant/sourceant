from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.core.scope import Scope

from .models import (
    CoverageQuery,
    CoverageReport,
    Requirement,
    RequirementLink,
    RequirementQuery,
    RequirementResult,
    RequirementSelection,
)


@runtime_checkable
class RequirementsReader(Protocol):
    def search(self, query: RequirementQuery) -> RequirementResult: ...

    def get_links(
        self, scope: Scope, requirement_ids: frozenset[str]
    ) -> tuple[RequirementLink, ...]: ...

    def coverage(self, query: CoverageQuery) -> CoverageReport: ...


@runtime_checkable
class RequirementsWriter(Protocol):
    def put(self, scope: Scope, requirement: Requirement) -> None: ...

    def put_link(self, scope: Scope, link: RequirementLink) -> None: ...

    def remove(self, scope: Scope, requirement_id: str) -> None: ...


@runtime_checkable
class RequirementsRepository(RequirementsReader, RequirementsWriter, Protocol):
    pass


@runtime_checkable
class RequirementSelector(Protocol):
    """Which recorded requirements a change is answerable to.

    The core answers this from the links it holds, which is exact and shallow.
    Reading intent out of a change is a different problem, and an installation
    that can do it registers its own selector here.
    """

    def select(self, selection: RequirementSelection) -> tuple[Requirement, ...]: ...


@runtime_checkable
class RequirementsSource(Protocol):
    """Requirements a team already tracks somewhere else.

    Implementations read from a tracker and hand back what they found. Writing
    it down, and deciding what it means, stays with the caller.
    """

    def sync(self, scope: Scope) -> tuple[Requirement, ...]: ...
