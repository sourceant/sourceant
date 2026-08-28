import subprocess

import pytest
from sqlalchemy import create_engine

from src.core.code_index import CodeGraphQuery, CodeSearch, InMemoryCodeIndex
from src.core.code_index.indexer import RepositoryIndexer
from src.core.code_index.sql import SQLCodeIndexRepository
from src.core.scope import Scope

SCOPE = Scope.from_mapping({"repository": "acme/billing"})

CHARGE = "def charge(amount):\n    return amount\n"
REFUND = "import os\n\n\ndef refund(amount):\n    return -amount\n"


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "billing"
    (root / "src").mkdir(parents=True)
    (root / "src" / "charge.py").write_text(CHARGE)
    (root / "src" / "refund.py").write_text(REFUND)
    (root / "README.md").write_text("# billing\n")
    return root


@pytest.fixture
def durable(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'store.db'}")
    return SQLCodeIndexRepository(engine, create_schema=True)


def _paths(store, scope=SCOPE):
    query = CodeSearch(scope=scope, labels=frozenset({"File"}), limit=100)
    return sorted(node.properties["file_path"] for node in store.search(query).nodes)


def test_it_indexes_every_source_file_it_walks(repository, durable):
    result = RepositoryIndexer(durable).index(SCOPE, repository)

    assert _paths(durable) == ["README.md", "src/charge.py", "src/refund.py"]
    assert result.indexed == 3


def test_it_records_the_symbols_it_found(repository, durable):
    RepositoryIndexer(durable).index(SCOPE, repository)

    names = {
        node.properties.get("name")
        for node in durable.search(
            CodeSearch(
                scope=SCOPE, properties={"file_path": "src/charge.py"}, limit=100
            )
        ).nodes
    }

    assert "charge" in names


def test_a_second_run_reparses_nothing_when_nothing_changed(repository, durable):
    indexer = RepositoryIndexer(durable)
    indexer.index(SCOPE, repository)

    result = indexer.index(SCOPE, repository, update=True)

    assert (result.indexed, result.unchanged, result.removed) == (0, 3, 0)


def test_a_second_run_reparses_only_what_changed(repository, durable):
    indexer = RepositoryIndexer(durable)
    indexer.index(SCOPE, repository)
    (repository / "src" / "charge.py").write_text(
        "def charge(amount):\n    return amount * 2\n\n\ndef fee():\n    return 1\n"
    )

    result = indexer.index(SCOPE, repository, update=True)

    assert (result.indexed, result.unchanged) == (1, 2)
    names = {
        node.properties.get("name")
        for node in durable.search(
            CodeSearch(
                scope=SCOPE, properties={"file_path": "src/charge.py"}, limit=100
            )
        ).nodes
    }
    assert "fee" in names


def test_a_deleted_file_leaves_the_graph(repository, durable):
    indexer = RepositoryIndexer(durable)
    indexer.index(SCOPE, repository)
    (repository / "src" / "refund.py").unlink()

    result = indexer.index(SCOPE, repository, update=True)

    assert result.removed == 1
    assert _paths(durable) == ["README.md", "src/charge.py"]


def test_a_full_run_replaces_what_was_there(repository, durable):
    indexer = RepositoryIndexer(durable)
    indexer.index(SCOPE, repository)
    (repository / "src" / "refund.py").unlink()

    indexer.index(SCOPE, repository)

    assert _paths(durable) == ["README.md", "src/charge.py"]


def test_it_leaves_out_paths_the_caller_excluded(repository, durable):
    RepositoryIndexer(durable).index(
        SCOPE, repository, excluded_paths=frozenset({"src"})
    )

    assert _paths(durable) == ["README.md"]


def test_it_does_not_walk_into_a_dependency_directory(repository, durable):
    (repository / "node_modules").mkdir()
    (repository / "node_modules" / "dep.py").write_text(CHARGE)

    RepositoryIndexer(durable).index(SCOPE, repository)

    assert _paths(durable) == ["README.md", "src/charge.py", "src/refund.py"]


def test_it_honours_gitignore_when_the_directory_is_a_repository(repository, durable):
    (repository / ".gitignore").write_text("src/refund.py\n")
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)

    RepositoryIndexer(durable).index(SCOPE, repository)

    assert _paths(durable) == [".gitignore", "README.md", "src/charge.py"]


def test_two_repositories_share_one_store(repository, durable, tmp_path):
    other_root = tmp_path / "shipping"
    (other_root / "src").mkdir(parents=True)
    (other_root / "src" / "ship.py").write_text("def ship():\n    return True\n")
    other_scope = Scope.from_mapping({"repository": "acme/shipping"})

    indexer = RepositoryIndexer(durable)
    indexer.index(SCOPE, repository)
    indexer.index(other_scope, other_root)

    assert _paths(durable) == ["README.md", "src/charge.py", "src/refund.py"]
    assert _paths(durable, other_scope) == ["src/ship.py"]


def test_the_whole_scope_can_be_drawn_after_indexing(repository, durable):
    RepositoryIndexer(durable).index(SCOPE, repository)

    drawing = durable.graph(CodeGraphQuery(scope=SCOPE))

    assert {
        node.properties.get("file_path")
        for node in drawing.nodes
        if "File" in node.labels
    } == {"README.md", "src/charge.py", "src/refund.py"}
    assert any(edge.type == "IMPORTS" for edge in drawing.edges)


def test_indexing_remains_complete_across_buffer_checkpoints(
    repository, durable, monkeypatch
):
    monkeypatch.setattr("src.core.code_index.sql.CHECKPOINT", 2)
    checkpoints = []
    checkpoint = durable.checkpoint

    def tracked_checkpoint():
        flushed = checkpoint()
        checkpoints.append(flushed)
        return flushed

    monkeypatch.setattr(durable, "checkpoint", tracked_checkpoint)

    RepositoryIndexer(durable).index(SCOPE, repository)

    assert sum(checkpoints) >= 2
    assert _paths(durable) == ["README.md", "src/charge.py", "src/refund.py"]
    drawing = durable.graph(CodeGraphQuery(scope=SCOPE, include_tests=True))
    assert any(edge.type == "IMPORTS" for edge in drawing.edges)


def test_it_works_against_an_index_that_only_writes(repository):
    result = RepositoryIndexer(InMemoryCodeIndex()).index(SCOPE, repository)

    assert result.indexed == 3


def test_it_refuses_a_path_that_is_not_a_directory(repository, durable):
    with pytest.raises(ValueError):
        RepositoryIndexer(durable).index(SCOPE, repository / "README.md")
