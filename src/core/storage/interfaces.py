from __future__ import annotations

from collections.abc import Iterable
from typing import BinaryIO, Protocol, runtime_checkable

from src.core.scope import Scope

from .models import (
    Artifact,
    ArtifactKey,
    ArtifactWrite,
    WorkingArea,
    WorkingAreaRequest,
)


@runtime_checkable
class ArtifactReader(Protocol):
    def get(self, key: ArtifactKey) -> Artifact | None: ...

    def open(self, key: ArtifactKey) -> BinaryIO: ...

    def list(
        self, scope: Scope, namespace: str, name: str | None = None
    ) -> tuple[Artifact, ...]: ...


@runtime_checkable
class ArtifactWriter(Protocol):
    def put(self, request: ArtifactWrite, content: BinaryIO) -> Artifact: ...

    def delete(self, key: ArtifactKey) -> bool: ...


@runtime_checkable
class ArtifactStore(ArtifactReader, ArtifactWriter, Protocol):
    pass


@runtime_checkable
class WorkingAreaStore(Protocol):
    def provision(self, request: WorkingAreaRequest) -> WorkingArea: ...

    def get(self, request: WorkingAreaRequest) -> WorkingArea | None: ...

    def remove(self, request: WorkingAreaRequest) -> bool: ...

    def list(
        self, scope: Scope, purpose: str | None = None
    ) -> Iterable[WorkingArea]: ...
