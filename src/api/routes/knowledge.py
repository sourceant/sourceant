"""Reading and recording local knowledge over HTTP.

Knowledge already reaches an agent over MCP. This is the same store for a
person: what a repository's decisions, conventions and constraints are, from a
browser rather than from a model.

Scoped like the code index, and for the same reason: a repository is answered
for only when it was registered on this machine, and recording needs the server
to have been started by ``sourceant serve``. See ``code.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.routes.code import find_repository, require_local
from src.api.routes.local_settings import WHOEVER_IS_HERE
from src.config.settings import DEFAULT_TOKEN_LIMIT
from src.config.db import get_engine
from src.core.knowledge.proposing import propose
from src.core.knowledge.seeding import read
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
from src.llms.litellm_provider import LiteLLMProvider

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


class InitializeInput(BaseModel):
    repository: str
    # Reading is the safe half and answering is the useful one, so a caller can
    # ask what a repository states without anything being recorded.
    dry_run: bool = False
    # Reading finds only what somebody bothered to type. Asking finds what they
    # did not, and costs whatever the model costs, so it is asked for.
    use_model: bool = False


def _model_for_this_machine():
    """The model this machine was told to ask, or None if it was told none."""
    from src.core.settings.resolver import resolve

    def value(key: str) -> str:
        return str(resolve(key, user=WHOEVER_IS_HERE).value or "")

    name = value("model.name")
    if not name:
        return None
    return LiteLLMProvider(
        model=name,
        token_limit=DEFAULT_TOKEN_LIMIT,
        api_key=value("model.api_key"),
        api_base=value("model.base_url"),
    )


def _evidence(root: Path) -> tuple[list[str], str]:
    """What a repository looks like, and what it says, without sending all of it."""
    layout = []
    for path in sorted(root.rglob("*"))[:4000]:
        if path.is_dir() or any(part.startswith(".") for part in path.parts):
            continue
        layout.append(path.relative_to(root).as_posix())
        if len(layout) >= 400:
            break

    prose = ""
    for name in ("README.md", "README.rst", "README.txt"):
        candidate = root / name
        if candidate.is_file():
            try:
                prose = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                prose = ""
            break
    return layout, prose


@router.post("/initialize", dependencies=[Depends(require_local)])
def initialize(body: InitializeInput, store: Any = Depends(get_knowledge)):
    """Record what a repository already states about itself.

    Most repositories have written some of this down: a decision record, a
    conventions section, a page of rules for whatever works on them. It is
    knowledge already, just nowhere a tool can reach.

    Nothing here is judged or summarised, and everything points back at the
    heading it came from so a person can check it in one step. It all arrives
    proposed, because nobody has agreed to any of it yet.
    """
    entry = find_repository(body.repository)
    root = Path(entry.path)
    seeds = read(root)
    found = [
        {**payload(seed.knowledge), "source": seed.path, "from": "repository"}
        for seed in seeds
    ]
    items = [seed.knowledge for seed in seeds]

    if body.use_model:
        provider = _model_for_this_machine()
        if provider is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No model is configured on this machine. Choose one in "
                    "Settings, or read what the repository states instead."
                ),
            )
        layout, prose = _evidence(root)
        try:
            proposals = propose(
                repository=entry.name,
                layout=layout,
                prose=prose,
                known=items,
                ask=provider.generate_text,
                model=provider.model,
            )
        except Exception as error:  # noqa: BLE001 - whatever a provider raises
            raise HTTPException(status_code=502, detail=str(error)) from error
        items += [proposal.knowledge for proposal in proposals]
        found += [
            {**payload(proposal.knowledge), "source": proposal.model, "from": "model"}
            for proposal in proposals
        ]

    if not body.dry_run:
        for item in items:
            store.put(entry.scope, item)

    return success_response(
        {"found": found, "recorded": 0 if body.dry_run else len(items)}
    )


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
