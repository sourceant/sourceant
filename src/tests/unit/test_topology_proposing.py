"""A connection a model says it read is kept only where the lines say so."""

from __future__ import annotations

import json

from src.core.topology.proposing import (
    CITED,
    MAX_ROUNDS,
    CORROBORATED,
    MAX_READS,
    PROPOSE,
    READ_FILE,
    SEARCH_CODE,
    Reading,
    WhatItReads,
    check,
)

CLIENT = """import httpx


def charge(amount):
    return httpx.post("https://billing.internal/charges", json={"amount": amount})
"""

FILES = {"src/billing.py": CLIENT}


def _read(path):
    return FILES.get(path)


def _nothing(terms):
    return ()


def _call(identifier, name, **arguments):
    return {"id": identifier, "name": name, "arguments": json.dumps(arguments)}


def _proposal(**overrides):
    asked = {
        "target": "billing",
        "kind": "consumes",
        "path": "src/billing.py",
        "start_line": 5,
        "end_line": 5,
        "quote": 'httpx.post("https://billing.internal/charges"',
        "because": "It posts charges to billing.",
    }
    asked.update(overrides)
    return asked


class Model:
    """Answers with the tool calls it was built with, one round at a time."""

    def __init__(self, *rounds):
        self.rounds = list(rounds)
        self.asked = []

    def ask_with_tools(self, messages, tools, *, purpose="", require=False):
        self.asked.append(messages[-1])
        if not self.rounds:
            return {"content": "done", "tool_calls": []}
        return {"content": "", "tool_calls": self.rounds.pop(0)}


def _reads(**kwargs):
    return WhatItReads(_read, _nothing, **kwargs)


class TestTheCitationDecides:
    def test_a_connection_whose_lines_say_so_is_proposed(self):
        model = Model([_call("1", PROPOSE, **_proposal())])

        proposed = _reads().propose(
            model,
            entity_id="checkout",
            repository="acme/checkout",
            targets=("billing",),
        )

        assert [(one.source_id, one.target_id, one.type) for one in proposed] == [
            ("checkout", "billing", "consumes")
        ]
        assert proposed[0].status == "pending"
        assert proposed[0].confidence == CITED

    def test_a_quote_the_file_does_not_carry_is_dropped(self):
        model = Model(
            [_call("1", PROPOSE, **_proposal(quote='httpx.post("https://ledger/")'))]
        )

        assert (
            _reads().propose(
                model,
                entity_id="checkout",
                repository="acme/checkout",
                targets=("billing",),
            )
            == ()
        )

    def test_a_quote_on_the_wrong_lines_is_dropped(self):
        model = Model([_call("1", PROPOSE, **_proposal(start_line=1, end_line=2))])

        assert (
            _reads().propose(
                model,
                entity_id="checkout",
                repository="acme/checkout",
                targets=("billing",),
            )
            == ()
        )

    def test_a_file_that_cannot_be_read_drops_the_proposal(self):
        model = Model([_call("1", PROPOSE, **_proposal(path="src/gone.py"))])

        assert (
            _reads().propose(
                model,
                entity_id="checkout",
                repository="acme/checkout",
                targets=("billing",),
            )
            == ()
        )

    def test_indentation_alone_does_not_drop_a_proposal(self):
        model = Model(
            [
                _call(
                    "1",
                    PROPOSE,
                    **_proposal(
                        quote='return httpx.post( "https://billing.internal/charges"'
                    ),
                )
            ]
        )

        proposed = _reads().propose(
            model,
            entity_id="checkout",
            repository="acme/checkout",
            targets=("billing",),
        )

        assert len(proposed) == 1


class TestWhatItMayName:
    def test_a_system_outside_the_list_is_refused(self):
        model = Model([_call("1", PROPOSE, **_proposal(target="ledger"))])

        assert (
            _reads().propose(
                model,
                entity_id="checkout",
                repository="acme/checkout",
                targets=("billing",),
            )
            == ()
        )

    def test_a_joint_a_manifest_would_declare_is_refused(self):
        model = Model([_call("1", PROPOSE, **_proposal(kind="depends_on"))])

        assert (
            _reads().propose(
                model,
                entity_id="checkout",
                repository="acme/checkout",
                targets=("billing",),
            )
            == ()
        )

    def test_nothing_is_proposed_without_systems_to_name(self):
        model = Model([_call("1", PROPOSE, **_proposal())])

        assert (
            _reads().propose(
                model, entity_id="checkout", repository="acme/checkout", targets=()
            )
            == ()
        )
        assert model.asked == []


class TestWhatTheIndexAdds:
    def test_a_reading_the_index_agrees_with_carries_more_confidence(self):
        model = Model([_call("1", PROPOSE, **_proposal())])
        reads = WhatItReads(_read, _nothing, lambda source, target: True)

        proposed = reads.propose(
            model,
            entity_id="checkout",
            repository="acme/checkout",
            targets=("billing",),
        )

        assert proposed[0].confidence == CORROBORATED
        assert proposed[0].properties["corroborated"] is True

    def test_an_index_that_cannot_answer_leaves_the_reading_standing(self):
        def broken(source, target):
            raise RuntimeError("the index is unreachable")

        model = Model([_call("1", PROPOSE, **_proposal())])

        proposed = WhatItReads(_read, _nothing, broken).propose(
            model,
            entity_id="checkout",
            repository="acme/checkout",
            targets=("billing",),
        )

        assert proposed[0].confidence == CITED


class Watching(Model):
    """Keeps what came back from each tool, which is all the model ever sees."""

    def __init__(self, *rounds):
        Model.__init__(self, *rounds)
        self.answered = []

    def ask_with_tools(self, messages, tools, *, purpose="", require=False):
        self.answered.extend(
            one["content"] for one in messages if one["role"] == "tool"
        )
        return Model.ask_with_tools(
            self, messages, tools, purpose=purpose, require=require
        )


class TestReading:
    def test_a_file_comes_back_with_its_line_numbers(self):
        model = Watching([_call("1", READ_FILE, path="src/billing.py")])

        _reads().propose(
            model,
            entity_id="checkout",
            repository="acme/checkout",
            targets=("billing",),
        )

        assert any("5: " in one for one in model.answered)

    def test_a_file_that_is_not_there_says_so_rather_than_answering_nothing(self):
        model = Watching([_call("1", READ_FILE, path="src/gone.py")])

        _reads().propose(
            model,
            entity_id="checkout",
            repository="acme/checkout",
            targets=("billing",),
        )

        assert any("not readable" in one for one in model.answered)

    def test_reading_stops_at_the_budget(self):
        rounds = [
            [_call(str(number), READ_FILE, path="src/billing.py")]
            for number in range(MAX_READS + 4)
        ]
        model = Model(*rounds)

        _reads(rounds=MAX_READS + 4).propose(
            model,
            entity_id="checkout",
            repository="acme/checkout",
            targets=("billing",),
        )

        assert len(model.asked) <= MAX_READS

    def test_a_search_answers_from_what_it_was_given(self):
        found = [{"path": "src/billing.py", "start_line": 5, "text": "billing"}]
        model = Watching([_call("1", SEARCH_CODE, terms=["billing"])])

        WhatItReads(_read, lambda terms: found).propose(
            model,
            entity_id="checkout",
            repository="acme/checkout",
            targets=("billing",),
        )

        assert any("src/billing.py" in one for one in model.answered)


class TestWhatComesBack:
    def test_the_same_joint_read_twice_is_one_proposal(self):
        model = Model(
            [
                _call("1", PROPOSE, **_proposal()),
                _call(
                    "2",
                    PROPOSE,
                    **_proposal(
                        quote="https://billing.internal/charges",
                        start_line=5,
                        end_line=5,
                    ),
                ),
            ]
        )

        proposed = _reads().propose(
            model,
            entity_id="checkout",
            repository="acme/checkout",
            targets=("billing",),
        )

        assert len(proposed) == 1
        assert len(proposed[0].evidence) == 2

    def test_a_proposal_names_the_lines_it_was_read_from(self):
        model = Model([_call("1", PROPOSE, **_proposal())])

        proposed = _reads().propose(
            model,
            entity_id="checkout",
            repository="acme/checkout",
            targets=("billing",),
            revision="abc123",
        )
        evidence = proposed[0].evidence[0]

        assert evidence.source == "acme/checkout/src/billing.py#L5-L5"
        assert evidence.kind == "reading"
        assert evidence.revision == "abc123"
        assert evidence.properties["quote"] in CLIENT

    def test_a_model_that_cannot_be_asked_says_so_rather_than_answering_nothing(self):
        class Plain:
            pass

        reads = _reads()

        assert (
            reads.propose(
                Plain(),
                entity_id="checkout",
                repository="acme/checkout",
                targets=("billing",),
            )
            == ()
        )
        assert reads.refused

    def test_a_model_that_falls_over_is_not_a_reading_that_found_nothing(self):
        class Falls:
            def ask_with_tools(self, *args, **kwargs):
                raise RuntimeError("the provider timed out")

        reads = _reads()

        assert (
            reads.propose(
                Falls(),
                entity_id="checkout",
                repository="acme/checkout",
                targets=("billing",),
            )
            == ()
        )
        assert reads.refused


class TestCheckOnItsOwn:
    def test_lines_past_the_end_of_a_file_do_not_read_back(self):
        reading = Reading("billing", "consumes", "src/billing.py", 90, 99, "anything")

        assert check(reading, CLIENT).kept is False

    def test_an_empty_quote_proves_nothing(self):
        reading = Reading("billing", "consumes", "src/billing.py", 1, 1, "")

        assert check(reading, CLIENT).kept is False


class _Endless:
    """A model that keeps asking, the way one mid-search does."""

    def __init__(self):
        self.asked = 0

    def ask_with_tools(self, messages, tools, *, purpose="", require=False):
        self.asked += 1
        return {
            "content": "",
            "tool_calls": [
                _call(str(self.asked), SEARCH_CODE, terms=["billing"]),
            ],
        }


def test_a_reading_cut_off_mid_search_says_it_did_not_finish():
    """Recorded as read, a repository nobody finished is never read again."""
    reads = WhatItReads(_read, _nothing, rounds=3)

    reads.propose(
        _Endless(),
        entity_id="checkout",
        repository="acme/checkout",
        targets=("billing",),
    )

    assert reads.unfinished
    assert not reads.refused


def test_a_model_that_stops_on_its_own_has_finished():
    class Stops:
        def ask_with_tools(self, messages, tools, *, purpose="", require=False):
            return {"content": "done", "tool_calls": []}

    reads = WhatItReads(_read, _nothing)

    reads.propose(
        Stops(), entity_id="checkout", repository="acme/checkout", targets=("billing",)
    )

    assert not reads.unfinished


def test_the_rounds_allow_the_reading_the_model_is_promised():
    """The prompt offers MAX_READS reads, so the loop has to permit them."""
    assert MAX_ROUNDS > MAX_READS
