from __future__ import annotations

from src.core.code_index import CodeIndexReader
from src.core.contracts import ContractReader
from src.core.knowledge import KnowledgeReader
from src.core.review_state import ReviewStateReader
from src.core.topology import TopologyReader

from .models import ContextPack, ContextRequest


class DefaultContextProvider:
    def __init__(
        self,
        *,
        code: CodeIndexReader | None = None,
        knowledge: KnowledgeReader | None = None,
        topology: TopologyReader | None = None,
        contracts: ContractReader | None = None,
        review_state: ReviewStateReader | None = None,
    ) -> None:
        self._code = code
        self._knowledge = knowledge
        self._topology = topology
        self._contracts = contracts
        self._review_state = review_state

    def get_context(self, request: ContextRequest) -> ContextPack:
        return ContextPack(
            scope=request.scope,
            code=(
                self._require("code", self._code).traverse(request.code)
                if request.code
                else None
            ),
            knowledge=(
                self._require("knowledge", self._knowledge).traverse(request.knowledge)
                if request.knowledge
                else None
            ),
            topology=(
                self._require("topology", self._topology).traverse(request.topology)
                if request.topology
                else None
            ),
            contracts=(
                self._require("contracts", self._contracts).search(request.contracts)
                if request.contracts
                else None
            ),
            findings=(
                self._require("review state", self._review_state).search(
                    request.findings
                )
                if request.findings
                else None
            ),
        )

    @staticmethod
    def _require(name, reader):
        if reader is None:
            raise ValueError(f"{name} context is not configured")
        return reader
