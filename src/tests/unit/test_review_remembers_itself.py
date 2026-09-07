"""A review that reads its own last word before writing the next one.

Line comments were read back so a suggestion was not repeated. The summary,
which is where the reviewer states its position on the change as a whole, was
never read back at all, so one pass could ask for the opposite of what an
earlier pass asked for and neither pass could tell.
"""

from src.integrations.github.github import COMMENT_MARKER
from src.llms.litellm_provider import LiteLLMProvider


def test_nothing_is_said_about_a_pull_request_reviewed_for_the_first_time():
    assert LiteLLMProvider._format_previous_review(None) == ""
    assert LiteLLMProvider._format_previous_review("") == ""


def test_what_was_said_before_is_put_in_front_of_the_next_reading():
    said = LiteLLMProvider._format_previous_review(
        "Make the two defaults agree with each other."
    )

    assert "Make the two defaults agree with each other." in said
    assert "Do not contradict" in said


def test_the_summary_is_read_back_without_the_marker_that_finds_it(monkeypatch):
    """The marker is how the comment is recognised again. It is machinery, and
    putting it in front of a model is noise."""
    from src.integrations.github import github as adapter

    found = {"body": f"# Code Review Summary\n\nSaid before.\n{COMMENT_MARKER}"}
    reader = adapter.GitHub.__new__(adapter.GitHub)
    monkeypatch.setattr(
        adapter.GitHub, "get_installation_access_token", lambda *a, **k: "a-token"
    )
    monkeypatch.setattr(adapter.GitHub, "_find_overview_comment", lambda *a, **k: found)

    summary = adapter.GitHub.get_previous_review_summary(reader, "acme", "billing", 1)

    assert "Said before." in summary
    assert COMMENT_MARKER not in summary


def test_a_pull_request_with_no_summary_yet_says_nothing(monkeypatch):
    from src.integrations.github import github as adapter

    reader = adapter.GitHub.__new__(adapter.GitHub)
    monkeypatch.setattr(
        adapter.GitHub, "get_installation_access_token", lambda *a, **k: "a-token"
    )
    monkeypatch.setattr(adapter.GitHub, "_find_overview_comment", lambda *a, **k: None)

    assert (
        adapter.GitHub.get_previous_review_summary(reader, "acme", "billing", 1) is None
    )
