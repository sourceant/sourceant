import asyncio
from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.auth import get_current_user
from src.core.model import provider_for
from src.core.responses import success_response
from src.core.search import SearchQuery, Searcher
from src.core.settings.configuration import Configuration
from src.core.scope import Scope
from src.core.services import service_registry
from src.core.topology.inference import infer_dependencies
from src.core.topology.manifests import read_manifests
from src.core.topology.proposing import WhatItReads
from src.core.topology.reading import contents_reader
from src.core.topology.store import topology_repository
from src.utils.logger import logger
from src.core.topology import (
    contents,
    TopologyEntity,
    TopologyEvidence,
    TopologyQuery,
    TopologyRelationship,
    TopologyRepository,
    TopologyTraversal,
)

router = APIRouter()

STORE_UNAVAILABLE = "The topology store is unavailable"


def get_topology_repository() -> TopologyRepository:
    """The plugin-provided repository when one is registered, else core's own store."""
    return topology_repository()


def get_scope(user: dict = Depends(get_current_user)) -> Scope:
    workspace_id = (user.get("scope") or {}).get("workspace_id")
    if workspace_id is None:
        raise HTTPException(
            status_code=403, detail="Token does not carry a workspace scope"
        )
    return Scope.from_mapping({"workspace": str(workspace_id)})


class EvidenceInput(BaseModel):
    id: str
    kind: str
    source: str
    revision: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)


class EntityInput(BaseModel):
    id: str
    kind: str
    status: str
    confidence: float = 1.0
    stale: bool = False
    properties: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceInput] = Field(default_factory=list)


class RelationshipInput(BaseModel):
    id: str
    source_id: str
    target_id: str
    type: str
    status: str
    confidence: float = 1.0
    stale: bool = False
    properties: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceInput] = Field(default_factory=list)


class InferAssetInput(BaseModel):
    entity_id: str
    repository: str


class InferInput(BaseModel):
    assets: list[InferAssetInput] = Field(default_factory=list, max_length=100)
    # Proposals are recorded as pending by default so they can be looked at in
    # place. Asking for a preview leaves the graph untouched.
    persist: bool = True
    # Which system this was asked for. An asset says which system it belongs to
    # and a system does not, so a proposal between two systems has no owner to
    # work out from its endpoints, and one nothing owns is never shown.
    system_id: str | None = None


class SearchInput(BaseModel):
    ids: list[str] = Field(default_factory=list)
    kinds: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    minimum_confidence: float = 0.0
    include_stale: bool = True
    limit: int = 50
    offset: int = 0


class TraversalInput(BaseModel):
    entity_ids: list[str]
    depth: int = 2
    entity_kinds: list[str] = Field(default_factory=list)
    entity_statuses: list[str] = Field(default_factory=list)
    relationship_types: list[str] = Field(default_factory=list)
    relationship_statuses: list[str] = Field(default_factory=list)
    direction: Literal["outbound", "inbound", "both"] = "both"
    minimum_confidence: float = 0.0
    include_stale: bool = False
    entity_limit: int = 50
    relationship_limit: int = 100


def _evidence(items: list[EvidenceInput]) -> tuple[TopologyEvidence, ...]:
    return tuple(
        TopologyEvidence(
            item.id, item.kind, item.source, item.revision, item.properties
        )
        for item in items
    )


@router.put("/entities")
async def put_entity(
    payload: EntityInput,
    scope: Scope = Depends(get_scope),
    repository: TopologyRepository = Depends(get_topology_repository),
):
    """Create or replace one topology entity in the caller's workspace."""
    try:
        entity = TopologyEntity(
            payload.id,
            payload.kind,
            payload.status,
            payload.confidence,
            payload.stale,
            payload.properties,
            _evidence(payload.evidence),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    repository.put_entity(scope, entity)
    return success_response(asdict(entity))


@router.put("/relationships")
async def put_relationship(
    payload: RelationshipInput,
    scope: Scope = Depends(get_scope),
    repository: TopologyRepository = Depends(get_topology_repository),
):
    """Create or replace one relationship between entities in the same workspace."""
    try:
        relationship = TopologyRelationship(
            payload.id,
            payload.source_id,
            payload.target_id,
            payload.type,
            payload.status,
            payload.confidence,
            payload.stale,
            payload.properties,
            _evidence(payload.evidence),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    try:
        repository.put_relationship(scope, relationship)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    return success_response(asdict(relationship))


@router.post("/infer")
async def infer_relationships(
    payload: InferInput,
    user: dict = Depends(get_current_user),
    scope: Scope = Depends(get_scope),
    repository: TopologyRepository = Depends(get_topology_repository),
):
    """Propose dependencies between repositories from the manifests they publish.

    Every proposal is pending and carries the file it came from. Nothing is
    approved here: a person decides whether a proposed relationship is real.
    """
    github_token = user.get("github_token")
    if not github_token:
        raise HTTPException(status_code=400, detail="No GitHub token available")

    from src.api.routes.requirements import connected_names

    allowed = set(connected_names(user))
    if any(asset.repository not in allowed for asset in payload.assets):
        raise HTTPException(403, "Repository is outside this workspace")
    if payload.assets:
        existing = repository.search(
            TopologyQuery(
                scope=scope,
                ids=frozenset(asset.entity_id for asset in payload.assets),
                limit=100,
            )
        )
        mapped = {
            entity.id: entity.properties.get("name") for entity in existing.entities
        }
        if any(
            mapped.get(asset.entity_id) != asset.repository for asset in payload.assets
        ):
            raise HTTPException(422, "Assets must name repositories in this workspace")
    if not payload.assets:
        return success_response({"proposed": [], "read": 0})

    manifests = await read_manifests(
        [
            {"entity_id": a.entity_id, "repository": a.repository}
            for a in payload.assets
        ],
        github_token,
    )
    from dataclasses import replace

    systems = (
        {entity.id: entity.properties.get("system_id") for entity in existing.entities}
        if payload.assets
        else {}
    )
    proposals = tuple(
        replace(
            proposal,
            properties={
                **proposal.properties,
                "system_id": systems.get(proposal.source_id) or payload.system_id,
                "provenance": {
                    "evidence": [asdict(item) for item in proposal.evidence]
                },
            },
        )
        for proposal in infer_dependencies(manifests)
    )

    if payload.persist:
        for proposal in proposals:
            try:
                repository.put_relationship(scope, proposal)
            except ValueError as error:
                logger.warning(f"Could not record a proposed relationship: {error}")

    return success_response(
        {
            "proposed": [asdict(p) for p in proposals],
            "read": len(manifests),
            "persisted": payload.persist,
        }
    )


@router.delete("/entities/{entity_id:path}")
async def remove_entity(
    entity_id: str,
    scope: Scope = Depends(get_scope),
    repository: TopologyRepository = Depends(get_topology_repository),
):
    """Remove an entity and every relationship attached to it."""
    if not repository.remove_entity(scope, entity_id):
        raise HTTPException(status_code=404, detail="Entity not found")
    return success_response(None, message="Entity removed")


@router.delete("/relationships/{relationship_id:path}")
async def remove_relationship(
    relationship_id: str,
    scope: Scope = Depends(get_scope),
    repository: TopologyRepository = Depends(get_topology_repository),
):
    """Remove a single relationship, leaving both endpoints in place."""
    if not repository.remove_relationship(scope, relationship_id):
        raise HTTPException(status_code=404, detail="Relationship not found")
    return success_response(None, message="Relationship removed")


@router.post("/search")
async def search_entities(
    payload: SearchInput,
    scope: Scope = Depends(get_scope),
    repository: TopologyRepository = Depends(get_topology_repository),
):
    """List entities in the caller's workspace, with their relationships."""
    try:
        query = TopologyQuery(
            scope,
            ids=frozenset(payload.ids),
            kinds=frozenset(payload.kinds),
            statuses=frozenset(payload.statuses),
            properties=payload.properties,
            minimum_confidence=payload.minimum_confidence,
            include_stale=payload.include_stale,
            limit=payload.limit,
            offset=payload.offset,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    # The graph driver reports an unreachable store as a bare ValueError, so
    # these calls cannot share the validation handler above without reporting
    # an outage as a query the caller could have written differently.
    try:
        result = repository.search(query)
        relationships = repository.get_relationships(
            scope, frozenset(entity.id for entity in result.entities)
        )
    except ValueError:
        logger.exception("Topology store unreachable during search")
        raise HTTPException(status_code=503, detail=STORE_UNAVAILABLE)
    return success_response(
        {
            **asdict(result),
            "relationships": [asdict(item) for item in relationships],
        }
    )


@router.post("/traverse")
async def traverse(
    payload: TraversalInput,
    scope: Scope = Depends(get_scope),
    repository: TopologyRepository = Depends(get_topology_repository),
):
    """Walk the workspace graph outward from a bounded set of seed entities."""
    try:
        traversal = TopologyTraversal(
            scope,
            tuple(payload.entity_ids),
            depth=payload.depth,
            entity_kinds=frozenset(payload.entity_kinds),
            entity_statuses=frozenset(payload.entity_statuses),
            relationship_types=frozenset(payload.relationship_types),
            relationship_statuses=frozenset(payload.relationship_statuses),
            direction=payload.direction,
            minimum_confidence=payload.minimum_confidence,
            include_stale=payload.include_stale,
            entity_limit=payload.entity_limit,
            relationship_limit=payload.relationship_limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    try:
        result = repository.traverse(traversal)
    except ValueError:
        logger.exception("Topology store unreachable during traversal")
        raise HTTPException(status_code=503, detail=STORE_UNAVAILABLE)
    return success_response(asdict(result))


@router.get("/relationships/{relationship_id:path}")
async def read_relationship(
    relationship_id: str,
    scope: Scope = Depends(get_scope),
    repository: TopologyRepository = Depends(get_topology_repository),
):
    """One relationship by id, so a caller holding only an id need not walk.

    Without this a caller that knows an edge but not its endpoints has to read
    the graph around it and look, which costs the whole neighbourhood to answer
    a question about one identity.
    """
    # The graph driver reports an unreachable store as a bare ValueError, which
    # is why this reads as a validation error and is answered as an outage.
    try:
        relationship = repository.get_relationship(scope, relationship_id)
    except ValueError:
        logger.exception("Topology store unreachable while reading a relationship")
        raise HTTPException(status_code=503, detail=STORE_UNAVAILABLE)
    if relationship is None:
        raise HTTPException(status_code=404, detail="Relationship not found")
    return success_response(asdict(relationship))


@router.get("/systems/{system_id}/contents")
async def system_contents(
    system_id: str,
    scope: Scope = Depends(get_scope),
    repository: TopologyRepository = Depends(get_topology_repository),
):
    """Everything one system holds, however deep it is nested.

    Asked here rather than worked out by the caller, which otherwise reads the
    whole graph to answer a question about two identities and decides
    membership from data instead of from the thing that owns it.
    """
    try:
        held = contents(repository, scope, system_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    except Exception:
        logger.exception("Topology store unreachable while reading a system")
        raise HTTPException(status_code=503, detail=STORE_UNAVAILABLE)
    return success_response(asdict(held))


class BatchInput(BaseModel):
    operation_id: str = Field(min_length=1, max_length=255)
    entities: list[EntityInput] = Field(default_factory=list, max_length=200)
    relationships: list[RelationshipInput] = Field(default_factory=list, max_length=400)
    remove_relationships: list[str] = Field(default_factory=list, max_length=200)


@router.post("/batch")
def apply_batch(
    payload: BatchInput,
    scope: Scope = Depends(get_scope),
    repository=Depends(get_topology_repository),
):
    from src.core.topology.batch import TopologyBatch, TopologyBatchWriter

    if not isinstance(repository, TopologyBatchWriter):
        raise HTTPException(503, "Topology store does not support atomic writes")
    try:
        batch = TopologyBatch(
            payload.operation_id,
            tuple(
                TopologyEntity(
                    item.id,
                    item.kind,
                    item.status,
                    item.confidence,
                    item.stale,
                    item.properties,
                    _evidence(item.evidence),
                )
                for item in payload.entities
            ),
            tuple(
                TopologyRelationship(
                    item.id,
                    item.source_id,
                    item.target_id,
                    item.type,
                    item.status,
                    item.confidence,
                    item.stale,
                    item.properties,
                    _evidence(item.evidence),
                )
                for item in payload.relationships
            ),
            tuple(payload.remove_relationships),
        )
        repository.apply_batch(scope, batch)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return success_response({"operation_id": payload.operation_id, "completed": True})


class SuggestInput(BaseModel):
    repositories: list[str] = Field(default_factory=list, max_length=100)


@router.post("/suggest")
async def suggest(
    payload: SuggestInput,
    user: dict = Depends(get_current_user),
    scope: Scope = Depends(get_scope),
    repository=Depends(get_topology_repository),
):
    from dataclasses import replace
    from src.core.topology.suggestions import suggest_groups

    names = sorted(set(payload.repositories))
    from src.api.routes.requirements import connected_names

    if not set(names).issubset(connected_names(user)):
        raise HTTPException(403, "Repository is outside this workspace")
    token = user.get("github_token")
    if not token:
        raise HTTPException(400, "No GitHub token available")
    manifests = await read_manifests(
        [{"entity_id": name, "repository": name} for name in names], token
    )
    entities = []
    offset = 0
    while True:
        page = repository.search(TopologyQuery(scope=scope, limit=100, offset=offset))
        entities.extend(page.entities)
        if not page.has_more:
            break
        offset += 100
    mapping = {}
    for entity in entities:
        candidates = {
            item.source
            for item in entity.evidence
            if item.kind == "code_index" and item.source in names
        }
        if entity.kind == "repository" and entity.properties.get("name") in names:
            candidates.add(entity.properties["name"])
        if len(candidates) == 1:
            mapping[entity.id] = candidates.pop()
    edges = repository.get_relationships(scope, frozenset(mapping)) if mapping else ()
    graph_edges = tuple(
        replace(
            edge,
            source_id=mapping[edge.source_id],
            target_id=mapping[edge.target_id],
            properties={**edge.properties, "inferred_from": "code_graph"},
        )
        for edge in edges
        if edge.source_id in mapping
        and edge.target_id in mapping
        and mapping[edge.source_id] != mapping[edge.target_id]
        and edge.type != "contains"
        and not edge.stale
        and edge.evidence
        and (edge.properties.get("provenance") or {}).get("read_from") == "code_index"
    )
    # A read repository already has a system of its own, and joining those is
    # what building a bigger one means. Without this the caller has only names,
    # and makes a second, empty stand-in for something that already exists.
    systems = {
        entity.properties["name"]: entity.id
        for entity in entities
        if entity.kind == "system"
        and entity.properties.get("derived")
        and entity.properties.get("name") in names
    }
    return success_response(
        {
            "groups": suggest_groups(names, manifests, graph_edges, systems),
            "read": len(manifests),
        }
    )


class ReadInput(BaseModel):
    entity_id: str
    repository: str
    # Which systems the reading may name. Fixed before the model is asked, so a
    # reading cannot widen what it is allowed to join this repository to.
    targets: list[str] = Field(default_factory=list, max_length=50)
    revision: str = ""
    persist: bool = True


@router.post("/read")
async def read_connections(
    payload: ReadInput,
    user: dict = Depends(get_current_user),
    scope: Scope = Depends(get_scope),
    repository: TopologyRepository = Depends(get_topology_repository),
):
    """Propose connections a manifest cannot declare, by reading the code.

    Every proposal is pending and quotes the lines it was read from, and a
    quote those lines do not carry is dropped before anything is recorded.
    """
    token = user.get("github_token")
    if not token:
        raise HTTPException(400, "No GitHub token available")
    # requirements imports get_scope from this module, so binding this one
    # name at the top closes the cycle.
    from src.api.routes.requirements import connected_names

    if payload.repository not in set(connected_names(user)):
        raise HTTPException(403, "Repository is outside this workspace")

    targets = tuple(sorted({one for one in payload.targets if one}))
    known = (
        repository.search(
            TopologyQuery(
                scope=scope,
                ids=frozenset((payload.entity_id, *targets)),
                limit=100,
            )
        ).entities
        if targets
        else ()
    )
    named = {entity.id for entity in known}
    if payload.entity_id not in named:
        raise HTTPException(422, "That entity is not in this workspace")
    # A reading may only join what the graph already holds. A proposal naming
    # something nobody recorded has nothing to be checked against.
    targets = tuple(one for one in targets if one in named)
    if not targets:
        return success_response({"proposed": [], "read": payload.repository})

    # Named with the workspace as well as the repository, so what a reading
    # costs is answerable to whoever asked for it.
    provider = provider_for(
        Configuration(
            repository=payload.repository,
            workspace=scope.get("workspace"),
            user=str(user["user_id"]),
        )
    )
    if provider is None:
        raise HTTPException(400, "No model is configured to read with")

    def search(terms):
        try:
            searcher = service_registry.resolve(Searcher)
        except LookupError:
            return ()
        query = SearchQuery(
            Scope.from_mapping({"repository": payload.repository}), tuple(terms)
        )
        result = searcher.search(query)
        return [
            {
                "path": match.path,
                "start_line": match.start_line,
                "end_line": match.end_line,
                "text": match.text,
            }
            for match in result.matches
        ]

    def corroborates(source_id: str, target_id: str) -> bool:
        edges = repository.get_relationships(scope, frozenset({source_id, target_id}))
        return any(
            {edge.source_id, edge.target_id} == {source_id, target_id}
            and not edge.stale
            and (edge.properties.get("provenance") or {}).get("read_from")
            == "code_index"
            for edge in edges
        )

    about = "\n".join(
        f"- {entity.id}: {entity.properties.get('name') or entity.kind}"
        for entity in known
        if entity.id in targets
    )
    reads = WhatItReads(
        contents_reader(payload.repository, token, payload.revision),
        search,
        corroborates,
    )
    proposals = await asyncio.to_thread(
        reads.propose,
        provider,
        entity_id=payload.entity_id,
        repository=payload.repository,
        targets=targets,
        about=about,
        revision=payload.revision,
    )

    if payload.persist:
        for proposal in proposals:
            try:
                repository.put_relationship(scope, proposal)
            except ValueError as error:
                logger.warning(f"Could not record a read connection: {error}")

    return success_response(
        {
            "proposed": [asdict(one) for one in proposals],
            "read": payload.repository,
            "unavailable": reads.refused,
            "persisted": payload.persist,
        }
    )


class DiscoverInput(BaseModel):
    """A whole system's worth of reading, asked for at once."""

    assets: list[InferAssetInput] = Field(default_factory=list, max_length=100)
    system_id: str = ""
    persist: bool = True
    #: Read again even where the repository has not moved since the last one.
    refresh: bool = False


@router.post("/discover", status_code=202)
async def discover_connections(
    payload: DiscoverInput,
    user: dict = Depends(get_current_user),
    scope: Scope = Depends(get_scope),
    repository: TopologyRepository = Depends(get_topology_repository),
):
    """Ask for a system's connections, and answer before any of it is done.

    Reading a system of any size outlasts a request, so this queues the work
    and answers with the batch to watch. Everything a worker is not allowed to
    decide is decided here: whether these repositories are this user's to read,
    whether there is a model to read them with, and which of them have not
    changed since they were last read.
    """
    from src.api.routes.requirements import connected_names
    from src.core.topology.discovery import discover

    if not payload.assets:
        raise HTTPException(422, "Nothing was given to read")

    allowed = set(connected_names(user))
    if any(asset.repository not in allowed for asset in payload.assets):
        raise HTTPException(403, "Repository is outside this workspace")

    ids = frozenset(asset.entity_id for asset in payload.assets)
    known = repository.search(TopologyQuery(scope=scope, ids=ids, limit=100)).entities
    named = {entity.id: entity for entity in known}
    if any(asset.entity_id not in named for asset in payload.assets):
        raise HTTPException(422, "Assets must name repositories in this workspace")
    if any(
        named[asset.entity_id].properties.get("name") != asset.repository
        for asset in payload.assets
    ):
        raise HTTPException(422, "Assets must name repositories in this workspace")

    # Asked before anything is queued rather than found by each job in turn,
    # so a deployment with no usable model says so once.
    provider = provider_for(
        Configuration(
            repository=payload.assets[0].repository,
            workspace=scope.get("workspace"),
            user=str(user["user_id"]),
        )
    )
    if provider is None:
        raise HTTPException(400, "No model is configured to read with")
    missing = getattr(provider, "missing_credentials", lambda: [])()
    if missing:
        raise HTTPException(
            400, f"The model configured here has no {', '.join(missing)} to use"
        )

    # Off the event loop: queueing a discovery asks a forge where each
    # repository stands, and blocking here would stop every other request.
    asked = await asyncio.to_thread(
        discover,
        [
            {"entity_id": one.entity_id, "repository": one.repository}
            for one in payload.assets
        ],
        workspace=str(scope.get("workspace") or ""),
        system_id=payload.system_id,
        targets=[one.entity_id for one in payload.assets],
        about={
            entity.id: str(entity.properties.get("name") or entity.kind)
            for entity in known
        },
        persist=payload.persist,
        refresh=payload.refresh,
        user=str(user["user_id"]),
    )
    # The response builds its own, so the route's declared status is not what
    # a caller sees unless it is said here too.
    return success_response(asked, status_code=202)


@router.get("/discoveries/{batch_id}")
async def read_discovery(
    batch_id: int,
    user: dict = Depends(get_current_user),
    scope: Scope = Depends(get_scope),
):
    """How far a discovery has got, what it found, and what it could not read.

    Answered as the jobs stand rather than as a single done-or-not, because
    partial is the ordinary state of this and a reader watching it wants the
    repository still going named.
    """
    from src.core.jobs import job_store
    from src.core.topology.discovery import MANIFESTS, READING

    workspace = str(scope.get("workspace") or "")
    store = job_store()
    batch = store.read_batch(batch_id)
    # Batches are numbered across every customer of an instance, so an id alone
    # must not be enough to read one.
    if batch is None or batch.tenant != workspace:
        raise HTTPException(404, "No such discovery")

    jobs = store.in_batch(batch_id)
    return success_response(
        {
            "batch_id": batch.id,
            "total": batch.total,
            "done": batch.done,
            "pending": batch.pending,
            "failed": batch.failed,
            "finished": batch.finished,
            "manifests": [_discovered(job) for job in jobs if job.kind == MANIFESTS],
            "readings": [_discovered(job) for job in jobs if job.kind == READING],
        }
    )


def _discovered(job) -> dict:
    return {
        "job_id": job.id,
        "repository": job.payload.get("repository") or "",
        "state": job.state,
        "error": job.error or "",
    }
