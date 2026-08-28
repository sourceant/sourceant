from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

from src.core.code_index import CodeIndexReader, CodeSearch, CodeTraversal
from src.core.context import ContextProvider, ContextRequest
from src.core.contracts import ContractQuery
from src.core.knowledge import (
    KnowledgeLink,
    KnowledgeLinkWriter,
    KnowledgeObject,
    KnowledgeQuery,
    KnowledgeRelationship,
    KnowledgeRepository,
    KnowledgeTraversal,
)
from src.core.requirements import (
    CoverageQuery,
    Requirement,
    RequirementLink,
    RequirementQuery,
    RequirementsRepository,
)
from src.core.review_state import FindingQuery
from src.core.scope import Scope
from src.core.topology import (
    TopologyEntity,
    TopologyEvidence,
    TopologyRelationship,
    TopologyRepository,
    TopologyTraversal,
)


def create_mcp_server(
    provider: ContextProvider,
    *,
    code: CodeIndexReader | None = None,
    knowledge: KnowledgeRepository | None = None,
    topology: TopologyRepository | None = None,
    requirements: RequirementsRepository | None = None,
    scope_resolver: Callable[[Scope], Scope] | None = None,
    auth: AuthSettings | None = None,
    token_verifier: TokenVerifier | None = None,
) -> FastMCP:
    server = FastMCP(
        name="SourceAnt",
        instructions=(
            "An indexed graph of this codebase and the engineering knowledge "
            "recorded against it. Search and traverse code structure, read and "
            "write decisions, rules, constraints, conventions, API contracts, "
            "and system topology, and combine any of them into one bounded "
            "context pack. Write what you learn back so it outlives this "
            "session."
        ),
        auth=auth,
        token_verifier=token_verifier,
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
    )
    resolve_scope = scope_resolver or (lambda scope: scope)

    @server.tool(
        name="search_code",
        description="Search bounded structural code nodes by labels and exact properties.",
        structured_output=True,
    )
    def search_code(
        scope: dict[str, str],
        labels: list[str] | None = None,
        properties: dict[str, Any] | None = None,
        node_ids: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        result = _require_code(code).search(
            CodeSearch(
                resolve_scope(Scope.from_mapping(scope)),
                labels=frozenset(labels or ()),
                properties=properties or {},
                node_ids=frozenset(node_ids or ()),
                limit=limit,
                offset=offset,
            )
        )
        return asdict(result)

    @server.tool(
        name="trace_code",
        description="Traverse a bounded structural code neighborhood and its references.",
        structured_output=True,
    )
    def trace_code(
        scope: dict[str, str],
        node_ids: list[str],
        depth: int = 2,
        edge_types: list[str] | None = None,
        direction: str = "both",
        limit: int = 50,
    ) -> dict[str, Any]:
        if not 1 <= depth <= 3:
            raise ValueError("depth must be between 1 and 3")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        result = _require_code(code).traverse(
            CodeTraversal(
                resolve_scope(Scope.from_mapping(scope)),
                tuple(node_ids),
                depth=depth,
                edge_types=frozenset(edge_types or ()),
                direction=direction,
                node_limit=limit,
            )
        )
        return asdict(result)

    @server.tool(
        name="get_context",
        description=(
            "Retrieve a bounded context pack from configured code, knowledge, "
            "topology, contract, requirement, and review state sources."
        ),
        structured_output=True,
    )
    def get_context(
        scope: dict[str, str],
        code_node_ids: list[str] | None = None,
        knowledge_ids: list[str] | None = None,
        topology_entity_ids: list[str] | None = None,
        contract_document_ids: list[str] | None = None,
        finding_states: list[str] | None = None,
        requirement_ids: list[str] | None = None,
        depth: int = 2,
        limit: int = 50,
    ) -> dict[str, Any]:
        if not 1 <= depth <= 3:
            raise ValueError("depth must be between 1 and 3")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        active_scope = resolve_scope(Scope.from_mapping(scope))
        request = ContextRequest(
            scope=active_scope,
            code=(
                CodeTraversal(
                    active_scope,
                    tuple(code_node_ids),
                    depth=depth,
                    node_limit=limit,
                )
                if code_node_ids
                else None
            ),
            knowledge=(
                KnowledgeTraversal(
                    active_scope,
                    tuple(knowledge_ids),
                    depth=depth,
                    knowledge_limit=limit,
                )
                if knowledge_ids
                else None
            ),
            topology=(
                TopologyTraversal(
                    active_scope,
                    tuple(topology_entity_ids),
                    depth=depth,
                    entity_limit=limit,
                )
                if topology_entity_ids
                else None
            ),
            contracts=(
                ContractQuery(
                    active_scope,
                    document_ids=frozenset(contract_document_ids),
                    limit=limit,
                )
                if contract_document_ids
                else None
            ),
            findings=(
                FindingQuery(
                    active_scope,
                    states=frozenset(finding_states),
                    limit=limit,
                )
                if finding_states
                else None
            ),
            requirements=(
                RequirementQuery(
                    active_scope,
                    ids=frozenset(requirement_ids),
                    limit=limit,
                )
                if requirement_ids
                else None
            ),
        )
        result = provider.get_context(request)
        pack = asdict(result)
        pack["scope"] = dict(active_scope.values)
        pack["truncated"] = result.truncated
        return pack

    @server.tool(
        name="put_knowledge",
        description="Create or update a scoped engineering knowledge item.",
        structured_output=True,
    )
    def put_knowledge(
        scope: dict[str, str],
        id: str,
        kind: str,
        status: str,
        summary: str,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        repository = _require_knowledge(knowledge)
        item = KnowledgeObject(id, kind, status, summary, properties or {})
        repository.put(resolve_scope(Scope.from_mapping(scope)), item)
        return asdict(item)

    @server.tool(
        name="put_knowledge_relationship",
        description="Create or update a relationship between scoped knowledge items.",
        structured_output=True,
    )
    def put_knowledge_relationship(
        scope: dict[str, str],
        id: str,
        source_id: str,
        target_id: str,
        type: str,
        status: str = "",
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        repository = _require_knowledge(knowledge)
        relationship = KnowledgeRelationship(
            id,
            source_id,
            target_id,
            type,
            status,
            properties or {},
        )
        repository.put_relationship(
            resolve_scope(Scope.from_mapping(scope)), relationship
        )
        return asdict(relationship)

    @server.tool(
        name="link_knowledge",
        description=(
            "Attach a knowledge object to the code, test, or system it governs, "
            "so a change to those files is judged against it."
        ),
        structured_output=True,
    )
    def link_knowledge(
        scope: dict[str, str],
        id: str,
        knowledge_id: str,
        target_kind: str,
        target_id: str,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        repository = _require_knowledge(knowledge)
        if not isinstance(repository, KnowledgeLinkWriter):
            raise ValueError("the configured knowledge store does not hold links")
        link = KnowledgeLink(id, knowledge_id, target_kind, target_id, properties or {})
        repository.put_link(resolve_scope(Scope.from_mapping(scope)), link)
        return asdict(link)

    @server.tool(
        name="search_knowledge",
        description="Search scoped engineering knowledge by identity and lifecycle.",
        structured_output=True,
    )
    def search_knowledge(
        scope: dict[str, str],
        ids: list[str] | None = None,
        kinds: list[str] | None = None,
        statuses: list[str] | None = None,
        properties: dict[str, Any] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        repository = _require_knowledge(knowledge)
        result = repository.search(
            KnowledgeQuery(
                resolve_scope(Scope.from_mapping(scope)),
                ids=frozenset(ids or ()),
                kinds=frozenset(kinds or ()),
                statuses=frozenset(statuses or ()),
                properties=properties or {},
                limit=limit,
                offset=offset,
            )
        )
        return asdict(result)

    @server.tool(
        name="put_topology_entity",
        description="Create or update a scoped software topology entity.",
        structured_output=True,
    )
    def put_topology_entity(
        scope: dict[str, str],
        id: str,
        kind: str,
        status: str,
        confidence: float = 1.0,
        stale: bool = False,
        properties: dict[str, Any] | None = None,
        evidence: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        repository = _require_topology(topology)
        entity = TopologyEntity(
            id,
            kind,
            status,
            confidence,
            stale,
            properties or {},
            _build_evidence(evidence),
        )
        repository.put_entity(resolve_scope(Scope.from_mapping(scope)), entity)
        return asdict(entity)

    @server.tool(
        name="put_topology_relationship",
        description="Create or update a relationship between scoped topology entities.",
        structured_output=True,
    )
    def put_topology_relationship(
        scope: dict[str, str],
        id: str,
        source_id: str,
        target_id: str,
        type: str,
        status: str,
        confidence: float = 1.0,
        stale: bool = False,
        properties: dict[str, Any] | None = None,
        evidence: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        repository = _require_topology(topology)
        relationship = TopologyRelationship(
            id,
            source_id,
            target_id,
            type,
            status,
            confidence,
            stale,
            properties or {},
            _build_evidence(evidence),
        )
        repository.put_relationship(
            resolve_scope(Scope.from_mapping(scope)), relationship
        )
        return asdict(relationship)

    @server.tool(
        name="traverse_topology",
        description="Traverse scoped software topology from a set of seed entities.",
        structured_output=True,
    )
    def traverse_topology(
        scope: dict[str, str],
        entity_ids: list[str],
        depth: int = 2,
        entity_kinds: list[str] | None = None,
        entity_statuses: list[str] | None = None,
        relationship_types: list[str] | None = None,
        relationship_statuses: list[str] | None = None,
        direction: str = "both",
        minimum_confidence: float = 0.0,
        include_stale: bool = False,
        entity_limit: int = 50,
        relationship_limit: int = 100,
    ) -> dict[str, Any]:
        repository = _require_topology(topology)
        result = repository.traverse(
            TopologyTraversal(
                resolve_scope(Scope.from_mapping(scope)),
                tuple(entity_ids),
                depth=depth,
                entity_kinds=frozenset(entity_kinds or ()),
                entity_statuses=frozenset(entity_statuses or ()),
                relationship_types=frozenset(relationship_types or ()),
                relationship_statuses=frozenset(relationship_statuses or ()),
                direction=direction,
                minimum_confidence=minimum_confidence,
                include_stale=include_stale,
                entity_limit=entity_limit,
                relationship_limit=relationship_limit,
            )
        )
        return asdict(result)

    @server.tool(
        name="put_requirement",
        description=(
            "Create or update a scoped requirement. Also stored as knowledge of "
            "kind requirement, so the knowledge tools find it too."
        ),
        structured_output=True,
    )
    def put_requirement(
        scope: dict[str, str],
        id: str,
        summary: str,
        kind: str = "requirement",
        status: str = "open",
        external_ref: str = "",
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        repository = _require_requirements(requirements)
        item = Requirement(id, kind, status, summary, external_ref, properties or {})
        repository.put(resolve_scope(Scope.from_mapping(scope)), item)
        return asdict(item)

    @server.tool(
        name="link_requirement",
        description=(
            "Point a requirement at the code, test, knowledge, or topology that "
            "carries it."
        ),
        structured_output=True,
    )
    def link_requirement(
        scope: dict[str, str],
        id: str,
        requirement_id: str,
        target_kind: str,
        target_id: str,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        repository = _require_requirements(requirements)
        link = RequirementLink(
            id, requirement_id, target_kind, target_id, properties or {}
        )
        repository.put_link(resolve_scope(Scope.from_mapping(scope)), link)
        return asdict(link)

    @server.tool(
        name="search_requirements",
        description="Search scoped requirements by identity, kind, status, or origin.",
        structured_output=True,
    )
    def search_requirements(
        scope: dict[str, str],
        ids: list[str] | None = None,
        kinds: list[str] | None = None,
        statuses: list[str] | None = None,
        external_refs: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        repository = _require_requirements(requirements)
        result = repository.search(
            RequirementQuery(
                scope=resolve_scope(Scope.from_mapping(scope)),
                ids=frozenset(ids or ()),
                kinds=frozenset(kinds or ()),
                statuses=frozenset(statuses or ()),
                external_refs=frozenset(external_refs or ()),
                limit=limit,
                offset=offset,
            )
        )
        return asdict(result)

    @server.tool(
        name="get_requirement_coverage",
        description=(
            "Which requirements have code, which have tests, and which of them "
            "a set of changed files touches."
        ),
        structured_output=True,
    )
    def get_requirement_coverage(
        scope: dict[str, str],
        requirement_ids: list[str] | None = None,
        paths: list[str] | None = None,
    ) -> dict[str, Any]:
        repository = _require_requirements(requirements)
        report = repository.coverage(
            CoverageQuery(
                scope=resolve_scope(Scope.from_mapping(scope)),
                requirement_ids=frozenset(requirement_ids or ()),
                paths=frozenset(paths or ()),
            )
        )
        return {
            "items": [asdict(item) for item in report.items],
            "uncovered": list(report.uncovered),
            "untested": list(report.untested),
            "truncated": report.truncated,
        }

    return server


def _require_requirements(
    requirements: RequirementsRepository | None,
) -> RequirementsRepository:
    if requirements is None:
        raise ValueError("requirements are not configured")
    return requirements


def _require_knowledge(
    knowledge: KnowledgeRepository | None,
) -> KnowledgeRepository:
    if knowledge is None:
        raise ValueError("knowledge management is not configured")
    return knowledge


def _require_code(code: CodeIndexReader | None) -> CodeIndexReader:
    if code is None:
        raise ValueError("code discovery is not configured")
    return code


def _require_topology(
    topology: TopologyRepository | None,
) -> TopologyRepository:
    if topology is None:
        raise ValueError("topology management is not configured")
    return topology


def _build_evidence(
    evidence: list[dict[str, Any]] | None,
) -> tuple[TopologyEvidence, ...]:
    return tuple(
        TopologyEvidence(
            item["id"],
            item["kind"],
            item["source"],
            item.get("revision", ""),
            item.get("properties", {}),
        )
        for item in evidence or ()
    )
