import pytest
from sqlalchemy import create_engine

from src.core.knowledge import (
    KnowledgeImportance,
    KnowledgeObject,
    KnowledgeQuery,
    KnowledgeSelection,
    LinkedKnowledgeSelector,
    SQLKnowledgeRepository,
)
from src.core.scope import Scope


def test_scoped_importance_survives_storage_and_precedes_result_limit(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'knowledge.db'}")
    store = SQLKnowledgeRepository(engine, create_schema=True)
    scope = Scope.from_mapping({"repository": "example/api"})
    for i in range(105):
        store.put(
            scope,
            KnowledgeObject(
                str(i),
                "constraint",
                "accepted",
                "Use UTC",
                {
                    "applicability": "scope",
                    "importance": "low",
                },
            ),
        )
    store.put(
        scope,
        KnowledgeObject(
            "important",
            "constraint",
            "accepted",
            "Keep data isolated",
            {
                "applicability": "scope",
                "importance": "high",
                "basis": "absence_observation",
                "evidence_note": "All configured routes examined; runtime plugins excluded",
            },
        ),
    )
    store.put(
        scope,
        KnowledgeObject(
            "candidate",
            "constraint",
            "candidate",
            "Unreviewed",
            {
                "applicability": "scope",
                "importance": "critical",
            },
        ),
    )
    store.put(
        scope,
        KnowledgeObject(
            "unrelated",
            "constraint",
            "accepted",
            "Other module",
            {
                "importance": "critical",
                "paths": ["other/**"],
            },
        ),
    )
    other = Scope.from_mapping({"repository": "example/other"})
    store.put(
        other,
        KnowledgeObject(
            "outside",
            "constraint",
            "accepted",
            "Other scope",
            {
                "applicability": "scope",
                "importance": "critical",
            },
        ),
    )
    reloaded = SQLKnowledgeRepository(engine)
    selected = LinkedKnowledgeSelector(reloaded).select(
        KnowledgeSelection(scope, limit=1)
    )
    assert [item.id for item in selected] == ["important"]
    assert selected[0].importance == KnowledgeImportance.HIGH
    assert (
        reloaded.search(KnowledgeQuery(scope, ids=frozenset({"important"}))).items
        == selected
    )


def test_legacy_unattached_knowledge_is_not_silently_global():
    from src.core.knowledge import InMemoryKnowledgeRepository

    store = InMemoryKnowledgeRepository()
    scope = Scope.from_mapping({"repository": "example/api"})
    store.put(scope, KnowledgeObject("legacy", "decision", "accepted", "Legacy"))
    assert LinkedKnowledgeSelector(store).select(KnowledgeSelection(scope)) == ()


@pytest.mark.parametrize(
    "properties",
    [
        {"importance": "urgent"},
        {"applicability": "everywhere"},
        {"basis": "absence_observation"},
    ],
)
def test_invalid_characteristics_are_rejected(properties):
    with pytest.raises(ValueError):
        KnowledgeObject("invalid", "constraint", "candidate", "Invalid", properties)
