"""What a review read, and whether it admits to what it did not.

A review that could not reach a repository and one that reached it and found
nothing say the same thing to a reader: nothing. These hold the difference.
"""

from src.core.change_context import ChangeContext, ChangedFile, ChangeSet
from src.core.impact import ChangeImpact
from src.core.review_coverage import (
    Coverage,
    KEYWORD,
    NEIGHBOURING_CODE,
    REACH,
    SIBLING_SOURCE,
    read_and_unread,
)
from src.core.scope import Scope
from src.core.search import CodeTextMatch, CodeTextResult, CodeTextSearcher
from src.core.services import ServiceRegistry
from src.core.topology import TopologyEntity, TopologySubgraph
from src.plugins.builtin.code_reviewer.context import related_code_section

HERE = Scope.from_mapping({"repository": "acme/api"})


def reaching(*repositories):
    return ChangeContext(
        scope=HERE,
        impact=ChangeImpact(
            TopologySubgraph(
                tuple(
                    TopologyEntity(
                        f"system:{name}",
                        "system",
                        "approved",
                        properties={"name": name},
                    )
                    for name in repositories
                ),
                (),
                False,
            ),
            (),
            (),
            False,
        ),
    )


def changes():
    return ChangeSet(
        scope=HERE,
        files=(ChangedFile(path="a.py"),),
        revision="head_sha_def",
        diff="--- a/a.py\n+++ b/a.py\n@@\n+def rebalance():\n",
    )


class TestWhatTheRecordHolds:
    def test_a_repository_nobody_could_read_is_named_with_its_reason(self):
        coverage = Coverage()
        coverage.reaches(("acme/web", "acme/billing"))
        coverage.record(SIBLING_SOURCE, KEYWORD, answered=True, target="acme/web")
        coverage.record(
            SIBLING_SOURCE,
            KEYWORD,
            answered=False,
            target="acme/billing",
            reason="Not read yet, so not searched: acme/billing",
        )

        assert coverage.unread == (
            ("acme/billing", "Not read yet, so not searched: acme/billing"),
        )

    def test_a_repository_that_answered_is_not_called_unread(self):
        coverage = Coverage()
        coverage.reaches(("acme/web",))
        coverage.record(SIBLING_SOURCE, KEYWORD, answered=True, target="acme/web")

        assert coverage.unread == ()


class TestWhatTheReaderIsTold:
    def test_it_says_how_many_of_the_reached_systems_were_read(self):
        coverage = Coverage()
        coverage.record(REACH, "graph", answered=True)
        coverage.reaches(("acme/web", "acme/billing"))
        coverage.record(SIBLING_SOURCE, KEYWORD, answered=True, target="acme/web")
        coverage.record(
            SIBLING_SOURCE,
            KEYWORD,
            answered=False,
            target="acme/billing",
            reason="never indexed",
        )

        said = read_and_unread(coverage)

        assert "1 of 2 systems this change reaches" in said
        assert "acme/billing (never indexed)" in said

    def test_a_review_that_could_not_walk_the_graph_says_so(self):
        coverage = Coverage()
        coverage.record(
            REACH,
            "graph",
            answered=False,
            reason="the system graph could not be walked",
        )

        said = read_and_unread(coverage)

        assert "could not say what this change reaches" in said

    def test_a_review_that_reached_nothing_is_not_confused_with_one_that_could_not_look(
        self,
    ):
        walked = Coverage()
        walked.record(REACH, "graph", answered=True)

        assert "reaches nothing else" in read_and_unread(walked)


class TestOneUnreadableRepositoryCostsThatRepository:
    """The questions are asked per repository, so a failure is contained."""

    def test_the_others_still_answer_and_the_missing_one_is_recorded(self):
        class _OneIsUnread:
            def search_text(self, query):
                if query.scope.get("repository") == "acme/billing":
                    return CodeTextResult(unavailable="Not read yet, so not searched")
                return CodeTextResult(
                    (
                        CodeTextMatch(
                            "web/app.ts", "r2", 1, 2, "rebalance()", "acme/web"
                        ),
                    )
                )

        services = ServiceRegistry()
        services.register(CodeTextSearcher, _OneIsUnread(), "test")
        coverage = Coverage()

        section = related_code_section(
            changes(),
            services,
            None,
            None,
            None,
            reaching("acme/web", "acme/billing"),
            coverage,
        )

        assert "web/app.ts" in section
        assert coverage.answered(SIBLING_SOURCE, "acme/web")
        assert not coverage.answered(SIBLING_SOURCE, "acme/billing")
        assert coverage.unread == (("acme/billing", "Not read yet, so not searched"),)

    def test_no_search_backend_is_said_out_loud_rather_than_left_out(self):
        """Told nothing was found, a review reads that as nothing being there."""
        services = ServiceRegistry()
        coverage = Coverage()

        section = related_code_section(
            changes(), services, None, None, None, reaching("acme/web"), coverage
        )

        assert "not available in this deployment" in section
        assert coverage.unread == (("acme/web", "nothing here can search code"),)

    def test_a_repository_that_cannot_be_searched_does_not_end_the_review(self):
        class _Refuses:
            def search_text(self, query):
                raise RuntimeError("no checkout")

        services = ServiceRegistry()
        services.register(CodeTextSearcher, _Refuses(), "test")
        coverage = Coverage()

        section = related_code_section(
            changes(), services, None, None, None, reaching("acme/web"), coverage
        )

        assert "unavailable" in section
        assert not coverage.answered(SIBLING_SOURCE, "acme/web")
        assert not coverage.answered(NEIGHBOURING_CODE)
