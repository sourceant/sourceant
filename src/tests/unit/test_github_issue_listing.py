import pytest

from src.integrations.github.github import GitHub


@pytest.fixture
def github(monkeypatch):
    client = GitHub.__new__(GitHub)
    seen = {}

    def _search(owner, repo, query, max_pages):
        seen["owner"] = owner
        seen["repo"] = repo
        seen["query"] = query
        return []

    monkeypatch.setattr(client, "_search_issues", _search)
    client.seen = seen
    return client


def test_pull_requests_are_left_out(github):
    github.list_issues("acme", "billing")

    assert github.seen["query"] == "repo:acme/billing is:issue"


def test_every_state_is_asked_for_by_default(github):
    github.list_issues("acme", "billing")

    assert "is:open" not in github.seen["query"]
    assert "is:closed" not in github.seen["query"]


def test_one_state_can_be_asked_for(github):
    github.list_issues("acme", "billing", state="closed")

    assert "is:closed" in github.seen["query"]


def test_several_labels_mean_any_of_them_not_all(github):
    github.list_issues("acme", "billing", labels=("requirement", "acceptance-criteria"))

    assert 'label:"requirement","acceptance-criteria"' in github.seen["query"]
    assert github.seen["query"].count("label:") == 1


def test_a_label_containing_a_space_stays_one_label(github):
    github.list_issues("acme", "billing", labels=("needs review",))

    assert 'label:"needs review"' in github.seen["query"]


def test_no_labels_means_no_label_qualifier(github):
    github.list_issues("acme", "billing", labels=())

    assert "label:" not in github.seen["query"]


def test_an_unknown_state_is_refused(github):
    with pytest.raises(ValueError):
        github.list_issues("acme", "billing", state="stale")
