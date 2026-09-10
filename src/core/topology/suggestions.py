from dataclasses import asdict

from .inference import infer_dependencies


def suggest_groups(repositories, manifests, graph_edges=(), systems=None):
    """Which repositories belong together, and what to join them out of.

    Reading a repository already derives a system for it, so a group is a group
    of systems. `systems` maps a repository name to the system that stands for
    it; a repository missing from it has never been read, and there is nothing
    to join yet.
    """
    systems = systems or {}
    parents = {name: name for name in repositories}

    def root(name):
        while parents[name] != name:
            name = parents[name]
        return name

    evidence = []
    for edge in (*infer_dependencies(manifests), *graph_edges):
        if edge.source_id not in parents or edge.target_id not in parents:
            continue
        parents[root(edge.target_id)] = root(edge.source_id)
        evidence.append(edge)
    groups = {}
    for name in sorted(repositories):
        groups.setdefault(root(name), []).append(name)
    read = {manifest.repository for manifest in manifests}
    output = []
    for names in groups.values():
        edges = [
            edge
            for edge in evidence
            if edge.source_id in names and edge.target_id in names
        ]
        sources = sorted(
            {
                (
                    "manifest"
                    if edge.properties.get("inferred_from")
                    in {"manifest", "entry_point"}
                    else "code_graph"
                )
                for edge in edges
            }
        )
        output.append(
            {
                "name": names[0].split("/")[-1],
                "repositories": names,
                "because": (
                    "Repositories are connected by recorded dependencies"
                    if edges
                    else "No dependency evidence connects this repository to another selected repository"
                ),
                "confidence": min((edge.confidence for edge in edges), default=0.0),
                "sources": sources,
                "evidence": [asdict(item) for edge in edges for item in edge.evidence],
                "unread_repositories": [name for name in names if name not in read],
                "systems": [
                    {"repository": name, "system_id": systems[name]}
                    for name in names
                    if name in systems
                ],
                "without_system": [name for name in names if name not in systems],
            }
        )
    weak = {}
    for group in output:
        if len(group["repositories"]) != 1:
            continue
        name = group["repositories"][0]
        owner, short = name.split("/", 1)
        prefix, separator, _ = short.partition("-")
        if separator:
            weak.setdefault((owner, prefix), []).append(group)
    for (owner, prefix), candidates in sorted(weak.items()):
        if len(candidates) < 2:
            continue
        names = sorted(
            name for candidate in candidates for name in candidate["repositories"]
        )
        output = [group for group in output if group not in candidates]
        output.append(
            {
                "name": prefix,
                "repositories": names,
                "because": "Repository names share an owner and prefix; no dependency evidence connects them",
                "confidence": 0.2,
                "sources": ["names"],
                "evidence": [{"kind": "name", "source": name} for name in names],
                "unread_repositories": [name for name in names if name not in read],
                "systems": [
                    {"repository": name, "system_id": systems[name]}
                    for name in names
                    if name in systems
                ],
                "without_system": [name for name in names if name not in systems],
            }
        )
    return sorted(output, key=lambda group: group["repositories"])
