import pytest

from src.core.review_impact import (
    ChangedCodeReference,
    CompatibilityEvidence,
    CompatibilityEvidenceQuery,
    CompatibilityEvidenceRepository,
    DefaultReviewImpactPreparer,
    ImpactFinding,
    ImpactSeedRepository,
    InMemoryCompatibilityEvidenceReader,
    InMemoryImpactSeedResolver,
    ReviewImpactRequest,
)
from src.core.scope import Scope
from src.core.topology import (
    InMemoryTopologyRepository,
    TopologyEntity,
    TopologyEvidence,
    TopologyRelationship,
)

PRODUCT = Scope.from_mapping({"boundary": "product"})
OTHER = Scope.from_mapping({"boundary": "other"})
CHANGE = ChangedCodeReference("checkout.create", "symbol", "provider-2", "api.py")
PROVENANCE = TopologyEvidence(
    "comparison-2", "contract_comparison", "openapi", "provider-2"
)


def build_preparer():
    seeds = InMemoryImpactSeedResolver()
    topology = InMemoryTopologyRepository()
    compatibility = InMemoryCompatibilityEvidenceReader()
    preparer = DefaultReviewImpactPreparer(
        seeds=seeds,
        topology=topology,
        compatibility=compatibility,
    )
    return preparer, seeds, topology, compatibility


def test_in_memory_repositories_implement_read_and_write_contracts():
    _, seeds, _, compatibility = build_preparer()

    assert isinstance(seeds, ImpactSeedRepository)
    assert isinstance(compatibility, CompatibilityEvidenceRepository)


def add_topology(seeds, topology, scope=PRODUCT):
    seeds.put_mapping(scope, CHANGE, ("provider",))
    topology.put_entity(scope, TopologyEntity("provider", "service", "active"))
    topology.put_entity(scope, TopologyEntity("consumer", "service", "active"))
    topology.put_relationship(
        scope,
        TopologyRelationship(
            "consumer-provider",
            "consumer",
            "provider",
            "depends_on",
            "approved",
            evidence=(PROVENANCE,),
        ),
    )


def evidence(**values):
    defaults = {
        "id": "comparison-2",
        "provider_entity_id": "provider",
        "consumer_entity_id": "consumer",
        "status": "approved",
        "compatible": False,
        "before_revision": "provider-1",
        "after_revision": "provider-2",
        "summary": "Required response field was removed",
        "evidence": (PROVENANCE,),
    }
    defaults.update(values)
    return CompatibilityEvidence(**defaults)


def test_prepares_deterministic_incompatible_impact_with_provenance():
    preparer, seeds, topology, compatibility = build_preparer()
    add_topology(seeds, topology)
    compatibility.put_evidence(PRODUCT, evidence())

    first = preparer.prepare(ReviewImpactRequest(PRODUCT, (CHANGE,)))
    second = preparer.prepare(ReviewImpactRequest(PRODUCT, (CHANGE,)))

    assert first == second
    assert tuple(entity.id for entity in first.topology.entities) == (
        "provider",
        "consumer",
    )
    assert first.findings[0].state == "incompatible"
    assert first.findings[0].certain is True
    assert first.findings[0].properties == {
        "after_revision": "provider-2",
        "before_revision": "provider-1",
    }


def test_keeps_uncertain_evidence_out_of_certain_findings():
    preparer, seeds, topology, compatibility = build_preparer()
    add_topology(seeds, topology)
    compatibility.put_evidence(PRODUCT, evidence(compatible=None))

    result = preparer.prepare(ReviewImpactRequest(PRODUCT, (CHANGE,)))

    assert result.findings[0].state == "uncertain"
    assert result.findings[0].certain is False


def test_ignores_compatible_stale_pending_and_weak_evidence():
    preparer, seeds, topology, compatibility = build_preparer()
    add_topology(seeds, topology)
    for item in (
        evidence(id="compatible", compatible=True),
        evidence(id="stale", stale=True),
        evidence(id="pending", status="pending"),
        evidence(id="weak", confidence=0.5),
    ):
        compatibility.put_evidence(PRODUCT, item)

    result = preparer.prepare(ReviewImpactRequest(PRODUCT, (CHANGE,)))

    assert tuple(item.id for item in result.compatibility) == ("compatible",)
    assert result.findings == ()


def test_filters_evidence_before_applying_the_result_limit():
    preparer, seeds, topology, compatibility = build_preparer()
    add_topology(seeds, topology)
    for item in (
        evidence(id="a-stale", stale=True),
        evidence(id="b-pending", status="pending"),
        evidence(id="c-weak", confidence=0.5),
        evidence(id="z-valid"),
    ):
        compatibility.put_evidence(PRODUCT, item)

    result = preparer.prepare(ReviewImpactRequest(PRODUCT, (CHANGE,), entity_limit=2))

    assert tuple(item.id for item in result.compatibility) == ("z-valid",)
    assert tuple(item.compatibility_evidence_id for item in result.findings) == (
        "z-valid",
    )


def test_preserves_scope_and_returns_empty_when_code_has_no_mapping():
    preparer, seeds, topology, compatibility = build_preparer()
    add_topology(seeds, topology, OTHER)
    compatibility.put_evidence(OTHER, evidence())

    result = preparer.prepare(ReviewImpactRequest(PRODUCT, (CHANGE,)))

    assert result.topology.entities == ()
    assert result.compatibility == ()
    assert result.findings == ()


def test_returns_empty_when_mapped_topology_entity_does_not_exist():
    preparer, seeds, _, _ = build_preparer()
    seeds.put_mapping(PRODUCT, CHANGE, ("missing",))

    result = preparer.prepare(ReviewImpactRequest(PRODUCT, (CHANGE,)))

    assert result.topology.entities == ()
    assert result.compatibility == ()
    assert result.findings == ()


def test_code_mapping_is_revision_specific():
    seeds = InMemoryImpactSeedResolver()
    seeds.put_mapping(PRODUCT, CHANGE, ("provider",))

    changed_revision = ChangedCodeReference(
        CHANGE.id, CHANGE.kind, "provider-3", CHANGE.path
    )

    assert seeds.resolve(PRODUCT, (changed_revision,)) == ()


def test_rejects_impact_findings_without_traceable_evidence():
    with pytest.raises(ValueError, match="must identify changed code"):
        ImpactFinding("finding", "incompatible", "Failure", (), (), "", True)


def test_compatibility_query_accepts_limits_independent_of_review_defaults():
    query = CompatibilityEvidenceQuery(PRODUCT, frozenset({"provider"}), limit=500)

    assert query.limit == 500


def test_compatibility_query_requires_a_positive_limit():
    with pytest.raises(ValueError, match="limit must be positive"):
        CompatibilityEvidenceQuery(PRODUCT, frozenset({"provider"}), limit=0)
