from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from src.core.code_index import CodeTraversalResult
from src.core.knowledge import KnowledgeObject
from src.core.requirements import Requirement
from src.core.impact import ChangedCodeReference, ChangeImpact
from src.core.scope import Scope
from src.core.settings.configuration import Configuration


@dataclass(frozen=True)
class ChangedFile:
    path: str
    change: str = "modified"
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("a changed file needs a path")


@dataclass(frozen=True)
class ChangeSet:
    scope: Scope
    files: tuple[ChangedFile, ...]
    revision: str = ""
    base_revision: str = ""
    title: str = ""
    description: str = ""
    diff: str = ""
    depth: int = 2
    limit: int = 50
    requirement_scopes: tuple[Scope, ...] = ()
    #: Where the topology a change reaches into is filed. Wider than the
    #: repository, because asked of the repository alone the walk cannot leave
    #: it.
    impact_scope: Scope | None = None
    #: Where every setting this review reads is resolved from. Separate from
    #: `scope`, which also files code in the index and so carries nothing
    #: wider than the repository.
    configuration: Configuration = field(default_factory=Configuration)

    def __post_init__(self) -> None:
        if not self.files:
            raise ValueError("a change set needs at least one file")
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("changed files must be unique")
        if not 1 <= self.depth <= 3:
            raise ValueError("depth must be between 1 and 3")
        if not 1 <= self.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if self.configuration == Configuration():
            object.__setattr__(
                self, "configuration", Configuration.from_scope(self.scope)
            )

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.files)

    @property
    def code_scope(self) -> Scope:
        """Where code structure is filed.

        Code is pinned to the commit it was read at. Knowledge and requirements
        are not, because a decision outlives the commit it was recorded on.
        """
        if not self.revision:
            return self.scope
        return self.scope.extend({"revision": self.revision})

    def code_references(self) -> tuple[ChangedCodeReference, ...]:
        if not self.revision:
            return ()
        return tuple(
            ChangedCodeReference(
                id=f"file:{item.path}",
                kind="file",
                revision=self.revision,
                path=item.path,
                repository=str(self.scope.get("repository") or ""),
                properties={"change": item.change},
            )
            for item in self.files
        )


@dataclass(frozen=True)
class ChangeContext:
    scope: Scope
    code: CodeTraversalResult | None = None
    knowledge: tuple[KnowledgeObject, ...] = ()
    requirements: tuple[Requirement, ...] = ()
    impact: ChangeImpact | None = None
    truncated: bool = False

    @property
    def empty(self) -> bool:
        return not (
            self.knowledge
            or self.requirements
            or (self.code and self.code.nodes)
            or (self.impact and self.impact.findings)
        )
