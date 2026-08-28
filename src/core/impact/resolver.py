from __future__ import annotations

from src.core.topology import TopologyReader, TopologySubgraph, TopologyTraversal

from .interfaces import CompatibilityCheckReader, ImpactSeedResolver
from .models import (
    CompatibilityCheck,
    CompatibilityCheckQuery,
    ImpactFinding,
    ChangeImpact,
    ChangeImpactRequest,
)


class DefaultChangeImpactResolver:
    def __init__(
        self,
        *,
        seeds: ImpactSeedResolver,
        topology: TopologyReader,
        compatibility: CompatibilityCheckReader,
    ) -> None:
        self._seeds = seeds
        self._topology = topology
        self._compatibility = compatibility

    def resolve(self, request: ChangeImpactRequest) -> ChangeImpact:
        seed_ids = self._seeds.resolve(request.scope, request.changes)
        if not seed_ids:
            return ChangeImpact(TopologySubgraph((), (), False), (), (), False)
        seed_truncated = len(seed_ids) > request.entity_limit
        seed_ids = seed_ids[: request.entity_limit]
        topology = self._topology.traverse(
            TopologyTraversal(
                request.scope,
                seed_ids,
                depth=request.depth,
                relationship_statuses=frozenset({"approved"}),
                minimum_confidence=request.minimum_confidence,
                entity_limit=request.entity_limit,
                relationship_limit=request.relationship_limit,
            )
        )
        if not topology.entities:
            return ChangeImpact(topology, (), (), topology.truncated or seed_truncated)
        entity_ids = frozenset(entity.id for entity in topology.entities)
        evidence = self._compatibility.read(
            CompatibilityCheckQuery(
                scope=request.scope,
                entity_ids=entity_ids,
                statuses=frozenset({"approved"}),
                minimum_confidence=request.minimum_confidence,
                limit=request.entity_limit + 1,
            )
        )
        evidence_truncated = len(evidence) > request.entity_limit
        accepted = evidence[: request.entity_limit]
        findings = tuple(
            self._finding(request, item)
            for item in accepted
            if item.compatible is not True
        )
        return ChangeImpact(
            topology,
            accepted,
            findings,
            topology.truncated or seed_truncated or evidence_truncated,
        )

    @staticmethod
    def _finding(
        request: ChangeImpactRequest, evidence: CompatibilityCheck
    ) -> ImpactFinding:
        certain = evidence.compatible is False
        return ImpactFinding(
            id=f"compatibility:{evidence.id}",
            state="incompatible" if certain else "uncertain",
            summary=evidence.summary,
            changed_code_ids=tuple(change.id for change in request.changes),
            topology_entity_ids=(
                evidence.provider_entity_id,
                evidence.consumer_entity_id,
            ),
            compatibility_evidence_id=evidence.id,
            certain=certain,
            properties={
                "after_revision": evidence.after_revision,
                "before_revision": evidence.before_revision,
            },
        )
