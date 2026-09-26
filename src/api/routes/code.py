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

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.cli.local_index import (
    RegisteredRepository,
    RegistryError,
    add_repository,
    list_repositories,
    mark_indexed,
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
from src.core.code_index.attention import attention
from src.core.code_index.clustering import Modularity, degrees
from src.core.code_index.relationships import joined
from src.core.responses import success_response
from src.core.services import service_registry
from src.utils.logger import logger

router = APIRouter()

NO_REPOSITORIES = "No repository is registered on this machine"
NOT_LOCAL = (
    "This server was not started with 'sourceant serve', so it does not change "
    "what is indexed on the machine it runs on"
)

_fallback: Any = None

# What is being read right now. One process serves a machine, so this lives
# here; anywhere several do, it would have to live where they both see it.
_reading: set[str] = set()


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


@router.get("/repositories")
def read_repositories():
    """Every repository registered on this machine, for a client drawing all of them."""
    return success_response([repository_payload(item) for item in registered()])


def repository_payload(entry: RegisteredRepository) -> dict[str, Any]:
    """One repository, and whether it has been read."""
    return {
        "name": entry.name,
        "path": entry.path,
        "indexed_at": entry.indexed_at,
        "reading": entry.name in _reading,
    }


@router.get("/attention")
def read_attention(
    repository: str = Query(...),
    limit: int = Query(10, ge=1, le=50),
    index: Any = Depends(get_code_index),
):
    """Where recent change is landing on what the rest of the code leans on.

    Two facts nobody has to supply: how much of the repository imports a file,
    and how often it has been changing. Either alone says little. A file half
    the codebase imports and nobody has touched in two years is settled. A file
    that changes constantly and nothing imports is somebody's scratch pad. It
    is where they meet that is worth a person's time, and that is also the
    shortest list of files worth reading first.
    """
    entry = find_repository(repository)
    if not isinstance(index, CodeGraphReader):
        raise HTTPException(
            status_code=501,
            detail="The configured index cannot read a whole scope at once",
        )

    result = index.graph(
        CodeGraphQuery(
            scope=entry.scope, include_tests=False, node_limit=MAX_GRAPH_NODES
        )
    )
    nodes, edges = joined(result.nodes, result.edges)

    # How many files import each one, which is what "leans on" means here.
    paths = {
        node.id: str(dict(node.properties).get("file_path") or "")
        for node in nodes
        if "file" in {label.lower() for label in node.labels}
    }
    dependants: dict[str, int] = {}
    for edge in edges:
        target = paths.get(edge.target_id)
        if target and paths.get(edge.source_id):
            dependants[target] = dependants.get(target, 0) + 1

    found = attention(dependants, Path(entry.path), limit=limit)
    return success_response(
        {
            "files": [
                {
                    "path": item.path,
                    "dependants": item.dependants,
                    "changes": item.changes,
                }
                for item in found
            ],
            # So a screen can say "in the last 90 days" rather than inventing a
            # window of its own.
            "since": "90 days",
        }
    )


@router.get("/graph")
def read_graph(
    repository: str = Query(...),
    path_prefix: str = Query(""),
    include_tests: bool = Query(False),
    node_limit: int = Query(MAX_GRAPH_NODES, ge=1, le=MAX_GRAPH_NODES),
    focus: str = Query(""),
    depth: int = Query(2, ge=1, le=5),
    q: str = Query("", max_length=500),
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

    if focus:
        known = {node.id for node in nodes}
        if focus not in known:
            raise HTTPException(404, "The selected node is not in this graph")
        neighbours: dict[str, set[str]] = {}
        for edge in edges:
            neighbours.setdefault(edge.source_id, set()).add(edge.target_id)
            neighbours.setdefault(edge.target_id, set()).add(edge.source_id)
        selected = {focus}
        frontier = {focus}
        for _ in range(depth):
            frontier = {
                other for node in frontier for other in neighbours.get(node, ())
            } - selected
            selected.update(frontier)
            if not frontier:
                break
        nodes = tuple(node for node in nodes if node.id in selected)
    if q.strip():
        term = q.strip().casefold()
        nodes = tuple(
            node
            for node in nodes
            if node.id == focus
            or any(
                term in str(value).casefold()
                for value in (
                    node.id,
                    node.properties.get("name", ""),
                    node.properties.get("file_path", ""),
                )
            )
        )
    selected = {node.id for node in nodes}
    edges = tuple(
        edge
        for edge in edges
        if edge.source_id in selected and edge.target_id in selected
    )

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
                    # Read out of the file, or worked out from it.
                    "origin": edge.properties.get("origin", "extracted"),
                }
                for edge in edges
            ],
            "communities": [
                {"id": part.id, "name": part.name, "size": part.size}
                for part in grouped.communities
            ],
            "truncated": result.truncated,
            "focus": focus or None,
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
    # Registering a folder nobody reads answers nothing about it, so reading
    # starts here unless the caller is about to ask for it differently.
    index: bool = True


class IndexInput(BaseModel):
    repository: str = ""
    update: bool = True
    everything: bool = False


@router.post("/repositories", dependencies=[Depends(require_local)])
def create_repository(
    body: RepositoryInput,
    background: BackgroundTasks,
    index: Any = Depends(get_code_index),
):
    """Cover one more directory, and start reading it.

    The reading happens behind the answer: a repository of any size takes
    longer than anybody will hold a modal open for, and the answer says it is
    being read so a client can show that.
    """
    try:
        entry = add_repository(Path(body.path), name=body.name)
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if body.index and isinstance(index, CodeIndexWriter):
        # Marked before the answer leaves, so a client that asks straight away
        # is told it is being read rather than that it never has been.
        _reading.add(entry.name)
        background.add_task(read_repository, entry, index, False)
    return success_response(repository_payload(entry))


def read_repository(
    entry: RegisteredRepository, index: Any, update: bool
) -> dict[str, Any]:
    """Read one registered repository, and record that it was read."""
    from src.cli.index_commands import _excluded_paths
    from src.core.code_index.indexer import RepositoryIndexer

    _reading.add(entry.name)
    try:
        result = RepositoryIndexer(index).index(
            entry.scope,
            Path(entry.path),
            update=update,
            excluded_paths=_excluded_paths(entry.name),
        )
        mark_indexed(entry.name)
    except Exception as error:  # noqa: BLE001 - whatever a parser raises
        # Logged as well as raised: behind an answer that has already gone out,
        # raising tells nobody.
        logger.warning("Reading %s failed: %s", entry.name, error)
        raise
    finally:
        _reading.discard(entry.name)
    return {
        "repository": entry.name,
        "indexed": result.indexed,
        "unchanged": result.unchanged,
        "removed": result.removed,
        "skipped": result.skipped,
    }


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

    done = [read_repository(entry, index, body.update) for entry in targets]
    return success_response(done)
