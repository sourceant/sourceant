"""What a review asks to look at.

The words worth searching for are usually not in the diff. The rule that used
to pick them read whole diff lines, so deleted comment prose became the query
and other repositories were searched for words like "almost" and "narrower".
"""

import json

import pytest

from src.core.review_search import MAX_SEARCHES, WhatToLookFor
from src.core.scope import Scope
from src.core.search import SearchMatch, SearchResult


def scope_for(repository):
    return Scope.from_mapping({"repository": repository})


class Found:
    def __init__(self, result=None):
        self.asked = []
        self._result = result or SearchResult(
            (SearchMatch("web/app.ts", "r2", 1, 2, "rebalance()", "acme/web"),)
        )

    def search(self, query):
        self.asked.append((str(query.scope.get("repository")), query.terms))
        return self._result


def answering(*rounds):
    """A model that answers each round with the calls it was given."""

    class _Model:
        def __init__(self):
            self.rounds = list(rounds)
            self.seen = []

        def ask_with_tools(self, messages, tools, *, purpose="tools", require=False):
            self.seen.append(messages[-1])
            if not self.rounds:
                return {"content": "", "tool_calls": []}
            calls = self.rounds.pop(0)
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call-{index}",
                        "name": "search_code",
                        "arguments": json.dumps(one),
                    }
                    for index, one in enumerate(calls)
                ],
            }

    return _Model()


class TestTheModelChoosesTheWords:
    def test_it_searches_what_the_review_asked_for(self):
        searcher = Found()
        model = answering([{"repository": "acme/web", "terms": ["rebalance"]}])

        asked = WhatToLookFor(searcher, scope_for).gather(
            model, change="{}", repositories=("acme/web",)
        )

        assert searcher.asked == [("acme/web", ("rebalance",))]
        assert asked[0].matches[0]["path"] == "web/app.ts"

    def test_it_can_come_back_for_more(self):
        """The second search is the one the first result suggests."""
        searcher = Found()
        model = answering(
            [{"repository": "acme/web", "terms": ["rebalance"]}],
            [{"repository": "acme/web", "terms": ["settlement"]}],
        )

        asked = WhatToLookFor(searcher, scope_for).gather(
            model, change="{}", repositories=("acme/web",)
        )

        assert [terms for _, terms in searcher.asked] == [
            ("rebalance",),
            ("settlement",),
        ]
        assert len(asked) == 2

    def test_what_it_found_is_given_back_to_it(self):
        searcher = Found()
        model = answering([{"repository": "acme/web", "terms": ["rebalance"]}])

        WhatToLookFor(searcher, scope_for).gather(
            model, change="{}", repositories=("acme/web",)
        )

        answered = [one for one in model.seen if one.get("role") == "tool"]
        assert answered
        assert "web/app.ts" in answered[0]["content"]


class TestItCannotWidenWhatItWasGiven:
    def test_a_repository_outside_the_list_is_refused(self):
        """The list is fixed before the model is asked."""
        searcher = Found()
        model = answering([{"repository": "acme/secrets", "terms": ["token"]}])

        asked = WhatToLookFor(searcher, scope_for).gather(
            model, change="{}", repositories=("acme/web",)
        )

        assert searcher.asked == []
        assert "not one this change reaches" in asked[0].unavailable

    def test_it_stops_after_enough_searches(self):
        searcher = Found()
        model = answering(
            *[[{"repository": "acme/web", "terms": ["one"]}] for _ in range(6)]
        )

        asked = WhatToLookFor(searcher, scope_for, rounds=6).gather(
            model, change="{}", repositories=("acme/web",)
        )

        assert len(asked) <= MAX_SEARCHES

    def test_a_term_the_query_refuses_is_reported_not_raised(self):
        searcher = Found()
        model = answering([{"repository": "acme/web", "terms": ["no"]}])

        asked = WhatToLookFor(searcher, scope_for).gather(
            model, change="{}", repositories=("acme/web",)
        )

        assert searcher.asked == []
        assert asked[0].unavailable


class TestWhenItCannotBeAsked:
    def test_a_provider_that_takes_no_tools_asks_nothing(self):
        """Older providers answer a prompt and nothing else."""

        class _Plain:
            pass

        assert (
            WhatToLookFor(Found(), scope_for).gather(
                _Plain(), change="{}", repositories=("acme/web",)
            )
            == ()
        )

    def test_a_provider_that_fails_does_not_end_the_review(self):
        class _Fails:
            def ask_with_tools(self, *args, **kwargs):
                raise RuntimeError("the provider refused")

        assert (
            WhatToLookFor(Found(), scope_for).gather(
                _Fails(), change="{}", repositories=("acme/web",)
            )
            == ()
        )

    def test_nothing_reached_means_nothing_asked(self):
        model = answering([{"repository": "acme/web", "terms": ["rebalance"]}])

        assert (
            WhatToLookFor(Found(), scope_for).gather(
                model, change="{}", repositories=()
            )
            == ()
        )


class TestARepositoryPassedOverIsNamed:
    """Asked once, a review searches where it looked first and stops.

    The rest are reported unread, so anything broken in them goes unmentioned.
    Naming them makes the omission a decision.
    """

    def test_it_is_asked_again_about_what_it_skipped(self):
        searcher = Found()
        model = answering([{"repository": "acme/web", "terms": ["rebalance"]}])

        asked = WhatToLookFor(searcher, scope_for).gather(
            model, change="{}", repositories=("acme/web", "acme/billing")
        )

        nudged = [one for one in model.seen if one.get("role") == "user"]
        assert any("acme/billing" in one["content"] for one in nudged[1:])
        assert len(asked) == 1

    def test_it_may_still_answer_with_nothing(self):
        """A repository nothing in the change can reach is rightly skipped."""
        searcher = Found()
        model = answering([{"repository": "acme/web", "terms": ["rebalance"]}])

        asked = WhatToLookFor(searcher, scope_for).gather(
            model, change="{}", repositories=("acme/web", "acme/billing")
        )

        assert [one.repository for one in asked] == ["acme/web"]

    def test_it_is_not_asked_twice_about_the_same_gap(self):
        searcher = Found()
        model = answering([])

        WhatToLookFor(searcher, scope_for, rounds=3).gather(
            model, change="{}", repositories=("acme/web",)
        )

        nudges = [
            one
            for one in model.seen
            if one.get("role") == "user" and "have not searched" in one["content"]
        ]
        assert len(nudges) == 1

    def test_searching_everything_asks_nothing_further(self):
        searcher = Found()
        model = answering(
            [
                {"repository": "acme/web", "terms": ["rebalance"]},
                {"repository": "acme/billing", "terms": ["rebalance"]},
            ]
        )

        WhatToLookFor(searcher, scope_for).gather(
            model, change="{}", repositories=("acme/web", "acme/billing")
        )

        assert not [
            one
            for one in model.seen
            if one.get("role") == "user" and "have not searched" in one["content"]
        ]
