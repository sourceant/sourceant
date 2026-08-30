"""Reading what a repository already says about itself."""

from pathlib import Path

from src.core.knowledge.seeding import from_markdown, read

ADR = """# Retry charges three times

## Context

The provider rate limits after four attempts in a minute.

## Decision

A failed charge is retried three times and then left alone.

## Consequences

A charge can be left unresolved for a person to pick up.
"""

GUIDE = """# Contributing

## Installation

Run make up.

## Conventions

Every route answers in the standard envelope.

## Constraints

The reviewer never writes to a repository it was not invited to.

## Notes

Nothing in particular.
"""


class TestRecords:
    """A decision record is one decision, whatever headings it happens to use."""

    def test_the_decision_is_what_it_decided(self):
        seeds = from_markdown("docs/adr/0001-retry-charges.md", ADR)

        assert len(seeds) == 1
        assert seeds[0].knowledge.summary == (
            "A failed charge is retried three times and then left alone."
        )

    def test_the_context_is_kept_as_why(self):
        seeds = from_markdown("docs/adr/0001-retry-charges.md", ADR)

        assert seeds[0].knowledge.properties["why"] == (
            "The provider rate limits after four attempts in a minute."
        )

    def test_it_points_back_at_where_it_came_from(self):
        seeds = from_markdown("docs/adr/0001-retry-charges.md", ADR)

        assert (
            seeds[0].knowledge.properties["source"] == "docs/adr/0001-retry-charges.md"
        )
        assert seeds[0].knowledge.properties["title"] == "Retry charges three times"

    def test_a_record_with_no_decision_heading_falls_back_to_what_it_opens_with(self):
        seeds = from_markdown("docs/decisions/queues.md", "# Queues\n\nWe use Redis.\n")

        assert seeds[0].knowledge.summary == "We use Redis."


class TestHeadings:
    """Anywhere else, a heading has to say what kind it is."""

    def test_a_conventions_heading_is_a_convention(self):
        seeds = {
            seed.knowledge.kind: seed
            for seed in from_markdown("CONTRIBUTING.md", GUIDE)
        }

        assert seeds["convention"].knowledge.summary == (
            "Every route answers in the standard envelope."
        )

    def test_a_constraints_heading_is_a_constraint(self):
        seeds = {
            seed.knowledge.kind: seed
            for seed in from_markdown("CONTRIBUTING.md", GUIDE)
        }

        assert "constraint" in seeds

    def test_a_heading_that_names_no_kind_is_left_alone(self):
        """Otherwise the store fills up with headings like Installation."""
        headings = {
            seed.knowledge.properties["heading"]
            for seed in from_markdown("CONTRIBUTING.md", GUIDE)
        }

        assert "Installation" not in headings
        assert "Notes" not in headings

    def test_a_heading_with_nothing_under_it_is_not_knowledge(self):
        seeds = from_markdown(
            "CONTRIBUTING.md", "## Conventions\n\n## Something else\n"
        )

        assert seeds == []


class TestNothingIsAgreedYet:
    def test_everything_comes_back_proposed(self):
        """Nobody has agreed to any of it: it was read off a file."""
        seeds = from_markdown("docs/adr/0001-retry.md", ADR) + from_markdown(
            "CONTRIBUTING.md", GUIDE
        )

        assert {seed.knowledge.status for seed in seeds} == {"proposed"}


class TestReadingARepository:
    def write(self, root: Path, path: str, text: str):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def test_it_reads_the_places_projects_write_these_down(self, tmp_path):
        self.write(tmp_path, "docs/adr/0001-retry.md", ADR)
        self.write(tmp_path, "CONTRIBUTING.md", GUIDE)
        self.write(tmp_path, "src/main.py", "print('not knowledge')\n")

        seeds = read(tmp_path)

        assert {seed.path for seed in seeds} == {
            "docs/adr/0001-retry.md",
            "CONTRIBUTING.md",
        }

    def test_a_repository_that_says_nothing_yields_nothing(self, tmp_path):
        self.write(tmp_path, "README.md", "# Billing\n\nA service.\n")

        assert read(tmp_path) == []

    def test_the_same_thing_twice_is_recorded_once(self, tmp_path):
        self.write(tmp_path, "docs/adr/0001-retry.md", ADR)
        self.write(tmp_path, "docs/decisions/0001-retry.md", ADR)

        seeds = read(tmp_path)

        assert len(seeds) == 1
