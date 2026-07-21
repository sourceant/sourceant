from __future__ import annotations

from dataclasses import asdict
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.core.code_index import CodeTraversal
from src.core.context import ContextProvider, ContextRequest
from src.core.contracts import ContractQuery
from src.core.knowledge import (
    Knowledge,
    KnowledgeQuery,
    KnowledgeRelationship,
    KnowledgeRepository,
    KnowledgeTraversal,
)
from src.core.review_state import FindingQuery
from src.core.scope import Scope
from src.core.topology import TopologyTraversal


def create_mcp_server(
    provider: ContextProvider,
    *,
    knowledge: KnowledgeRepository | None = None,
) -> FastMCP:
    server = FastMCP(
        name="SourceAnt",
        instructions="Retrieve bounded engineering context before reviewing code.",
    )

    @server.tool(
        name="get_context",
        description=(
            "Retrieve a bounded context pack from configured code, knowledge, topology, "
            "contract, and review state sources."
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
        depth: int = 2,
        limit: int = 50,
    ) -> dict[str, Any]:
        if not 1 <= depth <= 3:
            raise ValueError("depth must be between 1 and 3")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        active_scope = Scope.from_mapping(scope)
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
        item = Knowledge(id, kind, status, summary, properties or {})
        repository.put(Scope.from_mapping(scope), item)
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
        repository.put_relationship(Scope.from_mapping(scope), relationship)
        return asdict(relationship)

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
                Scope.from_mapping(scope),
                ids=frozenset(ids or ()),
                kinds=frozenset(kinds or ()),
                statuses=frozenset(statuses or ()),
                properties=properties or {},
                limit=limit,
                offset=offset,
            )
        )
        return asdict(result)

    return server


def _require_knowledge(
    knowledge: KnowledgeRepository | None,
) -> KnowledgeRepository:
    if knowledge is None:
        raise ValueError("knowledge management is not configured")
    return knowledge
