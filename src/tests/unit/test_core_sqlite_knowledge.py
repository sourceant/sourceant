from src.core.knowledge import (
    Knowledge,
    KnowledgeQuery,
    KnowledgeRelationship,
    KnowledgeTraversal,
    SQLiteKnowledgeRepository,
)
from src.core.scope import Scope

PROJECT = Scope.from_mapping({"project": "one"})
OTHER_PROJECT = Scope.from_mapping({"project": "two"})


def test_sqlite_knowledge_survives_restart_and_preserves_scope(tmp_path):
    path = tmp_path / "knowledge.db"
    repository = SQLiteKnowledgeRepository(path)
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
    repository.close()

    reopened = SQLiteKnowledgeRepository(path)
    result = reopened.traverse(KnowledgeTraversal(PROJECT, ("decision",)))
    other = reopened.search(KnowledgeQuery(OTHER_PROJECT))

    assert [item.summary for item in result.items] == ["Use one", "Use one"]
    assert [edge.id for edge in result.relationships] == ["decision-rule"]
    assert [item.summary for item in other.items] == ["Use two", "Use two"]
    reopened.close()


def test_sqlite_knowledge_updates_items_and_relationships(tmp_path):
    repository = SQLiteKnowledgeRepository(tmp_path / "knowledge.db")
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
    repository.close()
