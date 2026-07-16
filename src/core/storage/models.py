from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Mapping

from src.core.scope import Scope


@dataclass(frozen=True)
class ArtifactKey:
    scope: Scope
    namespace: str
    name: str
    version: str

    def __post_init__(self) -> None:
        if any(not value for value in (self.namespace, self.name, self.version)):
            raise ValueError("artifact namespace, name, and version must not be empty")


@dataclass(frozen=True)
class ContentDigest:
    algorithm: str
    value: str

    def __post_init__(self) -> None:
        if not self.algorithm or not self.value:
            raise ValueError("digest algorithm and value must not be empty")


@dataclass(frozen=True)
class Artifact:
    key: ArtifactKey
    digest: ContentDigest
    size: int
    media_type: str
    created_at: datetime
    properties: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.size < 0:
            raise ValueError("artifact size must not be negative")
        if not self.media_type:
            raise ValueError("artifact media_type must not be empty")


@dataclass(frozen=True)
class ArtifactWrite:
    key: ArtifactKey
    media_type: str = "application/octet-stream"
    digest_algorithm: str = "sha256"
    expected_digest: ContentDigest | None = None
    properties: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkingAreaRequest:
    scope: Scope
    purpose: str
    name: str

    def __post_init__(self) -> None:
        if not self.purpose or not self.name:
            raise ValueError("working area purpose and name must not be empty")


@dataclass(frozen=True)
class WorkingArea:
    request: WorkingAreaRequest
    path: Path
