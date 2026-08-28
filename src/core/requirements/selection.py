from __future__ import annotations

from .interfaces import RequirementsReader
from .models import CoverageQuery, Requirement, RequirementQuery, RequirementSelection


class LinkedRequirementSelector:
    """Requirements linked to the files a change touches.

    Exact and shallow: a requirement nobody linked to the changed files is not
    returned, however clearly the change addresses it.
    """

    def __init__(self, requirements: RequirementsReader) -> None:
        self._requirements = requirements

    def select(self, selection: RequirementSelection) -> tuple[Requirement, ...]:
        if not selection.paths:
            return ()
        report = self._requirements.coverage(
            CoverageQuery(scope=selection.scope, paths=frozenset(selection.paths))
        )
        if not report.items:
            return ()
        found = self._requirements.search(
            RequirementQuery(
                scope=selection.scope,
                ids=frozenset(item.requirement_id for item in report.items),
                limit=selection.limit,
            )
        )
        return found.items
