from __future__ import annotations

from dataclasses import asdict
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.core.code_index import CodeTraversal
from src.core.context import ContextProvider, ContextRequest
from src.core.contracts import ContractQuery
from src.core.knowledge import KnowledgeTraversal
from src.core.review_state import FindingQuery
from src.core.scope import Scope
from src.core.topology import TopologyTraversal


def create_mcp_server(provider: ContextProvider) -> FastMCP:
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

    return server
