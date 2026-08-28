from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from src.core.code_index import CodeTraversalResult
from src.core.knowledge import KnowledgeObject
from src.core.requirements import Requirement
from src.core.impact import ChangedCodeReference, ChangeImpact
from src.core.scope import Scope


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

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.files)

    def code_references(self) -> tuple[ChangedCodeReference, ...]:
        if not self.revision:
            return ()
        return tuple(
            ChangedCodeReference(
                id=f"file:{item.path}",
                kind="file",
                revision=self.revision,
                path=item.path,
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
