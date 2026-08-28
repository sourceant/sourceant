"""Reading and recording local knowledge over HTTP.

Knowledge already reaches an agent over MCP. This is the same store for a
person: what a repository's decisions, conventions and constraints are, from a
browser rather than from a model.

Scoped like the code index, and for the same reason: a repository is answered
for only when it was registered on this machine, and recording needs the server
to have been started by ``sourceant serve``. See ``code.py``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.routes.code import find_repository, require_local
from src.config.db import get_engine
from src.core.knowledge import (
    InMemoryKnowledgeRepository,
    KnowledgeObject,
    KnowledgeQuery,
    KnowledgeRemover,
    KnowledgeRepository,
    SQLKnowledgeRepository,
)
from src.core.responses import success_response
from src.core.services import service_registry

router = APIRouter()

_fallback: Any = None


def get_knowledge() -> Any:
    """The plugin-provided store when one is registered, else core's own.

    ``ServiceRegistry.register`` allows a single provider per interface, so core
    cannot pre-register alongside a plugin. Resolution has to happen per request.
    """
    global _fallback
    try:
        return service_registry.resolve(KnowledgeRepository)
    except LookupError:
        pass
    if _fallback is None:
        engine = get_engine()
        _fallback = (
            SQLKnowledgeRepository(engine)
            if engine is not None
            else InMemoryKnowledgeRepository()
        )
    return _fallback


def payload(item: KnowledgeObject) -> dict[str, Any]:
    return {
        "id": item.id,
        "kind": item.kind,
        "status": item.status,
        "summary": item.summary,
        "properties": dict(item.properties),
    }


@router.get("")
def read_knowledge(
    repository: str = Query(...),
    kinds: list[str] = Query(default=[]),
    statuses: list[str] = Query(default=[]),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    store: Any = Depends(get_knowledge),
):
    """What is recorded about one repository."""
    entry = find_repository(repository)
    result = store.search(
        KnowledgeQuery(
            scope=entry.scope,
            kinds=frozenset(kinds),
            statuses=frozenset(statuses),
            limit=limit,
            offset=offset,
        )
    )
    return success_response(
        {
            "items": [payload(item) for item in result.items],
            "total": result.total,
            "has_more": result.has_more,
        }
    )


class KnowledgeInput(BaseModel):
    repository: str
    id: str
    kind: str
    status: str = "accepted"
    summary: str
    properties: dict[str, Any] = Field(default_factory=dict)


@router.put("", dependencies=[Depends(require_local)])
def write_knowledge(body: KnowledgeInput, store: Any = Depends(get_knowledge)):
    """Record something about a repository, or replace what was recorded."""
    entry = find_repository(body.repository)
    try:
        item = KnowledgeObject(
            id=body.id,
            kind=body.kind,
            status=body.status,
            summary=body.summary,
            properties=body.properties,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    store.put(entry.scope, item)
    return success_response(payload(item))


@router.delete("", dependencies=[Depends(require_local)])
def forget_knowledge(
    repository: str = Query(...),
    id: str = Query(...),
    store: Any = Depends(get_knowledge),
):
    """Remove something recorded, where the store can remove.

    A store that only ever appends is still a usable store, so this asks rather
    than assuming.
    """
    entry = find_repository(repository)
    if not isinstance(store, KnowledgeRemover):
        raise HTTPException(
            status_code=501, detail="This store does not remove what it recorded"
        )
    store.remove(entry.scope, id)
    return success_response({"id": id})
