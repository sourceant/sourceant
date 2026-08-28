"""Every collection a caller sizes, at a size that breaks an unchunked query.

SQLite compiled before 3.32 binds at most 999 parameters. These use more than
that at each seam so a regression fails here rather than on a large repository.
"""

import pytest
from sqlalchemy import create_engine, event

from src.core.code_index import CodeEdge, CodeGraphQuery, CodeNode, CodeSearch
from src.core.code_index.sql import SQLCodeIndexRepository
from src.core.impact import ChangedCodeReference
from src.core.impact.sql import SQLImpactSeedRepository
from src.core.knowledge import KnowledgeLink, KnowledgeObject
from src.core.knowledge.sql import SQLKnowledgeRepository
from src.core.requirements import CODE, CoverageQuery, Requirement, RequirementLink
from src.core.requirements.sql import SQLRequirementsRepository
from src.core.scope import Scope

SCOPE = Scope.from_mapping({"repository": "acme/billing"})
MANY = 2500


def _engine(tmp_path, name):
    return create_engine(f"sqlite:///{tmp_path / name}")


def _counted(engine):
    counted = {"n": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        counted["n"] += 1

    return counted


@pytest.fixture
def code(tmp_path):
    store = SQLCodeIndexRepository(_engine(tmp_path, "code.db"), create_schema=True)
    with store.bulk_writes():
        for index in range(MANY):
            store.put_node(
                SCOPE,
                CodeNode(
                    f"file:src/f{index}.py",
                    frozenset({"File"}),
                    {"file_path": "src/shared.py" if index else f"src/f{index}.py"},
                ),
            )
        for index in range(MANY - 1):
            store.put_edge(
                SCOPE,
                CodeEdge(
                    f"imports:{index}",
                    f"file:src/f{index}.py",
                    f"file:src/f{index + 1}.py",
                    "IMPORTS",
                ),
            )
    return store


def test_drawing_a_large_scope_does_not_exceed_what_sqlite_binds(code):
    drawing = code.graph(CodeGraphQuery(scope=SCOPE, node_limit=MANY))

    assert len(drawing.nodes) == MANY
    assert len(drawing.edges) == MANY - 1


def test_removing_a_path_shared_by_thousands_of_nodes(code):
    code.remove_path(SCOPE, "src/shared.py")

    assert code.search(CodeSearch(scope=SCOPE, limit=100)).total == 1


def test_requirement_coverage_over_thousands_of_changed_paths(tmp_path):
    store = SQLRequirementsRepository(_engine(tmp_path, "req.db"), create_schema=True)
    store.put(
        SCOPE, Requirement(id="r1", kind="requirement", status="open", summary="x")
    )
    store.put_link(
        SCOPE,
        RequirementLink(
            id="l1", requirement_id="r1", target_kind=CODE, target_id="src/hit.py"
        ),
    )
    paths = frozenset([f"src/f{index}.py" for index in range(MANY)] + ["src/hit.py"])

    report = store.coverage(CoverageQuery(scope=SCOPE, paths=paths))

    assert [item.requirement_id for item in report.items] == ["r1"]


def test_requirement_links_for_thousands_of_requirements(tmp_path):
    store = SQLRequirementsRepository(_engine(tmp_path, "links.db"), create_schema=True)
    for index in range(MANY):
        store.put(
            SCOPE,
            Requirement(id=f"r{index}", kind="requirement", status="open", summary="x"),
        )
        store.put_link(
            SCOPE,
            RequirementLink(
                id=f"l{index}",
                requirement_id=f"r{index}",
                target_kind=CODE,
                target_id=f"src/f{index}.py",
            ),
        )

    found = store.get_links(SCOPE, frozenset(f"r{index}" for index in range(MANY)))

    assert len(found) == MANY


def test_knowledge_for_thousands_of_changed_paths(tmp_path):
    store = SQLKnowledgeRepository(_engine(tmp_path, "know.db"), create_schema=True)
    store.put(
        SCOPE,
        KnowledgeObject(id="d1", kind="decision", status="approved", summary="x"),
    )
    store.put_link(
        SCOPE,
        KnowledgeLink(
            id="kl1", knowledge_id="d1", target_kind="code", target_id="src/hit.py"
        ),
    )
    paths = frozenset([f"src/f{index}.py" for index in range(MANY)] + ["src/hit.py"])

    assert store.knowledge_ids_for_paths(SCOPE, paths) == frozenset({"d1"})


def test_impact_seeds_ask_once_per_kind_not_once_per_file(tmp_path):
    engine = _engine(tmp_path, "impact.db")
    store = SQLImpactSeedRepository(engine, create_schema=True)
    changes = tuple(
        ChangedCodeReference(
            id=f"file:src/f{index}.py", kind="file", revision="abc", path="x"
        )
        for index in range(100)
    )
    for change in changes:
        store.put_mapping(SCOPE, change, ("system:billing",))
    counted = _counted(engine)

    assert store.resolve(SCOPE, changes) == ("system:billing",)
    assert counted["n"] == 1


def test_impact_seeds_over_thousands_of_changed_files(tmp_path):
    store = SQLImpactSeedRepository(
        _engine(tmp_path, "impact-many.db"), create_schema=True
    )
    changes = tuple(
        ChangedCodeReference(
            id=f"file:src/f{index}.py", kind="file", revision="abc", path="x"
        )
        for index in range(MANY)
    )
    store.put_mapping(SCOPE, changes[0], ("system:billing",))

    assert store.resolve(SCOPE, changes) == ("system:billing",)
