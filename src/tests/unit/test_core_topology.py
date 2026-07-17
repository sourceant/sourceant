import pytest

from src.core.scope import Scope
from src.core.topology import (
    InMemoryTopologyRepository,
    TopologyEntity,
    TopologyEvidence,
    TopologyRelationship,
    TopologyTraversal,
)

PRODUCT = Scope.from_mapping({"boundary": "product"})
OTHER_PRODUCT = Scope.from_mapping({"boundary": "other"})


def entity(identifier: str, kind: str = "component", **values) -> TopologyEntity:
    return TopologyEntity(identifier, kind, "active", **values)


def test_topology_traversal_is_bounded_filtered_and_scope_isolated():
    topology = InMemoryTopologyRepository()
    for scope in (PRODUCT, OTHER_PRODUCT):
        for item in (
            entity("frontend"),
            entity("api"),
            entity("contract", "interface"),
            entity("release", "release"),
        ):
            topology.put_entity(scope, item)
    topology.put_relationship(
        PRODUCT,
        TopologyRelationship(
            "frontend-contract",
            "frontend",
            "contract",
            "consumes",
            "approved",
        ),
    )
    topology.put_relationship(
        PRODUCT,
        TopologyRelationship("api-contract", "api", "contract", "provides", "approved"),
    )
    topology.put_relationship(
        PRODUCT,
        TopologyRelationship("api-release", "api", "release", "released_as", "pending"),
    )

    result = topology.traverse(
        TopologyTraversal(
            PRODUCT,
            ("frontend",),
            depth=2,
            relationship_types=frozenset({"consumes", "provides"}),
            relationship_statuses=frozenset({"approved"}),
        )
    )

    assert [item.id for item in result.entities] == [
        "frontend",
        "contract",
        "api",
    ]
    assert [relationship.id for relationship in result.relationships] == [
        "frontend-contract",
        "api-contract",
    ]
    assert result.truncated is False


def test_topology_traversal_handles_cycles_directions_and_limits():
    topology = InMemoryTopologyRepository()
    for identifier in ("a", "b", "c"):
        topology.put_entity(PRODUCT, entity(identifier))
    for source, target in (("a", "b"), ("b", "c"), ("c", "a")):
        topology.put_relationship(
            PRODUCT,
            TopologyRelationship(
                f"{source}-{target}", source, target, "relates_to", "approved"
            ),
        )

    outbound = topology.traverse(
        TopologyTraversal(PRODUCT, ("a",), depth=3, direction="outbound")
    )
    inbound = topology.traverse(
        TopologyTraversal(PRODUCT, ("a",), depth=1, direction="inbound")
    )
    limited = topology.traverse(
        TopologyTraversal(PRODUCT, ("a",), depth=3, entity_limit=2)
    )
    relationship_limited = topology.traverse(
        TopologyTraversal(PRODUCT, ("a",), depth=3, relationship_limit=1)
    )

    assert [item.id for item in outbound.entities] == ["a", "b", "c"]
    assert len(outbound.relationships) == 3
    assert [item.id for item in inbound.entities] == ["a", "c"]
    assert [relationship.id for relationship in inbound.relationships] == ["c-a"]
    assert len(limited.entities) == 2
    assert limited.truncated is True
    assert len(relationship_limited.relationships) == 1
    assert relationship_limited.truncated is True


def test_topology_filters_confidence_lifecycle_and_stale_evidence():
    topology = InMemoryTopologyRepository()
    evidence = TopologyEvidence("verification", "contract_test", "build", "abc123")
    topology.put_entity(PRODUCT, entity("source", evidence=(evidence,)))
    topology.put_entity(PRODUCT, entity("trusted", confidence=0.9))
    topology.put_entity(PRODUCT, entity("uncertain", confidence=0.4))
    topology.put_entity(PRODUCT, entity("stale", stale=True))
    for target in ("trusted", "uncertain", "stale"):
        topology.put_relationship(
            PRODUCT,
            TopologyRelationship(
                f"source-{target}",
                "source",
                target,
                "depends_on",
                "approved",
                evidence=(evidence,),
            ),
        )

    result = topology.traverse(
        TopologyTraversal(PRODUCT, ("source",), minimum_confidence=0.8)
    )

    assert [item.id for item in result.entities] == ["source", "trusted"]
    assert [relationship.id for relationship in result.relationships] == [
        "source-trusted"
    ]
    assert result.entities[0].evidence == (evidence,)


def test_topology_updates_preserve_identity_and_replace_duplicate_edges():
    topology = InMemoryTopologyRepository()
    for identifier in ("a", "b", "c"):
        topology.put_entity(PRODUCT, entity(identifier))
    topology.put_relationship(
        PRODUCT,
        TopologyRelationship("edge", "a", "b", "depends_on", "approved"),
    )
    topology.put_entity(PRODUCT, entity("a", properties={"qualified_name": "moved.a"}))
    topology.put_relationship(
        PRODUCT,
        TopologyRelationship("edge", "a", "c", "depends_on", "approved"),
    )

    result = topology.traverse(TopologyTraversal(PRODUCT, ("a",), depth=1))

    assert [item.id for item in result.entities] == ["a", "c"]
    assert result.entities[0].properties == {"qualified_name": "moved.a"}
    assert [relationship.target_id for relationship in result.relationships] == ["c"]


def test_topology_identifies_missing_relationship_endpoint():
    topology = InMemoryTopologyRepository()
    topology.put_entity(PRODUCT, entity("a"))
    topology.put_entity(OTHER_PRODUCT, entity("b"))

    with pytest.raises(ValueError, match="target entity 'b' does not exist in scope"):
        topology.put_relationship(
            PRODUCT,
            TopologyRelationship("edge", "a", "b", "depends_on", "approved"),
        )


def test_topology_rejects_empty_identifiers():
    with pytest.raises(ValueError, match="evidence id cannot be empty"):
        TopologyEvidence("", "contract_test", "build")
    with pytest.raises(ValueError, match="entity id cannot be empty"):
        entity("")
    with pytest.raises(ValueError, match="relationship id cannot be empty"):
        TopologyRelationship("", "a", "b", "depends_on", "approved")
    with pytest.raises(ValueError, match="relationship source_id cannot be empty"):
        TopologyRelationship("edge", "", "b", "depends_on", "approved")
    with pytest.raises(ValueError, match="relationship target_id cannot be empty"):
        TopologyRelationship("edge", "a", "", "depends_on", "approved")
    with pytest.raises(ValueError, match="entity_ids cannot contain empty values"):
        TopologyTraversal(PRODUCT, ("",))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("depth", 4, "depth must be between 1 and 3"),
        ("minimum_confidence", 1.1, "minimum_confidence must be between 0 and 1"),
        ("entity_limit", 51, "entity_limit must be between 1 and 50"),
        (
            "relationship_limit",
            501,
            "relationship_limit must be between 1 and 500",
        ),
        ("direction", "sideways", "direction must be outbound, inbound, or both"),
    ],
)
def test_topology_traversal_rejects_unbounded_inputs(field, value, message):
    arguments = {"scope": PRODUCT, "entity_ids": ("a",), field: value}

    with pytest.raises(ValueError, match=message):
        TopologyTraversal(**arguments)
