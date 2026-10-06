from src.core.review.source_search import SnapshotSearcher
from src.core.scope import Scope
from src.core.search import SearchQuery, SearchResult


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
