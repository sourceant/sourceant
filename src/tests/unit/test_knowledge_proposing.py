"""Asking a model for what a repository has not written down."""

import json

from src.core.knowledge.models import KnowledgeObject
from src.core.knowledge.proposing import propose

LAYOUT = ["src/billing/charge.py", "src/billing/ledger.py"]
PROSE = "# Billing\n\nCharges customers."


def answering(payload):
    """A model that answers with exactly this, and records what it was asked."""
    asked = {}

    def ask(prompt):
        asked["prompt"] = prompt
        return payload

    ask.asked = asked
    return ask


def one(**overrides):
    item = {
        "id": "retry-limit",
        "kind": "decision",
        "summary": "A failed charge is retried three times and then left alone.",
        "why": "The provider rate limits after four.",
    }
    item.update(overrides)
    return json.dumps([item])


class TestWhatComesBack:
    def test_a_proposal_becomes_knowledge(self):
        proposals = propose("acme/billing", LAYOUT, PROSE, [], answering(one()))

        assert len(proposals) == 1
        assert proposals[0].knowledge.id == "retry-limit"
        assert proposals[0].knowledge.kind == "decision"
        assert proposals[0].knowledge.properties["why"] == (
            "The provider rate limits after four."
        )

    def test_nothing_a_model_says_is_taken_as_agreed(self):
        proposals = propose("acme/billing", LAYOUT, PROSE, [], answering(one()))

        assert proposals[0].knowledge.status == "proposed"

    def test_it_says_a_model_said_it(self):
        proposals = propose(
            "acme/billing", LAYOUT, PROSE, [], answering(one()), model="a/model"
        )

        assert proposals[0].knowledge.properties["source"] == "model"
        assert proposals[0].knowledge.properties["model"] == "a/model"


class TestReadingTheAnswer:
    """Models fence JSON, preface it, and apologise around it."""

    def test_a_fenced_answer_is_read(self):
        proposals = propose(
            "acme/billing", LAYOUT, PROSE, [], answering(f"```json\n{one()}\n```")
        )

        assert len(proposals) == 1

    def test_an_answer_with_talking_around_it_is_read(self):
        payload = f"Sure! Here is what I found:\n{one()}\nHope that helps."

        assert len(propose("acme/billing", LAYOUT, PROSE, [], answering(payload))) == 1

    def test_an_answer_that_is_not_json_is_nothing(self):
        assert (
            propose("acme/billing", LAYOUT, PROSE, [], answering("I cannot help")) == []
        )

    def test_an_empty_array_is_nothing(self):
        assert propose("acme/billing", LAYOUT, PROSE, [], answering("[]")) == []


class TestRefusals:
    def test_inventory_is_not_knowledge(self):
        """A model asked what a repository knows will say it uses PostgreSQL."""
        payload = one(summary="This service uses PostgreSQL and Redis.")

        assert propose("acme/billing", LAYOUT, PROSE, [], answering(payload)) == []

    def test_a_kind_nobody_asked_for_is_dropped(self):
        assert (
            propose("acme/billing", LAYOUT, PROSE, [], answering(one(kind="vibe")))
            == []
        )

    def test_something_with_no_summary_is_dropped(self):
        assert (
            propose("acme/billing", LAYOUT, PROSE, [], answering(one(summary=""))) == []
        )

    def test_the_same_name_twice_is_taken_once(self):
        payload = json.dumps(
            [
                {
                    "id": "retry",
                    "kind": "decision",
                    "summary": "Charges retry three times.",
                },
                {
                    "id": "retry",
                    "kind": "decision",
                    "summary": "Charges retry three times.",
                },
            ]
        )

        assert len(propose("acme/billing", LAYOUT, PROSE, [], answering(payload))) == 1

    def test_it_is_told_what_is_already_recorded(self):
        """Otherwise it proposes what somebody already wrote down."""
        known = [
            KnowledgeObject(
                id="retry-limit",
                kind="decision",
                status="accepted",
                summary="Charges retry three times.",
            )
        ]
        ask = answering("[]")

        propose("acme/billing", LAYOUT, PROSE, known, ask)

        assert "Charges retry three times." in ask.asked["prompt"]

    def test_more_than_asked_for_is_cut(self):
        payload = json.dumps(
            [
                {
                    "id": f"thing-{n}",
                    "kind": "convention",
                    "summary": f"Rule {n} holds.",
                }
                for n in range(20)
            ]
        )

        assert (
            len(propose("acme/billing", LAYOUT, PROSE, [], answering(payload), limit=5))
            == 5
        )


class TestHowSureAModelSaidItWas:
    """Recorded rather than acted on: what is sure enough is somebody's policy."""

    def answering(self, entries):
        import json

        return lambda prompt: json.dumps(entries)

    def test_what_it_said_is_kept(self):
        proposals = propose(
            repository="acme/billing",
            layout=["app/charge.py"],
            prose="",
            known=(),
            ask=self.answering(
                [
                    {
                        "id": "retry-limit",
                        "kind": "decision",
                        "summary": "Charges retry three times, then stop.",
                        "why": "The provider rate limits after four.",
                        "sure": 0.9,
                    }
                ]
            ),
            model="a-model",
        )

        assert proposals[0].confidence == 0.9
        assert proposals[0].knowledge.properties["confidence"] == 0.9

    def test_saying_nothing_is_the_middle_rather_than_certain(self):
        proposals = propose(
            repository="acme/billing",
            layout=["app/charge.py"],
            prose="",
            known=(),
            ask=self.answering(
                [
                    {
                        "id": "retry-limit",
                        "kind": "decision",
                        "summary": "Charges retry three times, then stop.",
                        "why": "The provider rate limits after four.",
                    }
                ]
            ),
        )

        assert proposals[0].confidence == 0.5

    def test_nonsense_is_the_middle_rather_than_an_error(self):
        proposals = propose(
            repository="acme/billing",
            layout=["app/charge.py"],
            prose="",
            known=(),
            ask=self.answering(
                [
                    {
                        "id": "retry-limit",
                        "kind": "decision",
                        "summary": "Charges retry three times, then stop.",
                        "why": "The provider rate limits after four.",
                        "sure": "very",
                    }
                ]
            ),
        )

        assert proposals[0].confidence == 0.5
