"""Propose topology relationships from what repositories declare about themselves.

A proposal is only as good as what it can point at, so every relationship here
comes from a manifest a repository actually publishes: the name it goes by, and
the names it declares a dependency on. Nothing is guessed, and nothing is
derived from a model, so a reader can check any proposal by opening one file.

Proposals are always pending. Deciding whether a relationship is real is a
human's job, and this only narrows what they have to look at.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from src.core.topology.models import TopologyEvidence, TopologyRelationship

DEPENDS_ON = "depends_on"


@dataclass(frozen=True)
class RepositoryManifest:
    """One manifest read from one repository."""

    entity_id: str
    repository: str
    path: str
    # The names this repository publishes itself under.
    identity: tuple[str, ...] = ()
    # The names it declares a dependency on.
    dependencies: tuple[str, ...] = ()
    properties: Mapping[str, Any] = field(default_factory=dict)


def _clean(values: Iterable[Any]) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        name = value.strip()
        if name and name not in seen:
            seen.append(name)
    return tuple(seen)


def parse_package_json(entity_id: str, repository: str, path: str, content: str):
    data = json.loads(content)
    deps: list[str] = []
    for key in (
        "dependencies",
        "devDependencies",
        "peerDependencies",
        "optionalDependencies",
    ):
        section = data.get(key)
        if isinstance(section, dict):
            deps.extend(section.keys())
    return RepositoryManifest(
        entity_id=entity_id,
        repository=repository,
        path=path,
        identity=_clean([data.get("name")]),
        dependencies=_clean(deps),
    )


def parse_composer_json(entity_id: str, repository: str, path: str, content: str):
    data = json.loads(content)
    deps: list[str] = []
    for key in ("require", "require-dev"):
        section = data.get(key)
        if isinstance(section, dict):
            # PHP platform requirements are not repositories.
            deps.extend(k for k in section if "/" in k)
    return RepositoryManifest(
        entity_id=entity_id,
        repository=repository,
        path=path,
        identity=_clean([data.get("name")]),
        dependencies=_clean(deps),
    )


_GO_MODULE = re.compile(r"^module\s+(\S+)", re.MULTILINE)
_GO_REQUIRE_BLOCK = re.compile(r"require\s*\((.*?)\)", re.DOTALL)
_GO_REQUIRE_LINE = re.compile(r"^require\s+(\S+)", re.MULTILINE)


def parse_go_mod(entity_id: str, repository: str, path: str, content: str):
    deps: list[str] = []
    for block in _GO_REQUIRE_BLOCK.findall(content):
        for line in block.splitlines():
            line = line.split("//")[0].strip()
            if line:
                deps.append(line.split()[0])
    deps.extend(_GO_REQUIRE_LINE.findall(content))
    identity = _GO_MODULE.findall(content)
    return RepositoryManifest(
        entity_id=entity_id,
        repository=repository,
        path=path,
        identity=_clean(identity),
        dependencies=_clean(deps),
    )


_REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def parse_requirements_txt(entity_id: str, repository: str, path: str, content: str):
    deps: list[str] = []
    for line in content.splitlines():
        line = line.split("#")[0].strip()
        # Options and includes are not dependencies.
        if not line or line.startswith("-"):
            continue
        match = _REQUIREMENT.match(line)
        if match:
            deps.append(match.group(1))
    return RepositoryManifest(
        entity_id=entity_id,
        repository=repository,
        path=path,
        dependencies=_clean(deps),
    )


def parse_pyproject_toml(entity_id: str, repository: str, path: str, content: str):
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

    data = tomllib.loads(content)
    project = data.get("project") or {}
    deps: list[str] = []
    for raw in project.get("dependencies") or []:
        match = _REQUIREMENT.match(str(raw))
        if match:
            deps.append(match.group(1))
    return RepositoryManifest(
        entity_id=entity_id,
        repository=repository,
        path=path,
        identity=_clean([project.get("name")]),
        dependencies=_clean(deps),
    )


PARSERS = {
    "package.json": parse_package_json,
    "composer.json": parse_composer_json,
    "go.mod": parse_go_mod,
    "requirements.txt": parse_requirements_txt,
    "pyproject.toml": parse_pyproject_toml,
}


def parse_manifest(entity_id: str, repository: str, path: str, content: str):
    """Parse a manifest by its filename, or return None when it is not one."""
    parser = PARSERS.get(path.rsplit("/", 1)[-1])
    if parser is None:
        return None
    try:
        return parser(entity_id, repository, path, content)
    except Exception:
        # A manifest that will not parse proposes nothing rather than failing
        # the whole system.
        return None


def _relationship_id(source_id: str, target_id: str) -> str:
    return f"{source_id}--{DEPENDS_ON}--{target_id}"


def infer_dependencies(
    manifests: Iterable[RepositoryManifest],
) -> tuple[TopologyRelationship, ...]:
    """
    Propose a dependency wherever one repository declares a name another one
    publishes itself under. Every proposal names the manifest it came from.
    """
    manifests = list(manifests)

    # Which entity publishes which name. A name published by more than one
    # repository is ambiguous and proposes nothing.
    published: dict[str, set[str]] = {}
    for manifest in manifests:
        for name in manifest.identity:
            published.setdefault(name, set()).add(manifest.entity_id)

    proposals: dict[str, TopologyRelationship] = {}
    for manifest in manifests:
        for name in manifest.dependencies:
            owners = published.get(name)
            if not owners or len(owners) > 1:
                continue
            target_id = next(iter(owners))
            if target_id == manifest.entity_id:
                continue

            evidence = TopologyEvidence(
                id=f"{manifest.repository}:{manifest.path}:{name}",
                kind="manifest",
                source=f"{manifest.repository}/{manifest.path}",
                properties={"declared": name},
            )
            key = _relationship_id(manifest.entity_id, target_id)
            existing = proposals.get(key)
            if existing:
                proposals[key] = TopologyRelationship(
                    id=existing.id,
                    source_id=existing.source_id,
                    target_id=existing.target_id,
                    type=existing.type,
                    status=existing.status,
                    confidence=existing.confidence,
                    properties=existing.properties,
                    evidence=existing.evidence + (evidence,),
                )
                continue

            proposals[key] = TopologyRelationship(
                id=key,
                source_id=manifest.entity_id,
                target_id=target_id,
                type=DEPENDS_ON,
                # Proposed, never applied. A person decides whether it is real.
                status="pending",
                confidence=1.0,
                properties={"inferred_from": "manifest"},
                evidence=(evidence,),
            )

    return tuple(proposals.values())
