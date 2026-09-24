from .linking import index_directories, index_paths, resolve
from .models import CodeEdge


def joined(nodes, edges):
    paths = {}
    imports = {}
    for node in nodes:
        labels = {label.lower() for label in node.labels}
        if "file" in labels:
            paths[node.properties.get("file_path", "")] = node.id
        elif "import" in labels:
            imports[node.id] = node

    if not imports:
        return nodes, edges

    named = [name for name in paths if name]
    by_name = index_paths(named)
    inside = index_directories(named)

    resolved: dict[str, tuple[str, ...]] = {}
    for node_id, node in imports.items():
        importer = str(node.properties.get("file_path", ""))
        found = resolve(by_name, importer, str(node.properties.get("name", "")), inside)
        reached = tuple(paths[path] for path in found if path in paths)
        if reached:
            resolved[node_id] = reached

    kept_nodes = tuple(node for node in nodes if node.id not in imports)
    kept_edges = []
    seen: set[tuple[str, str]] = set()
    for edge in edges:
        if edge.source_id not in imports and edge.target_id not in imports:
            kept_edges.append(edge)
            continue
        if edge.source_id in imports and edge.source_id not in resolved:
            continue
        if edge.target_id in imports and edge.target_id not in resolved:
            continue
        for source in resolved.get(edge.source_id, (edge.source_id,)):
            for target in resolved.get(edge.target_id, (edge.target_id,)):
                if source == target or (source, target) in seen:
                    continue
                seen.add((source, target))
                kept_edges.append(
                    CodeEdge(
                        f"imports:{source}:{target}",
                        source,
                        target,
                        "IMPORTS",
                        # Targets are inferred from paths, not compiler resolution.
                        {"origin": "inferred"},
                    )
                )
    return kept_nodes, tuple(kept_edges)
