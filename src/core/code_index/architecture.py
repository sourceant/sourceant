from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

from .models import CodeGraphResult

SCHEMA_VERSION = 1
EVIDENCE_LIMIT = 3
EDGE_TYPES = frozenset({"CALLS", "IMPORTS", "INHERITS", "IMPLEMENTS", "DEPENDS_ON"})
RELATION_LIMIT = 1000


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        return ""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return ""
    return path.as_posix()


def summarize(
    graph: CodeGraphResult,
    *,
    repository: str,
    depth: int = 1,
    include_tests: bool = False,
) -> dict[str, Any]:
    if not repository or not 1 <= depth <= 4:
        raise ValueError("A repository and a depth between 1 and 4 are required")

    groups: dict[str, dict] = {}
    owners: dict[str, str] = {}
    locations: dict[str, dict] = {}
    signatures: dict[str, list] = defaultdict(list)
    missing = 0
    for node in graph.nodes:
        path = _path(node.properties.get("file_path"))
        if not path:
            missing += 1
            continue
        prefix = "/".join(PurePosixPath(path).parts[:-1][:depth]) or "."
        identity = "part:" + _digest([repository, prefix])[:24]
        part = groups.setdefault(
            identity,
            {
                "id": identity,
                "name": prefix,
                "path": prefix,
                "files": set(),
                "nodes": 0,
            },
        )
        part["files"].add(path)
        part["nodes"] += 1
        owners[node.id] = identity
        location = {"path": path, "symbol": node.id}
        line = node.properties.get("start_line")
        if isinstance(line, int) and not isinstance(line, bool) and line > 0:
            location["line"] = line
        locations[node.id] = location
        signatures[identity].append(
            [node.id, sorted(node.labels), dict(node.properties)]
        )

    connections: dict[tuple[str, str, str], dict] = {}
    outgoing: dict[str, int] = defaultdict(int)
    incoming: dict[str, int] = defaultdict(int)
    omitted_edges = 0
    limited = False
    for edge in graph.edges:
        kind = edge.type.upper()
        if kind not in EDGE_TYPES:
            continue
        source, target = owners.get(edge.source_id), owners.get(edge.target_id)
        if not source or not target:
            omitted_edges += 1
            continue
        signatures[source].append(
            [edge.source_id, edge.target_id, kind, dict(edge.properties)]
        )
        if source == target:
            continue
        key = (source, target, kind)
        if key not in connections and len(connections) >= RELATION_LIMIT:
            limited = True
            continue
        connection = connections.setdefault(
            key,
            {
                "source": source,
                "target": target,
                "type": kind.lower(),
                "count": 0,
                "evidence": [],
            },
        )
        connection["count"] += 1
        outgoing[source] += 1
        incoming[target] += 1
        evidence_key = json.dumps(
            {
                "source": locations[edge.source_id],
                "target": locations[edge.target_id],
                "origin": edge.properties.get("origin", "extracted"),
            },
            sort_keys=True,
        )
        sample = connection["evidence"]
        if evidence_key not in sample:
            sample.append(evidence_key)
            sample.sort()
            del sample[EVIDENCE_LIMIT:]

    components = []
    for identity, part in sorted(groups.items(), key=lambda item: item[1]["path"]):
        files = sorted(part["files"])
        components.append(
            {
                **part,
                "files": len(files),
                "sample_files": files[:EVIDENCE_LIMIT],
                "incoming": incoming[identity],
                "outgoing": outgoing[identity],
                "fingerprint": _digest(
                    sorted(
                        signatures[identity],
                        key=lambda row: json.dumps(row, sort_keys=True),
                    )
                ),
            }
        )
    relationships = [
        {
            **connection,
            "evidence": [json.loads(item) for item in connection["evidence"]],
        }
        for _, connection in sorted(connections.items())
    ]
    coverage = {
        "nodes": len(graph.nodes),
        "files": len({loc["path"] for loc in locations.values()}),
        "unplaced_nodes": missing,
        "unresolved_edges": omitted_edges,
        "truncated": graph.truncated or limited,
    }
    result = {
        "schema_version": SCHEMA_VERSION,
        "repository": repository,
        "grouping": {
            "kind": "directory",
            "depth": depth,
            "include_tests": include_tests,
        },
        "components": components,
        "relationships": relationships,
        "coverage": coverage,
    }
    return {**result, "fingerprint": _digest(result)}


def compare(before: dict, after: dict) -> dict:
    for key in ("schema_version", "repository", "grouping"):
        if before.get(key) != after.get(key):
            raise ValueError(f"Snapshots must have the same {key}")
    if before.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported snapshot version")
    for snapshot in (before, after):
        identities = {part["id"] for part in snapshot["components"]}
        relations = {
            (edge["source"], edge["target"], edge["type"])
            for edge in snapshot["relationships"]
        }
        if len(identities) != len(snapshot["components"]) or len(relations) != len(
            snapshot["relationships"]
        ):
            raise ValueError("Snapshot identities must be unique")
        if any(
            source not in identities or target not in identities
            for source, target, _ in relations
        ):
            raise ValueError(
                "Snapshot relationships must reference existing components"
            )
        coverage = snapshot.get("coverage", {})
        if (
            coverage.get("truncated", True)
            or coverage.get("unplaced_nodes", 0)
            or coverage.get("unresolved_edges", 0)
        ):
            raise ValueError(
                "Incomplete snapshots cannot establish architecture changes"
            )

    old = {part["id"]: part for part in before["components"]}
    new = {part["id"]: part for part in after["components"]}
    components = []
    for identity in sorted(old.keys() | new.keys()):
        if identity not in old:
            status = "added"
        elif identity not in new:
            status = "removed"
        elif old[identity]["fingerprint"] != new[identity]["fingerprint"]:
            status = "modified"
        else:
            continue
        components.append({**new.get(identity, old.get(identity)), "status": status})

    def indexed(snapshot):
        return {
            (edge["source"], edge["target"], edge["type"]): edge
            for edge in snapshot["relationships"]
        }

    prior, current = indexed(before), indexed(after)
    relationships = []
    for key in sorted(prior.keys() | current.keys()):
        if key not in prior:
            status = "added"
        elif key not in current:
            status = "removed"
        elif prior[key] != current[key]:
            status = "modified"
        else:
            continue
        source, target, _ = key
        relationships.append(
            {
                **current.get(key, prior.get(key)),
                "status": status,
                "source_name": new.get(source, old.get(source))["name"],
                "target_name": new.get(target, old.get(target))["name"],
            }
        )
    return {
        "repository": after["repository"],
        "before": before["fingerprint"],
        "after": after["fingerprint"],
        "components": components,
        "relationships": relationships,
    }
