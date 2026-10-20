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
