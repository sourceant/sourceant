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


def test_scoped_importance_survives_storage_and_precedes_result_limit(
    tmp_path, monkeypatch
):
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
    from src.core.knowledge import sql

    decoded = []
    original = sql.KnowledgeObject

    def record(*args, **kwargs):
        decoded.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(sql, "KnowledgeObject", record)
    reloaded = SQLKnowledgeRepository(engine)
    selected = LinkedKnowledgeSelector(reloaded).select(
        KnowledgeSelection(scope, limit=1)
    )
    assert [item.id for item in selected] == ["important"]
    assert decoded == ["important"]
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


@pytest.fixture
def selection_store(tmp_path):
    import os
    from uuid import uuid4

    engine = create_engine(
        os.environ.get(
            "TEST_KNOWLEDGE_DATABASE_URL", f"sqlite:///{tmp_path / 'selection.db'}"
        )
    )
    store = SQLKnowledgeRepository(engine, create_schema=True)
    scope = Scope.from_mapping({"repository": str(uuid4())})
    yield engine, store, scope
    engine.dispose()


def test_sql_selection_ranks_all_applicability_sources_before_limit(
    selection_store, monkeypatch
):
    from src.core.knowledge import KnowledgeLink
    from src.core.knowledge import sql

    engine, store, scope = selection_store
    entries = [
        ("linked-low", {"importance": "low"}),
        ("scope-high", {"importance": "high", "applicability": "scope"}),
        ("path-critical", {"importance": "critical", "paths": ["src/[ab]*.py"]}),
        ("scope-critical", {"importance": "critical", "applicability": "scope"}),
        ("unattached-critical", {"importance": "critical"}),
    ]
    for identifier, properties in entries:
        store.put(
            scope,
            KnowledgeObject(identifier, "rule", "accepted", identifier, properties),
        )
    for identifier in ("linked-low", "path-critical"):
        for suffix in ("one", "two"):
            store.put_link(
                scope,
                KnowledgeLink(identifier + suffix, identifier, "code", "src/api.py"),
            )
    store.put(
        scope,
        KnowledgeObject(
            "candidate",
            "rule",
            "candidate",
            "Unreviewed",
            {
                "importance": "critical",
                "applicability": "scope",
            },
        ),
    )
    store.put(
        Scope.from_mapping({"repository": "outside"}),
        KnowledgeObject(
            "outside",
            "rule",
            "accepted",
            "Other repository",
            {
                "importance": "critical",
                "applicability": "scope",
            },
        ),
    )
    decoded = []
    original = sql.KnowledgeObject

    def record(*args, **kwargs):
        decoded.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(sql, "KnowledgeObject", record)
    reader = SQLKnowledgeRepository(engine)
    result = LinkedKnowledgeSelector(reader).select(
        KnowledgeSelection(scope, ("src/api.py",), limit=2)
    )
    assert [item.id for item in result] == ["path-critical", "scope-critical"]
    assert "outside" not in decoded
    assert "candidate" not in decoded
    assert "unattached-critical" not in decoded
    assert len(decoded) <= 6


def test_sql_selection_streams_past_nonmatching_globs(selection_store):
    _, store, scope = selection_store
    for i in range(105):
        store.put(
            scope,
            KnowledgeObject(
                f"a{i:03}",
                "rule",
                "accepted",
                "Other path",
                {
                    "importance": "critical",
                    "paths": ["other/**"],
                },
            ),
        )
    store.put(
        scope,
        KnowledgeObject(
            "z-match",
            "rule",
            "accepted",
            "Applicable",
            {
                "importance": "critical",
                "paths": ["src/[ab]*.py"],
            },
        ),
    )
    store.put(
        scope,
        KnowledgeObject(
            "low",
            "rule",
            "accepted",
            "Scope",
            {
                "importance": "low",
                "applicability": "scope",
            },
        ),
    )
    selected = LinkedKnowledgeSelector(store).select(
        KnowledgeSelection(scope, ("src/api.py",), limit=1)
    )
    assert [item.id for item in selected] == ["z-match"]
    assert [
        item.id
        for item in LinkedKnowledgeSelector(store).select(
            KnowledgeSelection(scope, ("SRC/api.py",), limit=1)
        )
    ] == ["low"]


def test_sql_selection_breaks_importance_ties_by_id(selection_store):
    _, store, scope = selection_store
    for identifier in ("a", "Z", "B"):
        store.put(
            scope,
            KnowledgeObject(
                identifier,
                "rule",
                "accepted",
                identifier,
                {
                    "applicability": "scope",
                },
            ),
        )
    selected = LinkedKnowledgeSelector(store).select(KnowledgeSelection(scope, limit=2))
    assert [item.id for item in selected] == ["B", "Z"]
