from __future__ import annotations

from src.core.topology import TopologyReader, TopologySubgraph, TopologyTraversal

from .interfaces import CompatibilityEvidenceReader, ImpactSeedResolver
from .models import ImpactFinding, ReviewImpact, ReviewImpactRequest


class DefaultReviewImpactPreparer:
    def __init__(
        self,
        *,
        seeds: ImpactSeedResolver,
        topology: TopologyReader,
        compatibility: CompatibilityEvidenceReader,
    ) -> None:
        self._seeds = seeds
        self._topology = topology
        self._compatibility = compatibility

    def prepare(self, request: ReviewImpactRequest) -> ReviewImpact:
        seed_ids = self._seeds.resolve(request.scope, request.changes)
        if not seed_ids:
            return ReviewImpact(TopologySubgraph((), (), False), (), (), False)
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
        entity_ids = frozenset(entity.id for entity in topology.entities)
        evidence = self._compatibility.read(
            request.scope, entity_ids, request.entity_limit + 1
        )
        evidence_truncated = len(evidence) > request.entity_limit
        evidence = evidence[: request.entity_limit]
        accepted = tuple(
            item
            for item in evidence
            if not item.stale
            and item.status == "approved"
            and item.confidence >= request.minimum_confidence
        )
        findings = tuple(
            self._finding(request, item)
            for item in accepted
            if item.compatible is not True
        )
        return ReviewImpact(
            topology,
            accepted,
            findings,
            topology.truncated or seed_truncated or evidence_truncated,
        )

    @staticmethod
    def _finding(request, evidence):
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
