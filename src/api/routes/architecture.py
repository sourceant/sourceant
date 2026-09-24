from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.routes.code import find_repository, get_code_index, joined, require_local
from src.core.code_index import (
    MAX_GRAPH_NODES,
    CodeGraphQuery,
    CodeGraphReader,
    CodeGraphResult,
)
from src.core.code_index.architecture import compare, summarize
from src.core.responses import success_response

router = APIRouter(dependencies=[Depends(require_local)])


class Grouping(BaseModel):
    kind: Literal["directory"]
    depth: int = Field(ge=1, le=4)
    include_tests: bool


class Component(BaseModel):
    id: str = Field(max_length=100)
    name: str = Field(max_length=4096)
    path: str = Field(max_length=4096)
    files: int = Field(ge=0)
    nodes: int = Field(ge=0)
    sample_files: list[str] = Field(max_length=3)
    incoming: int = Field(ge=0)
    outgoing: int = Field(ge=0)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class Location(BaseModel):
    path: str = Field(max_length=4096)
    symbol: str = Field(max_length=8192)
    line: int | None = Field(default=None, ge=1)


class Evidence(BaseModel):
    source: Location
    target: Location
    origin: str = Field(max_length=100)


class Relationship(BaseModel):
    source: str = Field(max_length=100)
    target: str = Field(max_length=100)
    type: str = Field(max_length=100)
    count: int = Field(ge=1)
    evidence: list[Evidence] = Field(max_length=3)


class Coverage(BaseModel):
    nodes: int = Field(ge=0)
    files: int = Field(ge=0)
    unplaced_nodes: int = Field(ge=0)
    unresolved_edges: int = Field(ge=0)
    truncated: bool


class Snapshot(BaseModel):
    schema_version: Literal[1]
    repository: str = Field(min_length=1, max_length=512)
    grouping: Grouping
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    components: list[Component] = Field(max_length=MAX_GRAPH_NODES)
    relationships: list[Relationship] = Field(max_length=1000)
    coverage: Coverage


def snapshot(repository: str, depth: int, include_tests: bool, index: Any) -> dict:
    entry = find_repository(repository)
    if not isinstance(index, CodeGraphReader):
        raise HTTPException(
            status_code=501, detail="The configured index cannot read a graph"
        )
    graph = index.graph(CodeGraphQuery(entry.scope, include_tests=include_tests))
    nodes, edges = joined(graph.nodes, graph.edges)
    return summarize(
        CodeGraphResult(nodes, edges, graph.truncated),
        repository=entry.name,
        depth=depth,
        include_tests=include_tests,
    )


@router.get("")
def read_architecture(
    repository: str = Query(...),
    depth: int = Query(1, ge=1, le=4),
    include_tests: bool = Query(False),
    index: Any = Depends(get_code_index),
):
    return success_response(snapshot(repository, depth, include_tests, index))


@router.post("/compare")
def compare_architecture(before: Snapshot, index: Any = Depends(get_code_index)):
    current = snapshot(
        before.repository, before.grouping.depth, before.grouping.include_tests, index
    )
    try:
        result = compare(before.model_dump(exclude_none=True), current)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return success_response(result)
