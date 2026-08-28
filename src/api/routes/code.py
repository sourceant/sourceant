"""Reading and building the local code index over HTTP.

The registry is the authorization for reading. A scope is never taken from the
query string: the caller names a repository, and it is served only when that
name was registered on this machine by ``sourceant repo add``. A deployment
nobody registered a repository on therefore answers nothing here, which is what
keeps routes that carry no token from reaching a scope somebody else owns.

Registering is a different matter, because whoever can register a path can then
read it, and the registry cannot vouch for a route that fills the registry. So
the write routes exist only when the server was started by ``sourceant serve``,
which is the local command. Both deployment scripts run uvicorn directly and
never reach them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.cli.local_index import (
    RegisteredRepository,
    RegistryError,
    add_repository,
    list_repositories,
    remove_repository,
)
from src.config.db import get_engine
from src.config.settings import LOCAL_MODE
from src.core.code_index import (
    MAX_GRAPH_NODES,
    CodeEdge,
    CodeGraphQuery,
    CodeGraphReader,
    CodeIndexReader,
    CodeIndexWriter,
    CodeNode,
    CodeSearch,
    InMemoryCodeIndex,
    SQLCodeIndexRepository,
)
from src.core.code_index.clustering import Modularity, degrees
from src.core.code_index.linking import index_directories, index_paths, resolve
from src.core.responses import success_response
from src.core.services import service_registry

router = APIRouter()

NO_REPOSITORIES = "No repository is registered on this machine"
NOT_LOCAL = (
    "This server was not started with 'sourceant serve', so it does not change "
    "what is indexed on the machine it runs on"
)

_fallback: Any = None


def require_local() -> None:
    if not LOCAL_MODE:
        raise HTTPException(status_code=403, detail=NOT_LOCAL)


def get_code_index() -> Any:
    """The plugin-provided index when one is registered, else core's own store.

    ``ServiceRegistry.register`` allows a single provider per interface, so core
    cannot pre-register alongside a plugin. Resolution has to happen per request.
    """
    global _fallback
    try:
        return service_registry.resolve(CodeIndexReader)
    except LookupError:
        pass
    if _fallback is None:
        engine = get_engine()
        _fallback = (
            SQLCodeIndexRepository(engine)
            if engine is not None
            else InMemoryCodeIndex()
        )
    return _fallback


def registered() -> list[RegisteredRepository]:
    try:
        return list_repositories()
    except RegistryError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


def find_repository(name: str) -> RegisteredRepository:
    entries = registered()
    for entry in entries:
        if entry.name == name:
            return entry
    if not entries:
        raise HTTPException(status_code=404, detail=NO_REPOSITORIES)
    raise HTTPException(
        status_code=404, detail=f"{name} is not registered on this machine"
    )


def node_payload(node: CodeNode) -> dict[str, Any]:
    """One node in the shape a graph view already draws.

    ``kind`` says what the thing is, never what it is written in: a file is a
    file whatever its language, and a drawing that coloured Python files apart
    from Go ones would be colouring the wrong question. The language is its own
    field for whoever wants it.
    """
    properties = dict(node.properties)
    labels = sorted(node.labels)
    lowered = {label.lower() for label in labels}
    kind = str(properties.get("kind") or "").lower()

    payload = {
        "id": node.id,
        "name": properties.get("name") or node.id,
        "kind": kind or next(iter(sorted(lowered)), ""),
        "path": properties.get("file_path", ""),
        # What the index filed it under. Kind answers the same question for
        # everything drawn today, so this is for whoever wants the raw thing.
        "labels": labels,
    }
    if "file" in lowered:
        payload["kind"] = "file"
        payload["language"] = kind
    return payload


def joined(nodes, edges):
    """The graph with its files joined to the files they import.

    An import is stored as the text somebody wrote, so on its own it joins a
    file to a name and nothing else: a repository drawn from that is one island
    per file, which is a picture of a directory listing rather than of code. The
    connections between files are most of what anybody is looking for.

    An import that resolves becomes the edge between the two files and its own
    node goes: it stood for a file, and now the file is there.

    One that does not resolve names something outside the repository, and it
    goes too. It has nothing on the far side of it, so it draws as a spur off
    the file that wrote it, and a hundred of those is a picture of a package
    manifest rather than of the code. The index keeps them either way; this is
    only about what is drawn.
    """
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
                    CodeEdge(f"imports:{source}:{target}", source, target, "IMPORTS")
                )
    return kept_nodes, tuple(kept_edges)


@router.get("/repositories")
def read_repositories():
    """Every repository registered on this machine, for a client drawing all of them."""
    return success_response(
        [{"name": item.name, "path": item.path} for item in registered()]
    )


@router.get("/graph")
def read_graph(
    repository: str = Query(...),
    path_prefix: str = Query(""),
    include_tests: bool = Query(False),
    node_limit: int = Query(MAX_GRAPH_NODES, ge=1, le=MAX_GRAPH_NODES),
    index: Any = Depends(get_code_index),
):
    """A whole scope at once, in the shape a graph view draws."""
    entry = find_repository(repository)
    if not isinstance(index, CodeGraphReader):
        raise HTTPException(
            status_code=501,
            detail="The configured index cannot read a whole scope at once",
        )
    result = index.graph(
        CodeGraphQuery(
            scope=entry.scope,
            path_prefix=path_prefix,
            include_tests=include_tests,
            node_limit=node_limit,
        )
    )
    nodes, edges = joined(result.nodes, result.edges)

    # How busy a node is decides how it is drawn, and which part it belongs to
    # decides its colour. Both are about the graph rather than any one node, so
    # neither can be answered while building one, and both have to be answered
    # after the files have been joined or every file is its own part.
    met = degrees(nodes, edges)
    grouped = Modularity().cluster(nodes, edges)

    drawn = []
    for node in nodes:
        payload = node_payload(node)
        payload["degree"] = met.get(node.id, 0)
        payload["community"] = grouped.of.get(node.id)
        drawn.append(payload)

    return success_response(
        {
            "nodes": drawn,
            "links": [
                {
                    "source": edge.source_id,
                    "target": edge.target_id,
                    "type": edge.type.lower(),
                }
                for edge in edges
            ],
            "communities": [
                {"id": part.id, "name": part.name, "size": part.size}
                for part in grouped.communities
            ],
            "truncated": result.truncated,
            "focus": None,
        }
    )


@router.get("/nodes")
def read_nodes(
    repository: str = Query(...),
    file_path: str = Query(""),
    labels: list[str] = Query(default=[]),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    index: Any = Depends(get_code_index),
):
    """A page of nodes, filtered on what the index can filter on without a scan.

    Label and file path only. The index matches properties by equality, so a
    substring search here would read every node in the scope to answer; a client
    that has already drawn the graph filters what it drew instead.
    """
    entry = find_repository(repository)
    result = index.search(
        CodeSearch(
            scope=entry.scope,
            labels=frozenset(labels),
            properties={"file_path": file_path} if file_path else {},
            limit=limit,
            offset=offset,
        )
    )
    return success_response(
        {
            "nodes": [node_payload(node) for node in result.nodes],
            "total": result.total,
            "has_more": result.has_more,
        }
    )


class RepositoryInput(BaseModel):
    path: str
    name: str = ""


class IndexInput(BaseModel):
    repository: str = ""
    update: bool = True
    everything: bool = False


@router.post("/repositories", dependencies=[Depends(require_local)])
def create_repository(body: RepositoryInput):
    """Cover one more directory, so the next index run reads it too."""
    try:
        entry = add_repository(Path(body.path), name=body.name)
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return success_response({"name": entry.name, "path": entry.path})


@router.delete("/repositories", dependencies=[Depends(require_local)])
def delete_repository(path: str = Query(...)):
    """Stop covering a directory. What was already indexed is left alone."""
    if not remove_repository(Path(path)):
        raise HTTPException(status_code=404, detail=f"{path} was not registered")
    return success_response({"path": path})


@router.post("/index", dependencies=[Depends(require_local)])
def run_index(body: IndexInput, index: Any = Depends(get_code_index)):
    """Read registered repositories into the graph.

    This answers when the reading is done rather than starting something and
    reporting later. A caller that cannot tell a finished index from an
    unfinished one draws half a repository and calls it the repository.

    It writes through the same store the reads come from. Building its own
    would let a plugin's index be read while core's was the one being filled.
    """
    from src.cli.index_commands import _excluded_paths
    from src.core.code_index.indexer import RepositoryIndexer

    if not isinstance(index, CodeIndexWriter):
        raise HTTPException(
            status_code=501, detail="The configured index cannot be written to"
        )

    if body.everything or not body.repository:
        targets = registered()
        if not targets:
            raise HTTPException(status_code=404, detail=NO_REPOSITORIES)
    else:
        targets = [find_repository(body.repository)]

    indexer = RepositoryIndexer(index)
    done = []
    for entry in targets:
        result = indexer.index(
            entry.scope,
            Path(entry.path),
            update=body.update,
            excluded_paths=_excluded_paths(entry.name),
        )
        done.append(
            {
                "repository": entry.name,
                "indexed": result.indexed,
                "unchanged": result.unchanged,
                "removed": result.removed,
                "skipped": result.skipped,
            }
        )
    return success_response(done)
