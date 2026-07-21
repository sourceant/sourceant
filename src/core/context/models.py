from __future__ import annotations

from dataclasses import dataclass

from src.core.code_index import CodeTraversal, CodeTraversalResult
from src.core.contracts import ContractQuery, ContractResult
from src.core.knowledge import KnowledgeSubgraph, KnowledgeTraversal
from src.core.review_state import FindingQuery, FindingResult
from src.core.scope import Scope
from src.core.topology import TopologySubgraph, TopologyTraversal


@dataclass(frozen=True)
class ContextRequest:
    scope: Scope
    code: CodeTraversal | None = None
    knowledge: KnowledgeTraversal | None = None
    topology: TopologyTraversal | None = None
    contracts: ContractQuery | None = None
    findings: FindingQuery | None = None

    def __post_init__(self) -> None:
        queries = (
            self.code,
            self.knowledge,
            self.topology,
            self.contracts,
            self.findings,
        )
        if not any(query is not None for query in queries):
            raise ValueError("context request must select at least one context source")
        if any(query is not None and query.scope != self.scope for query in queries):
            raise ValueError("context queries must use the request scope")


@dataclass(frozen=True)
class ContextPack:
    scope: Scope
    code: CodeTraversalResult | None = None
    knowledge: KnowledgeSubgraph | None = None
    topology: TopologySubgraph | None = None
    contracts: ContractResult | None = None
    findings: FindingResult | None = None

    @property
    def truncated(self) -> bool:
        return any(
            result is not None and result.truncated
            for result in (self.code, self.knowledge, self.topology)
        ) or any(
            result is not None and result.has_more
            for result in (self.contracts, self.findings)
        )
