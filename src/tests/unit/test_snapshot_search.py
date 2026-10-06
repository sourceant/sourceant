import pytest
from pathlib import Path
from src.core.review.source_search import SnapshotSearcher
from src.core.scope import Scope
from src.core.search import SearchQuery, SearchResult


@pytest.fixture(autouse=True)
def use_real_ripgrep(monkeypatch):
    monkeypatch.setattr("src.core.search.source_text.SMALL_SOURCE_BYTES", 0)


def test_snapshot_search_returns_head_excerpts_and_limits_results():
    scope = Scope.from_mapping({"repository": "acme/app", "revision": "head"})
    files = {
        "caller.ts": "input\nconst chosen = locale;\n",
        "other.ts": "LOCALE\n",
        "binary": None,
    }
    searcher = SnapshotSearcher(scope, tuple(files), files.get)
    answer = searcher.search(SearchQuery(scope, ("locale",), limit=1))
    assert answer.truncated is True
    assert len(answer.matches) == 1
    found = answer.matches[0]
    assert (found.path, found.revision, found.start_line, found.end_line) == (
        "caller.ts",
        "head",
        1,
        2,
    )
    assert found.text == files["caller.ts"].rstrip()
    assert searcher.search(SearchQuery(scope, ("locale",), limit=2)).truncated is False


def test_other_revisions_and_repositories_use_the_existing_searcher():
    scope = Scope.from_mapping({"repository": "acme/app", "revision": "head"})

    class Existing:
        def search(self, query):
            return SearchResult(unavailable="Existing source has no snapshot")

    searcher = SnapshotSearcher(scope, (), lambda _: None, Existing())
    for other in (
        scope.extend({"revision": "base"}),
        Scope.from_mapping({"repository": "acme/sibling"}),
    ):
        assert (
            searcher.search(SearchQuery(other, ("locale",))).unavailable
            == "Existing source has no snapshot"
        )
    assert (
        SnapshotSearcher(scope, (), lambda _: None)
        .search(SearchQuery(scope.extend({"revision": "base"}), ("locale",)))
        .unavailable
    )


def test_snapshot_search_ranks_later_stronger_matches_before_truncating():
    scope = Scope.from_mapping({"repository": "acme/app", "revision": "head"})
    files = {"first.ts": "locale\n", "last.ts": "locale request\n"}
    answer = SnapshotSearcher(scope, tuple(files), files.get).search(
        SearchQuery(scope, ("locale", "request"), limit=1)
    )
    assert answer.truncated is True
    assert [one.path for one in answer.matches] == ["last.ts"]


def test_search_preserves_literal_unicode_and_ignored_source():
    scope = Scope.from_mapping({"repository": "acme/app", "revision": "head"})
    files = {
        ".hidden.ts": "Straße\r\nconst value = a.b;\r\n",
        "vendor/ignored.ts": "STRASSE\n",
        "ordinary.ts": "const value = axb;\n",
        "dir:with spaces/file.ts": "const value = a.b;\n",
    }
    searcher = SnapshotSearcher(scope, tuple(files), files.get)
    literal = searcher.search(SearchQuery(scope, ("a.b",)))
    assert literal.unavailable is None
    assert {match.path for match in literal.matches} == {
        ".hidden.ts",
        "dir:with spaces/file.ts",
    }
    folded = searcher.search(SearchQuery(scope, ("strasse",)))
    assert {match.path for match in folded.matches} == {
        ".hidden.ts",
        "vendor/ignored.ts",
    }
    assert any("Straße" in match.text for match in folded.matches)


def test_missing_ripgrep_is_reported_as_unavailable(monkeypatch):
    monkeypatch.setattr("src.core.search.source_text.shutil.which", lambda _: None)
    scope = Scope.from_mapping({"repository": "acme/app", "revision": "head"})
    result = SnapshotSearcher(scope, ("app.py",), lambda _: "needle").search(
        SearchQuery(scope, ("needle",))
    )
    assert not result.matches
    assert result.unavailable == "ripgrep is required for source text search"


def test_parallel_queries_share_one_prepared_revision():
    from concurrent.futures import ThreadPoolExecutor

    scope = Scope.from_mapping({"repository": "acme/app", "revision": "head"})

    def read(path):
        return "needle\n"

    searcher = SnapshotSearcher(scope, ("app.py",), read)
    with ThreadPoolExecutor(max_workers=4) as pool:
        answers = list(
            pool.map(
                lambda _: searcher.search(SearchQuery(scope, ("needle",))), range(4)
            )
        )
    assert all(
        answer.matches == answers[0].matches and answer.unavailable is None
        for answer in answers
    )
    assert len(list(Path(searcher._text._directory.name).iterdir())) == 1


def test_external_ripgrep_configuration_cannot_hide_review_source(
    tmp_path, monkeypatch
):
    configuration = tmp_path / "ripgrep.conf"
    configuration.write_text("--glob=!*\n")
    monkeypatch.setenv("RIPGREP_CONFIG_PATH", str(configuration))
    scope = Scope.from_mapping({"repository": "acme/app", "revision": "head"})
    result = SnapshotSearcher(scope, ("app.py",), lambda _: "needle").search(
        SearchQuery(scope, ("needle",))
    )
    assert result.unavailable is None
    assert [match.path for match in result.matches] == ["app.py"]


def test_empty_source_returns_no_matches_without_unavailable_coverage():
    scope = Scope.from_mapping({"repository": "acme/app", "revision": "head"})
    result = SnapshotSearcher(scope, ("binary",), lambda _: None).search(
        SearchQuery(scope, ("needle",))
    )
    assert result.unavailable is None
    assert not result.matches


def test_small_snapshot_uses_memory_without_starting_a_search_process(monkeypatch):
    from unittest.mock import patch
    import subprocess

    monkeypatch.setattr("src.core.search.source_text.SMALL_SOURCE_BYTES", 256_000)
    scope = Scope.from_mapping({"repository": "acme/app", "revision": "head"})
    searcher = SnapshotSearcher(scope, ("app.py",), lambda _: "needle\n")
    with patch(
        "src.core.search.source_text.subprocess.run", wraps=subprocess.run
    ) as run:
        result = searcher.search(SearchQuery(scope, ("needle",)))
    assert [match.path for match in result.matches] == ["app.py"]
    assert result.unavailable is None
    run.assert_not_called()
