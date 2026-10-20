from pathlib import Path

from src.core.review.impact import impact
from src.core.review.town import town
from src.tests.unit.test_review_impact import _new_side

FIXTURES = Path(__file__).parent.parent / "fixtures/review-overview"


def _measured():
    diff = (FIXTURES / "full.diff").read_text()
    return impact(diff, "sourceant/sourceant", _new_side(diff).get)


def test_a_change_becomes_buildings_with_its_new_definitions():
    text = town(
        _measured(),
        title="feat(topology): Suggest systems from related repositories",
        name="sourceant/sourceant#42",
    )

    assert text.splitlines()[:3] == [
        'system "sourceant/sourceant#42"',
        'intent "Suggest systems from related repositories"',
        'purpose feat "Suggest systems from related repositories"',
    ]
    assert '+ service core_topology "src/core/topology"' in text
    assert "  + function root" in text
    # Migrations are code too, in their own district.
    assert 'district migrations "migrations"' in text


def test_findings_and_behaviors_land_on_the_definition_they_name():
    review = {
        "summary": {
            "intent": "Group related repositories into systems",
            "behaviors": [
                {
                    "subject": "Suggestions",
                    "before": "",
                    "after": "Related repositories are proposed as one system.",
                    "locations": ["src/core/topology/suggestions.py:10"],
                }
            ],
        },
        "code_suggestions": [
            {
                "file_name": "src/core/topology/suggestions.py",
                "start_line": 11,
                "comment": "The loop never ends on a cycle. Track visited names.",
                "severity": "blocking",
                "impact": "hang",
                "certainty": "conditional",
                "blast": "many",
                "trigger": "service",
            },
            {
                "file_name": "src/core/topology/suggestions.py",
                "start_line": 40,
                "comment": "Rename this.",
                "severity": "nit",
            },
        ],
    }

    text = town(_measured(), review, title="Group repositories")

    assert 'intent "Group related repositories into systems"' in text
    assert "purpose" not in text
    assert (
        'behavior B1 at core_topology.root "Did not exist" -> "Related repositories are proposed as one system."'
        in text
    )
    assert (
        'risk 1 at core_topology.root "The loop never ends on a cycle." impact=hang certainty=conditional blast=many trigger=service'
        in text
    )
    assert "Rename this" not in text


def test_every_word_written_is_one_the_library_defines():
    from src.core.review.codetown import vocabulary

    known = {one["keyword"] for one in vocabulary()["concepts"]} | set(
        vocabulary()["statements"]
    )
    text = town(_measured(), title="feat(topology): Suggest systems")
    for line in text.splitlines():
        words = line.split()
        if not words:
            continue
        head = words[1] if words[0] in vocabulary()["changes"] else words[0]
        assert head in known or words[1] in known, line


def test_a_word_the_library_lacks_fails_loudly():
    import pytest

    from src.core.review.codetown import word

    with pytest.raises(KeyError, match="no word 'skyscraper'"):
        word("skyscraper")


def test_a_folder_is_drawn_as_what_its_name_says_it_is():
    from src.core.review.town import _kind

    assert _kind(".github/workflows") == ("service", "process")
    assert _kind("src/memory/migrations") == ("database", None)
    assert _kind("src/memory") == ("database", None)
    assert _kind("src/memory/routes") == ("service", "endpoint")
    assert _kind("src/app/jobs") == ("service", "job")
    assert _kind("src/app/events") == ("queue", None)
    assert _kind("src/core/topology") == ("service", None)
