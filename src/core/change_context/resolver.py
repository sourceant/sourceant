from __future__ import annotations

from typing import Protocol, runtime_checkable

from sqlalchemy.exc import SQLAlchemyError

from src.core.code_index import CodeIndexReader, CodeSearch, CodeTraversal
from src.core.knowledge import KnowledgeSelection, KnowledgeSelector
from src.core.requirements import RequirementSelection, RequirementSelector
from src.core.impact import ChangeImpactResolver, ChangeImpactRequest

from .models import ChangeContext, ChangeSet

#: A store that cannot answer leaves a review reading less, never failing.
#: Not every store here is a database: one is a graph over Bolt and one
#: shells out to an indexer, and what those raise is not a database error.
UNAVAILABLE = (OSError, RuntimeError, ValueError, SQLAlchemyError)


@runtime_checkable
class ChangeContextResolver(Protocol):
    """Everything recorded that bears on one change.

    One call rather than four, so a caller that wants more than the core can
    work out replaces this and keeps the rest, and so what reaches a review is
    bounded as a whole rather than four times over.
    """

    def resolve(self, changes: ChangeSet) -> ChangeContext: ...


class DefaultChangeContextResolver:
    def __init__(
        self,
        *,
        code: CodeIndexReader | None = None,
        knowledge: KnowledgeSelector | None = None,
        requirements: RequirementSelector | None = None,
        impact: ChangeImpactResolver | None = None,
    ) -> None:
        self._code = code
        self._knowledge = knowledge
        self._requirements = requirements
        self._impact = impact

    def resolve(self, changes: ChangeSet) -> ChangeContext:
        code, code_truncated = self._code_for(changes)
        return ChangeContext(
            scope=changes.scope,
            code=code,
            knowledge=self._knowledge_for(changes),
            requirements=self._requirements_for(changes),
            impact=self._impact_for(changes),
            truncated=code_truncated,
        )

    def _code_for(self, changes: ChangeSet):
        if self._code is None:
            return None, False
        seeds = []
        truncated = False
        try:
            for path in changes.paths:
                found = self._code.search(
                    CodeSearch(
                        scope=changes.code_scope,
                        properties={"file_path": path},
                        limit=1,
                    )
                )
                seeds.extend(node.id for node in found.nodes)
                truncated = truncated or found.has_more
            if not seeds:
                return None, truncated
            return (
                self._code.traverse(
                    CodeTraversal(
                        scope=changes.code_scope,
                        node_ids=tuple(dict.fromkeys(seeds))[:100],
                        depth=changes.depth,
                        node_limit=changes.limit,
                    )
                ),
                truncated,
            )
        except UNAVAILABLE:
            return None, truncated

    def _knowledge_for(self, changes: ChangeSet):
        if self._knowledge is None:
            return ()
        try:
            return self._knowledge.select(
                KnowledgeSelection(
                    scope=changes.scope,
                    paths=changes.paths,
                    title=changes.title,
                    description=changes.description,
                    diff=changes.diff,
                )
            )
        except UNAVAILABLE:
            return ()

    def _requirements_for(self, changes: ChangeSet):
        if self._requirements is None:
            return ()
        try:
            found = []
            for scope in changes.requirement_scopes or (changes.scope,):
                found.extend(
                    self._requirements.select(
                        RequirementSelection(
                            scope=scope,
                            paths=changes.paths,
                            title=changes.title,
                            description=changes.description,
                            diff=changes.diff,
                        )
                    )
                )
            return tuple(found[:20])
        except UNAVAILABLE:
            return ()

    def _impact_for(self, changes: ChangeSet):
        references = changes.code_references()
        if self._impact is None or not references:
            return None
        try:
            return self._impact.resolve(
                ChangeImpactRequest(
                    scope=changes.impact_scope or changes.scope,
                    changes=references[:100],
                    depth=changes.depth,
                )
            )
        except UNAVAILABLE:
            return None
