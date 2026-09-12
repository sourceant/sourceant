import pytest

from src.core.impact import (
    ChangedCodeReference,
    CompatibilityCheck,
    CompatibilityCheckQuery,
    CompatibilityCheckRepository,
    DefaultChangeImpactResolver,
    FirstAnsweringSeedResolver,
    ImpactFinding,
    ImpactSeedRepository,
    InMemoryCompatibilityCheckReader,
    InMemoryImpactSeedResolver,
    ChangeImpactRequest,
    TopologyPrefixSeedResolver,
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
    compatibility = InMemoryCompatibilityCheckReader()
    preparer = DefaultChangeImpactResolver(
        seeds=seeds,
        topology=topology,
        compatibility=compatibility,
    )
    return preparer, seeds, topology, compatibility


def test_in_memory_repositories_implement_read_and_write_contracts():
    _, seeds, _, compatibility = build_preparer()

    assert isinstance(seeds, ImpactSeedRepository)
    assert isinstance(compatibility, CompatibilityCheckRepository)


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
    return CompatibilityCheck(**defaults)


def test_prepares_deterministic_incompatible_impact_with_provenance():
    preparer, seeds, topology, compatibility = build_preparer()
    add_topology(seeds, topology)
    compatibility.put_evidence(PRODUCT, evidence())

    first = preparer.resolve(ChangeImpactRequest(PRODUCT, (CHANGE,)))
    second = preparer.resolve(ChangeImpactRequest(PRODUCT, (CHANGE,)))

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

    result = preparer.resolve(ChangeImpactRequest(PRODUCT, (CHANGE,)))

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

    result = preparer.resolve(ChangeImpactRequest(PRODUCT, (CHANGE,)))

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

    result = preparer.resolve(ChangeImpactRequest(PRODUCT, (CHANGE,), entity_limit=2))

    assert tuple(item.id for item in result.compatibility) == ("z-valid",)
    assert tuple(item.compatibility_evidence_id for item in result.findings) == (
        "z-valid",
    )


def test_preserves_scope_and_returns_empty_when_code_has_no_mapping():
    preparer, seeds, topology, compatibility = build_preparer()
    add_topology(seeds, topology, OTHER)
    compatibility.put_evidence(OTHER, evidence())

    result = preparer.resolve(ChangeImpactRequest(PRODUCT, (CHANGE,)))

    assert result.topology.entities == ()
    assert result.compatibility == ()
    assert result.findings == ()


def test_returns_empty_when_mapped_topology_entity_does_not_exist():
    preparer, seeds, _, _ = build_preparer()
    seeds.put_mapping(PRODUCT, CHANGE, ("missing",))

    result = preparer.resolve(ChangeImpactRequest(PRODUCT, (CHANGE,)))

    assert result.topology.entities == ()
    assert result.compatibility == ()
    assert result.findings == ()


def test_a_mapping_answers_a_review_of_a_later_commit():
    """The two sides never share a commit.

    A mapping is written while a repository is read and asked for while a
    pull request is reviewed, which is always a different commit. Keyed on
    one, a review started nowhere and reached nothing.
    """
    seeds = InMemoryImpactSeedResolver()
    seeds.put_mapping(PRODUCT, CHANGE, ("provider",))

    later = ChangedCodeReference(CHANGE.id, CHANGE.kind, "provider-3", CHANGE.path)

    assert seeds.resolve(PRODUCT, (later,)) == ("provider",)


def test_a_mapping_answers_however_the_kind_is_spelled():
    """A reading says "File" and a review says "file"."""
    seeds = InMemoryImpactSeedResolver()
    seeds.put_mapping(
        PRODUCT,
        ChangedCodeReference("api.py", "File", "provider-2", "api.py"),
        ("provider",),
    )

    asked = ChangedCodeReference("api.py", "file", "provider-9", "api.py")

    assert seeds.resolve(PRODUCT, (asked,)) == ("provider",)


def test_the_same_path_in_two_repositories_is_two_mappings():
    """A system holds several repositories and two can hold one path.

    Keyed on the path alone, reading the second replaces the first and a
    review of that file starts from whichever was read last.
    """
    seeds = InMemoryImpactSeedResolver()
    seeds.put_mapping(
        PRODUCT,
        ChangedCodeReference("api.py", "file", "r1", "api.py", repository="acme/one"),
        ("one",),
    )
    seeds.put_mapping(
        PRODUCT,
        ChangedCodeReference("api.py", "file", "r1", "api.py", repository="acme/two"),
        ("two",),
    )

    asked = ChangedCodeReference(
        "api.py", "file", "later", "api.py", repository="acme/one"
    )

    assert seeds.resolve(PRODUCT, (asked,)) == ("one",)


def test_rejects_impact_findings_without_traceable_evidence():
    with pytest.raises(ValueError, match="must identify changed code"):
        ImpactFinding("finding", "incompatible", "Failure", (), (), "", True)


def test_compatibility_query_accepts_limits_independent_of_review_defaults():
    query = CompatibilityCheckQuery(PRODUCT, frozenset({"provider"}), limit=500)

    assert query.limit == 500


def test_compatibility_query_requires_a_positive_limit():
    with pytest.raises(ValueError, match="limit must be positive"):
        CompatibilityCheckQuery(PRODUCT, frozenset({"provider"}), limit=0)


def test_reaches_topology_that_was_derived_rather_than_declared():
    """A graph read out of a repository is never certain, and must still be walked."""
    preparer, seeds, topology, _ = build_preparer()
    seeds.put_mapping(PRODUCT, CHANGE, ("provider",))
    topology.put_entity(
        PRODUCT, TopologyEntity("provider", "component", "approved", confidence=0.95)
    )
    topology.put_entity(
        PRODUCT, TopologyEntity("consumer", "system", "pending", confidence=0.6)
    )
    topology.put_relationship(
        PRODUCT,
        TopologyRelationship(
            "consumer-provider",
            "consumer",
            "provider",
            "depends_on",
            "approved",
            confidence=0.6,
            evidence=(PROVENANCE,),
        ),
    )

    impact = preparer.resolve(ChangeImpactRequest(PRODUCT, (CHANGE,)))

    assert tuple(entity.id for entity in impact.topology.entities) == (
        "provider",
        "consumer",
    )


def test_how_sure_the_graph_is_and_how_sure_a_finding_is_are_asked_separately():
    preparer, seeds, topology, compatibility = build_preparer()
    add_topology(seeds, topology)
    compatibility.put_evidence(PRODUCT, evidence(id="weak", confidence=0.4))

    impact = preparer.resolve(
        ChangeImpactRequest(PRODUCT, (CHANGE,), minimum_reach_confidence=0.0)
    )

    assert tuple(entity.id for entity in impact.topology.entities) == (
        "provider",
        "consumer",
    )
    assert impact.compatibility == ()


class TestStartingTheWalkWithNothingRecorded:
    """A repository connected and not yet read still has to reach its system.

    Nothing in core writes a seed mapping, and nothing has read a repository
    somebody connected this morning. Both cases used to end the same way: no
    seed, no walk, and a review that reached nothing while the graph sat
    there holding the answer.
    """

    def graph(self):
        topology = InMemoryTopologyRepository()
        topology.put_entity(
            PRODUCT,
            TopologyEntity(
                "system:acme/billing",
                "system",
                "proposed",
                properties={"name": "acme/billing"},
            ),
        )
        topology.put_entity(
            PRODUCT,
            TopologyEntity(
                "component:billing:src",
                "component",
                "proposed",
                properties={"system_id": "system:acme/billing", "external_id": "src"},
            ),
        )
        topology.put_entity(
            PRODUCT,
            TopologyEntity(
                "component:billing:src/core",
                "component",
                "proposed",
                properties={
                    "system_id": "system:acme/billing",
                    "external_id": "src/core",
                },
            ),
        )
        return topology

    def changed(self, path):
        return ChangedCodeReference(
            f"file:{path}", "file", "head", path, repository="acme/billing"
        )

    def test_the_longest_folder_holding_a_file_is_where_it_starts(self):
        seeds = TopologyPrefixSeedResolver(self.graph())

        assert seeds.resolve(PRODUCT, (self.changed("src/core/jobs/sql.py"),)) == (
            "component:billing:src/core",
        )

    def test_a_file_no_part_claims_starts_at_the_system(self):
        """A change reaches whatever its repository reaches.

        Which folder it happens to sit in decides where the walk is most
        precise, never whether it happens at all.
        """
        seeds = TopologyPrefixSeedResolver(self.graph())

        assert seeds.resolve(PRODUCT, (self.changed("README.md"),)) == (
            "system:acme/billing",
        )

    def test_a_repository_the_graph_has_never_heard_of_starts_nowhere(self):
        seeds = TopologyPrefixSeedResolver(self.graph())
        elsewhere = ChangedCodeReference(
            "file:a.py", "file", "head", "a.py", repository="acme/unknown"
        )

        assert seeds.resolve(PRODUCT, (elsewhere,)) == ()

    def test_a_recorded_mapping_is_preferred_to_a_folder_name(self):
        """A mapping was written by something that read the repository."""
        recorded = InMemoryImpactSeedResolver()
        change = self.changed("src/core/jobs/sql.py")
        recorded.put_mapping(PRODUCT, change, ("component:billing:jobs",))
        seeds = FirstAnsweringSeedResolver(
            recorded, TopologyPrefixSeedResolver(self.graph())
        )

        assert seeds.resolve(PRODUCT, (change,)) == ("component:billing:jobs",)

    def test_the_graph_answers_when_nothing_was_recorded(self):
        seeds = FirstAnsweringSeedResolver(
            InMemoryImpactSeedResolver(), TopologyPrefixSeedResolver(self.graph())
        )

        assert seeds.resolve(PRODUCT, (self.changed("src/core/jobs/sql.py"),)) == (
            "component:billing:src/core",
        )


class TestCrossingALinkNobodyHasApproved:
    """Inference writes every link it proposes as pending.

    Approving one is a person's decision and usually a later one. Crossing
    only approved links meant a system connected last week reached nothing,
    which is every system when it is new.
    """

    def build(self, status):
        seeds = InMemoryImpactSeedResolver()
        topology = InMemoryTopologyRepository()
        seeds.put_mapping(PRODUCT, CHANGE, ("provider",))
        topology.put_entity(PRODUCT, TopologyEntity("provider", "system", "approved"))
        topology.put_entity(PRODUCT, TopologyEntity("consumer", "system", "approved"))
        topology.put_relationship(
            PRODUCT,
            TopologyRelationship(
                "consumer-provider", "consumer", "provider", "depends_on", status
            ),
        )
        return DefaultChangeImpactResolver(
            seeds=seeds,
            topology=topology,
            compatibility=InMemoryCompatibilityCheckReader(),
        )

    def test_a_pending_link_is_walked(self):
        result = self.build("pending").resolve(ChangeImpactRequest(PRODUCT, (CHANGE,)))

        assert {entity.id for entity in result.topology.entities} == {
            "provider",
            "consumer",
        }

    def test_a_rejected_link_is_not(self):
        result = self.build("rejected").resolve(ChangeImpactRequest(PRODUCT, (CHANGE,)))

        assert {entity.id for entity in result.topology.entities} == {"provider"}


class TestAWalkThatNeverStarted:
    """Nothing recorded where the changed files sit, so there was nowhere to
    begin. That reaches nothing, and so does a walk across the whole graph
    that finds nothing, and only one of them says anything about the change."""

    def test_it_is_not_reported_as_reaching_nothing(self):
        preparer, _, topology, _ = build_preparer()
        topology.put_entity(PRODUCT, TopologyEntity("provider", "system", "approved"))

        result = preparer.resolve(ChangeImpactRequest(PRODUCT, (CHANGE,)))

        assert not result.seeded
        assert result.topology.entities == ()

    def test_a_walk_that_started_and_found_nothing_is(self):
        preparer, seeds, topology, _ = build_preparer()
        seeds.put_mapping(PRODUCT, CHANGE, ("provider",))
        topology.put_entity(PRODUCT, TopologyEntity("provider", "system", "approved"))

        result = preparer.resolve(ChangeImpactRequest(PRODUCT, (CHANGE,)))

        assert result.seeded
        assert {entity.id for entity in result.topology.entities} == {"provider"}
