from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.schema import CreateTable

from src.core.knowledge import (
    Knowledge,
    KnowledgeQuery,
    KnowledgeRelationship,
    KnowledgeTraversal,
    SQLKnowledgeRepository,
)
from src.core.knowledge.sql import knowledge_table, relationship_table
from src.core.scope import Scope

PROJECT = Scope.from_mapping({"project": "one"})
OTHER_PROJECT = Scope.from_mapping({"project": "two"})


def test_sql_knowledge_scope_keys_compile_for_supported_databases():
    for table in (knowledge_table, relationship_table):
        mysql_ddl = str(CreateTable(table).compile(dialect=mysql.dialect()))
        postgres_ddl = str(
            CreateTable(table).compile(dialect=postgresql.dialect())
        )

        assert "scope VARCHAR(500) NOT NULL" in mysql_ddl
        assert "scope TEXT NOT NULL" in postgres_ddl


def test_sql_knowledge_survives_restart_and_preserves_scope(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'knowledge.db'}")
    repository = SQLKnowledgeRepository(engine, create_schema=True)
    for scope, summary in ((PROJECT, "Use one"), (OTHER_PROJECT, "Use two")):
        repository.put(scope, Knowledge("decision", "decision", "approved", summary))
        repository.put(scope, Knowledge("rule", "rule", "approved", summary))
        repository.put_relationship(
            scope,
            KnowledgeRelationship(
                "decision-rule",
                "decision",
                "rule",
                "supports",
                "approved",
            ),
        )
    reopened = SQLKnowledgeRepository(engine)
    result = reopened.traverse(KnowledgeTraversal(PROJECT, ("decision",)))
    other = reopened.search(KnowledgeQuery(OTHER_PROJECT))

    assert [item.summary for item in result.items] == ["Use one", "Use one"]
    assert [edge.id for edge in result.relationships] == ["decision-rule"]
    assert [item.summary for item in other.items] == ["Use two", "Use two"]


def test_sql_knowledge_updates_items_and_relationships(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'knowledge.db'}")
    repository = SQLKnowledgeRepository(engine, create_schema=True)
    for identifier in ("one", "two", "three"):
        repository.put(
            PROJECT,
            Knowledge(identifier, "decision", "approved", identifier),
        )
    repository.put_relationship(
        PROJECT,
        KnowledgeRelationship("edge", "one", "two", "supports"),
    )
    repository.put(PROJECT, Knowledge("one", "rule", "active", "Updated"))
    repository.put_relationship(
        PROJECT,
        KnowledgeRelationship("edge", "one", "three", "depends_on"),
    )

    result = repository.traverse(KnowledgeTraversal(PROJECT, ("one",)))

    assert result.items[0] == Knowledge("one", "rule", "active", "Updated")
    assert [item.id for item in result.items] == ["one", "three"]
    assert [edge.type for edge in result.relationships] == ["depends_on"]


def test_sql_knowledge_reads_writes_from_another_repository_instance(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'knowledge.db'}")
    reader = SQLKnowledgeRepository(engine, create_schema=True)
    writer = SQLKnowledgeRepository(engine)

    writer.put(
        PROJECT,
        Knowledge("decision", "decision", "approved", "Use signed requests"),
    )

    result = reader.search(KnowledgeQuery(PROJECT))

    assert [item.id for item in result.items] == ["decision"]
