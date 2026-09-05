import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.schema import CreateTable

from src.core.scope import Scope
from src.core.topology import (
    SQLTopologyRepository,
    TopologyEntity,
    TopologyEvidence,
    TopologyQuery,
    TopologyRelationship,
    TopologyTraversal,
)
from src.core.topology.sql import entity_table, relationship_table

PROJECT = Scope.from_mapping({"project": "one"})
OTHER_PROJECT = Scope.from_mapping({"project": "two"})


def _mysql_key_bytes(table) -> int:
    """What MySQL would count the primary key as, at four bytes a character."""
    return sum(column.type.length or 0 for column in table.primary_key) * 4


def test_sql_topology_scope_keys_compile_for_supported_databases():
    for table in (entity_table, relationship_table):
        mysql_ddl = str(CreateTable(table).compile(dialect=mysql.dialect()))
        postgres_ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))

        assert "scope VARCHAR(191) NOT NULL" in mysql_ddl
        assert "scope TEXT NOT NULL" in postgres_ddl
        assert _mysql_key_bytes(table) <= 3072


def test_sql_topology_survives_restart_and_preserves_scope(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'topology.db'}")
    repository = SQLTopologyRepository(engine, create_schema=True)
    for scope, owner in ((PROJECT, "one"), (OTHER_PROJECT, "two")):
        repository.put_entity(
            scope,
            TopologyEntity("checkout", "service", "approved", properties={"by": owner}),
        )
        repository.put_entity(
            scope,
            TopologyEntity("ledger", "service", "approved", properties={"by": owner}),
        )
        repository.put_relationship(
            scope,
            TopologyRelationship(
                "checkout-ledger",
                "checkout",
                "ledger",
                "depends_on",
                "approved",
            ),
        )
    reopened = SQLTopologyRepository(engine)
    result = reopened.traverse(TopologyTraversal(PROJECT, ("checkout",)))
    other = reopened.traverse(TopologyTraversal(OTHER_PROJECT, ("checkout",)))

    assert [entity.id for entity in result.entities] == ["checkout", "ledger"]
    assert [edge.id for edge in result.relationships] == ["checkout-ledger"]
    assert [entity.properties["by"] for entity in result.entities] == ["one", "one"]
    assert [entity.properties["by"] for entity in other.entities] == ["two", "two"]


def test_sql_topology_round_trips_confidence_stale_and_evidence(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'topology.db'}")
    repository = SQLTopologyRepository(engine, create_schema=True)
    evidence = (
        TopologyEvidence(
            "commit-1",
            "commit",
            "github",
            "abc123",
            {"path": "src/checkout.py"},
        ),
    )
    entity = TopologyEntity(
        "checkout",
        "service",
        "pending",
        confidence=0.4,
        stale=True,
        properties={"team": "payments"},
        evidence=evidence,
    )
    repository.put_entity(PROJECT, entity)

    reopened = SQLTopologyRepository(engine)
    result = reopened.traverse(
        TopologyTraversal(PROJECT, ("checkout",), include_stale=True)
    )

    assert result.entities == (entity,)


def test_sql_topology_traversal_filters_exclude_unapproved_edges(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'topology.db'}")
    repository = SQLTopologyRepository(engine, create_schema=True)
    for identifier in ("checkout", "ledger", "search"):
        repository.put_entity(
            PROJECT, TopologyEntity(identifier, "service", "approved")
        )
    repository.put_relationship(
        PROJECT,
        TopologyRelationship(
            "approved-edge", "checkout", "ledger", "depends_on", "approved"
        ),
    )
    repository.put_relationship(
        PROJECT,
        TopologyRelationship(
            "pending-edge", "checkout", "search", "depends_on", "pending"
        ),
    )

    result = repository.traverse(
        TopologyTraversal(
            PROJECT,
            ("checkout",),
            relationship_statuses=frozenset({"approved"}),
        )
    )

    assert [edge.id for edge in result.relationships] == ["approved-edge"]
    assert [entity.id for entity in result.entities] == ["checkout", "ledger"]


def test_sql_topology_updates_entities_and_relationships(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'topology.db'}")
    repository = SQLTopologyRepository(engine, create_schema=True)
    for identifier in ("one", "two", "three"):
        repository.put_entity(PROJECT, TopologyEntity(identifier, "service", "pending"))
    repository.put_relationship(
        PROJECT,
        TopologyRelationship("edge", "one", "two", "depends_on", "pending"),
    )
    repository.put_entity(PROJECT, TopologyEntity("one", "datastore", "approved"))
    repository.put_relationship(
        PROJECT,
        TopologyRelationship("edge", "one", "three", "consumes", "approved"),
    )

    result = repository.traverse(TopologyTraversal(PROJECT, ("one",)))

    assert result.entities[0] == TopologyEntity("one", "datastore", "approved")
    assert [entity.id for entity in result.entities] == ["one", "three"]
    assert [edge.type for edge in result.relationships] == ["consumes"]


def test_sql_topology_rejects_relationship_with_missing_endpoint(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'topology.db'}")
    repository = SQLTopologyRepository(engine, create_schema=True)
    repository.put_entity(PROJECT, TopologyEntity("checkout", "service", "approved"))

    with pytest.raises(ValueError, match="does not exist in scope"):
        repository.put_relationship(
            PROJECT,
            TopologyRelationship("edge", "checkout", "ledger", "depends_on", "pending"),
        )

    reopened = SQLTopologyRepository(engine)
    result = reopened.traverse(TopologyTraversal(PROJECT, ("checkout",)))

    assert result.relationships == ()


def test_sql_topology_will_not_link_across_scopes(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'topology.db'}")
    repository = SQLTopologyRepository(engine, create_schema=True)
    repository.put_entity(PROJECT, TopologyEntity("checkout", "service", "approved"))
    repository.put_entity(
        OTHER_PROJECT, TopologyEntity("ledger", "service", "approved")
    )

    with pytest.raises(ValueError, match="does not exist in scope"):
        repository.put_relationship(
            PROJECT,
            TopologyRelationship("edge", "checkout", "ledger", "depends_on", "pending"),
        )


def test_sql_topology_reads_writes_from_another_repository_instance(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'topology.db'}")
    reader = SQLTopologyRepository(engine, create_schema=True)
    writer = SQLTopologyRepository(engine)

    writer.put_entity(PROJECT, TopologyEntity("checkout", "service", "approved"))

    result = reader.traverse(TopologyTraversal(PROJECT, ("checkout",)))

    assert [entity.id for entity in result.entities] == ["checkout"]


def test_sql_topology_removes_an_entity_and_its_relationships(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'topology.db'}")
    repository = SQLTopologyRepository(engine, create_schema=True)
    for identifier in ("checkout", "ledger", "search"):
        repository.put_entity(
            PROJECT, TopologyEntity(identifier, "service", "approved")
        )
    repository.put_relationship(
        PROJECT,
        TopologyRelationship("a", "checkout", "ledger", "depends_on", "approved"),
    )
    repository.put_relationship(
        PROJECT,
        TopologyRelationship("b", "search", "checkout", "depends_on", "approved"),
    )
    repository.put_relationship(
        PROJECT,
        TopologyRelationship("c", "search", "ledger", "depends_on", "approved"),
    )

    assert repository.remove_entity(PROJECT, "checkout") is True
    assert repository.remove_entity(PROJECT, "checkout") is False

    reopened = SQLTopologyRepository(engine)
    result = reopened.traverse(TopologyTraversal(PROJECT, ("search",)))

    assert [entity.id for entity in result.entities] == ["search", "ledger"]
    assert [edge.id for edge in result.relationships] == ["c"]


def test_sql_topology_removes_one_relationship_and_keeps_its_endpoints(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'topology.db'}")
    repository = SQLTopologyRepository(engine, create_schema=True)
    for identifier in ("checkout", "ledger"):
        repository.put_entity(
            PROJECT, TopologyEntity(identifier, "service", "approved")
        )
    repository.put_relationship(
        PROJECT,
        TopologyRelationship("edge", "checkout", "ledger", "depends_on", "approved"),
    )

    assert repository.remove_relationship(PROJECT, "edge") is True
    assert repository.remove_relationship(PROJECT, "edge") is False

    reopened = SQLTopologyRepository(engine)
    listing = reopened.search(TopologyQuery(PROJECT))

    assert [entity.id for entity in listing.entities] == ["checkout", "ledger"]
    assert reopened.get_relationships(PROJECT, frozenset({"checkout", "ledger"})) == ()


def test_sql_topology_removal_is_confined_to_its_scope(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'topology.db'}")
    repository = SQLTopologyRepository(engine, create_schema=True)
    for scope in (PROJECT, OTHER_PROJECT):
        repository.put_entity(scope, TopologyEntity("checkout", "service", "approved"))

    assert repository.remove_entity(PROJECT, "checkout") is True

    assert repository.search(TopologyQuery(PROJECT)).total == 0
    assert repository.search(TopologyQuery(OTHER_PROJECT)).total == 1
