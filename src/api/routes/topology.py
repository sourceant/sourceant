from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.auth import get_current_user
from src.config.db import get_engine
from src.core.responses import success_response
from src.core.scope import Scope
from src.core.services import service_registry
from src.core.topology.inference import infer_dependencies
from src.core.topology.manifests import read_manifests
from src.utils.logger import logger
from src.core.topology import (
    InMemoryTopologyRepository,
    SQLTopologyRepository,
    TopologyEntity,
    TopologyEvidence,
    TopologyQuery,
    TopologyRelationship,
    TopologyRepository,
    TopologyTraversal,
)

router = APIRouter()

STORE_UNAVAILABLE = "The topology store is unavailable"

_fallback: TopologyRepository | None = None


def get_topology_repository() -> TopologyRepository:
    """The plugin-provided repository when one is registered, else core's own store.

    ``ServiceRegistry.register`` allows a single provider per interface, so core
    cannot pre-register alongside a plugin. Resolution has to happen per request.
    """
    global _fallback
    try:
        return service_registry.resolve(TopologyRepository)
    except LookupError:
        pass
    if _fallback is None:
        engine = get_engine()
        _fallback = (
            SQLTopologyRepository(engine)
            if engine is not None
            else InMemoryTopologyRepository()
        )
    return _fallback


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
    from src.api.routes.requirements import connected_names
    from src.core.topology.suggestions import suggest_groups

    names = sorted(set(payload.repositories))
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
