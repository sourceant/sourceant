"""Requirements, as something a group can hold.

Coverage already answers what a requirement has and has not: this hands the
same numbers back for a set of them, which is what makes "how much of this
feature is implemented" a question with an answer.
"""

from __future__ import annotations

from typing import Mapping

from src.core.scope import Scope
from src.core.sql_support import chunked

from .interfaces import RequirementsReader
from .models import CoverageQuery, RequirementQuery

#: What this type is called wherever a member type is named.
TYPE = "requirement"

#: Coverage answers at most a hundred requirements at a time.
STEP = 100


class GroupableRequirements:
    def __init__(self, requirements: RequirementsReader) -> None:
        self._requirements = requirements

    @property
    def type(self) -> str:
        return TYPE

    def exists(self, scope: Scope, ids: frozenset[str]) -> tuple[str, ...]:
        found: list[str] = []
        for chunk in chunked(ids, STEP):
            answered = self._requirements.search(
                RequirementQuery(scope=scope, ids=frozenset(chunk), limit=STEP)
            )
            found.extend(item.id for item in answered.items)
        return tuple(sorted(found))

    def summarize(self, scope: Scope, ids: frozenset[str]) -> Mapping[str, int]:
        total = covered = tested = 0
        for chunk in chunked(ids, STEP):
            report = self._requirements.coverage(
                CoverageQuery(scope=scope, requirement_ids=frozenset(chunk), limit=STEP)
            )
            for item in report.items:
                total += 1
                covered += 1 if item.covered else 0
                tested += 1 if item.tested else 0
        return {"total": total, "covered": covered, "tested": tested}
