"""Reading and building the local code index over HTTP.

The registry is the authorization for reading. A scope is never taken from the
query string: the caller names a repository, and it is served only when that
name was registered on this machine by ``sourceant repo add``. A deployment
nobody registered a repository on therefore answers nothing here, which is what
keeps routes that carry no token from reaching a hosted scope.

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
    # How busy a node is decides how it is drawn, and which part it belongs to
    # decides its colour. Both are about the graph rather than any one node, so
    # neither can be answered while building one.
    met = degrees(result.nodes, result.edges)
    grouped = Modularity().cluster(result.nodes, result.edges)

    nodes = []
    for node in result.nodes:
        payload = node_payload(node)
        payload["degree"] = met.get(node.id, 0)
        payload["community"] = grouped.of.get(node.id)
        nodes.append(payload)

    return success_response(
        {
            "nodes": nodes,
            "links": [
                {
                    "source": edge.source_id,
                    "target": edge.target_id,
                    "type": edge.type.lower(),
                }
                for edge in result.edges
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
