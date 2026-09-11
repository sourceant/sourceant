"""What a search may be asked for.

Terms used to be words: three or more letters and digits, nothing else. An
endpoint, a path and a snake_case name were all unaskable, and a search that
matched one on the line still threw the line away for not matching a word.
"""

import pytest

from src.core.search import CodeTextQuery, found_in
from src.core.scope import Scope

WHERE = Scope.from_mapping({"repository": "acme/api"})


class TestWhatCanBeAskedFor:
    @pytest.mark.parametrize(
        "term",
        [
            "rebalance",
            "reference_key",
            "/api/topology/infer",
            "ChangedCodeReference",
            "def rebalance(",
            "topology.infer",
        ],
    )
    def test_anything_a_reader_could_paste(self, term):
        assert CodeTextQuery(WHERE, (term,)).terms == (term,)

    @pytest.mark.parametrize("term", ["", "ab", "a\nb", "x" * 129, " padded"])
    def test_what_a_search_cannot_take(self, term):
        with pytest.raises(ValueError, match="one and sixteen"):
            CodeTextQuery(WHERE, (term,))

    def test_sixteen_is_still_the_most(self):
        with pytest.raises(ValueError, match="one and sixteen"):
            CodeTextQuery(WHERE, tuple(f"term{index}" for index in range(17)))


class TestWhatCountsAsFound:
    def test_a_path_is_found_in_the_line_that_carries_it(self):
        line = 'router.post("/api/topology/infer")'

        assert found_in(line, ("/api/topology/infer",)) == ("/api/topology/infer",)

    def test_a_snake_case_name_is_found_whole(self):
        assert found_in("    reference_key = hash(x)", ("reference_key",)) == (
            "reference_key",
        )

    def test_it_does_not_care_about_case(self):
        assert found_in("class ChangedCodeReference:", ("changedcodereference",))

    def test_a_term_the_line_does_not_carry_is_not_found(self):
        assert found_in("def rebalance(ledger):", ("settlement",)) == ()

    def test_every_term_present_counts(self):
        line = "from src.core.impact import ChangedCodeReference"

        assert len(found_in(line, ("impact", "ChangedCodeReference", "absent"))) == 2
