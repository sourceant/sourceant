"""What a review read, and whether it admits to what it did not.

A review that could not reach a repository and one that reached it and found
nothing say the same thing to a reader: nothing. These hold the difference.
"""

import json

from src.core.change_context import ChangeContext, ChangedFile, ChangeSet
from src.core.impact import ChangeImpact
from src.core.review_coverage import (
    Coverage,
    KEYWORD,
    NEIGHBOURING_CODE,
    NOTHING,
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
                        properties={"name": name, "derived": True},
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


class Asks:
    """A model that asks to search each repository once, then stops."""

    def __init__(self, *repositories, terms=("rebalance",)):
        self.repositories = list(repositories)
        self.terms = terms
        self.asked = []

    def ask_with_tools(self, messages, tools, *, purpose="tools", require=False):
        if not self.repositories:
            return {"content": "", "tool_calls": []}
        calls = [
            {
                "id": f"call-{index}",
                "name": "search_code",
                "arguments": json.dumps(
                    {"repository": name, "terms": list(self.terms)}
                ),
            }
            for index, name in enumerate(self.repositories)
        ]
        self.asked.extend(self.repositories)
        self.repositories = []
        return {"content": "", "tool_calls": calls}


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

    def test_a_review_that_could_not_walk_the_graph_says_why(self):
        """An empty walk and a walk that never started are not one answer."""
        coverage = Coverage()
        coverage.record(
            REACH,
            "graph",
            answered=False,
            reason="nothing said which graph to read for this repository",
        )

        said = read_and_unread(coverage)

        assert "Nothing outside this repository was read" in said
        assert "nothing said which graph to read" in said

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
            Asks("acme/web", "acme/billing"),
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
            changes(),
            services,
            None,
            None,
            None,
            reaching("acme/web"),
            coverage,
            Asks("acme/web"),
        )

        assert "search failed" in section
        assert not coverage.answered(SIBLING_SOURCE, "acme/web")
        assert not coverage.answered(NEIGHBOURING_CODE)


class TestASystemWithNoCodeIsNotAGap:
    """A system somebody drew to group repositories holds no code of its own.

    Reported as unread it sits in the warning permanently, and a warning that
    is always on is read as noise.
    """

    def drawn_by_hand(self):
        return ChangeContext(
            scope=HERE,
            impact=ChangeImpact(
                TopologySubgraph(
                    (
                        TopologyEntity(
                            "system:stack",
                            "system",
                            "approved",
                            properties={"name": "Acme stack"},
                        ),
                    ),
                    (),
                    False,
                ),
                (),
                (),
                False,
            ),
        )

    def test_it_is_not_listed_as_unread(self):
        class _Searcher:
            def search_text(self, query):
                return CodeTextResult()

        services = ServiceRegistry()
        services.register(CodeTextSearcher, _Searcher(), "test")
        coverage = Coverage()
        model = Asks("acme/api")

        related_code_section(
            changes(),
            services,
            None,
            None,
            None,
            self.drawn_by_hand(),
            coverage,
            model,
        )

        assert "Acme stack" not in model.asked
        assert coverage.unread == ()
        assert not coverage.answered(SIBLING_SOURCE, "Acme stack")

    def test_the_reader_is_not_warned_about_it(self):
        coverage = Coverage()
        coverage.record(REACH, "graph", answered=True)
        coverage.reaches(("Acme stack",))
        coverage.record(
            SIBLING_SOURCE,
            NOTHING,
            answered=False,
            target="Acme stack",
            reason="holds no code of its own",
        )

        assert "Not read" not in read_and_unread(coverage)
