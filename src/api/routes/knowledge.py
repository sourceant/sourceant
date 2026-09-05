"""Reading and recording local knowledge over HTTP.

Knowledge already reaches an agent over MCP. This is the same store for a
person: what a repository's decisions, conventions and constraints are, from a
browser rather than from a model.

Scoped like the code index, and for the same reason: a repository is answered
for only when it was registered on this machine, and recording needs the server
to have been started by ``sourceant serve``. See ``code.py``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.routes.code import find_repository, registered, require_local
from src.api.routes.local_settings import local_provider
from src.core.environment import LOCAL
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
    repository: str = Query(default=""),
    kinds: list[str] = Query(default=[]),
    statuses: list[str] = Query(default=[]),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    store: Any = Depends(get_knowledge),
):
    """What is recorded, about one repository or about every one.

    A decision is remembered by what it decided rather than by which checkout
    it was recorded against, so naming no repository answers about all of them.
    Each item says which one it came from, since across repositories that is
    the thing a reader cannot infer.
    """
    wanted = [find_repository(repository)] if repository else registered()

    items: list[dict] = []
    total = 0
    for entry in wanted:
        result = store.search(
            KnowledgeQuery(
                scope=entry.scope,
                kinds=frozenset(kinds),
                statuses=frozenset(statuses),
                # Asked for whole, then cut once. Asking each for a page of the
                # answer would drop whatever fell past one repository's page
                # while another had room.
                limit=100,
                offset=0,
            )
        )
        total += result.total
        items.extend(
            {**payload(item), "repository": entry.name} for item in result.items
        )

    page = items[offset : offset + limit]
    return success_response(
        {
            "items": page,
            "total": total,
            "has_more": offset + len(page) < len(items),
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


def accept_above() -> float:
    """How sure a proposal has to be to skip a person, or zero for none of them."""
    from src.core.settings.resolver import resolve

    try:
        return float(resolve("knowledge.accept_above", user=LOCAL).value or 0)
    except Exception:  # noqa: BLE001 - a store that cannot answer accepts nothing
        return 0.0


def agreed(
    item: KnowledgeObject, confidence: float, threshold: float
) -> KnowledgeObject:
    """The same thing, marked accepted where somebody said that is sure enough.

    A threshold of zero accepts nothing, which is the default: agreeing on
    somebody's behalf is not a favour until they have said it is.
    """
    if threshold <= 0 or confidence < threshold:
        return item
    return replace(item, status="accepted")


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
    # What a repository states about itself is quoted rather than inferred, so
    # it is as sure as anything here gets.
    threshold = accept_above()
    seeds = read(root)
    found = [
        {**payload(seed.knowledge), "source": seed.path, "from": "repository"}
        for seed in seeds
    ]
    items = [agreed(seed.knowledge, 1.0, threshold) for seed in seeds]

    if body.use_model:
        provider = local_provider()
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
        items += [
            agreed(proposal.knowledge, proposal.confidence, threshold)
            for proposal in proposals
        ]
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
