from src.core.search import changed_code_terms

RENAME = (
    "--- a/api.py\n"
    "+++ b/api.py\n"
    "@@\n"
    '-    priority: str = Field(default="", max_length=255)\n'
    '+    importance: str = Field(default="", max_length=255)\n'
)


def test_a_renamed_name_is_searched_for_under_the_name_it_lost():
    """The callers about to break still use the old name, so it leads."""
    terms = changed_code_terms(RENAME)

    assert terms[0] == "priority"
    assert "importance" in terms


def test_a_pure_addition_still_searches_for_what_it_added():
    diff = (
        "--- a/api.py\n+++ b/api.py\n@@\n+def rebalance(ledger):\n+    return ledger\n"
    )

    assert "rebalance" in changed_code_terms(diff)


def test_the_file_headers_are_not_read_as_changed_code():
    assert "api" not in changed_code_terms(RENAME)


def test_what_the_hunk_was_written_in_is_not_what_changed():
    """`Field`, `default` and `max` stand unchanged on both sides of a rename."""
    terms = changed_code_terms(RENAME)

    assert set(terms) == {"priority", "importance"}
