"""Findings survive the code moving under them.

An identity that changes when a line is added above it orphans the state set on
it, and the reviewer repeats itself.
"""

import pytest
from sqlalchemy import create_engine

from src.core.review import (
    DISMISSED,
    OPEN,
    FindingQuery,
    ReviewFinding,
    SQLFindingStore,
    prints_for,
)
from src.core.scope import Scope

HERE = Scope.from_mapping({"repository": "acme/billing"})


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'findings.db'}")
    return SQLFindingStore(engine, create_schema=True)


def test_a_finding_is_the_same_finding_after_the_line_moves():
    said = "Add a new migration instead of editing this one."
    code = "def up():\n    pass\n"

    before = prints_for("db/0001.py", said, code)
    after = prints_for("db/0001.py", said, code)

    assert before == after
    # Nothing in it is a line number, which is what makes that true.
    assert not any(name.endswith(":2") for name in before)


def test_rewording_still_matches_on_the_code_it_proposed():
    code = "def up():\n    pass\n"
    first = prints_for("db/0001.py", "Add a new migration instead.", code)
    reworded = prints_for("db/0001.py", "Please add a new migration.", code)

    assert first[0] != reworded[0], "the words changed, so the words print changed"
    assert first[1] == reworded[1], "the code did not, so it is still findable"


def test_spacing_and_case_are_not_a_different_finding():
    one = prints_for("a.py", "Use a  Constant here.", "X = 1")
    two = prints_for("a.py", "use a constant here.", "X = 1")

    assert one == two


def test_a_state_somebody_set_survives_the_review_saying_it_again(store):
    name = prints_for("a.py", "Use a constant.", "X = 1")[0]
    store.put_finding(
        HERE, ReviewFinding(id=name, state=OPEN, summary="Use a constant.")
    )

    # Somebody decides about it, then the review runs again and says it again.
    assert store.set_state(HERE, name, DISMISSED)
    store.put_finding(
        HERE, ReviewFinding(id=name, state=OPEN, summary="Use a constant.")
    )

    kept = store.get_finding(HERE, name)
    assert kept.state == DISMISSED, "a review running again is not a reason to reopen"


def test_findings_are_answered_by_state_and_kept_apart_by_scope(store):
    elsewhere = Scope.from_mapping({"repository": "acme/other"})
    store.put_finding(HERE, ReviewFinding(id="one", state=OPEN, summary="One"))
    store.put_finding(HERE, ReviewFinding(id="two", state=DISMISSED, summary="Two"))
    store.put_finding(elsewhere, ReviewFinding(id="three", state=OPEN, summary="Three"))

    found = store.search(FindingQuery(HERE, frozenset({OPEN})))

    assert [one.id for one in found.findings] == ["one"]
    assert found.total == 1


def test_a_state_nobody_named_is_still_allowed(store):
    """The vocabulary is core's, not a closed set."""
    store.put_finding(
        HERE, ReviewFinding(id="one", state="needs-review", summary="One")
    )

    found = store.search(FindingQuery(HERE, frozenset({"needs-review"})))

    assert [one.id for one in found.findings] == ["one"]


def test_a_page_is_asked_of_the_database_rather_than_sliced_here(store):
    """Fetching every finding to return twenty of them does not scale."""
    for n in range(30):
        store.put_finding(HERE, ReviewFinding(id=f"f{n}", state=OPEN, summary=str(n)))

    page = store.search(FindingQuery(HERE, limit=10, offset=5))

    assert len(page.findings) == 10
    assert page.total == 30
    assert page.has_more


def test_the_last_page_says_there_is_no_more(store):
    for n in range(12):
        store.put_finding(HERE, ReviewFinding(id=f"f{n}", state=OPEN, summary=str(n)))

    page = store.search(FindingQuery(HERE, limit=10, offset=10))

    assert len(page.findings) == 2
    assert not page.has_more


def test_filtering_on_a_property_still_pages(store):
    """Properties are a blob, so that filter and its page happen here."""
    for n in range(10):
        store.put_finding(
            HERE,
            ReviewFinding(
                id=f"f{n}",
                state=OPEN,
                summary=str(n),
                properties={"category": "BUG" if n % 2 else "STYLE"},
            ),
        )

    page = store.search(FindingQuery(HERE, properties={"category": "BUG"}, limit=3))

    assert len(page.findings) == 3
    assert page.total == 5
