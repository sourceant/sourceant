"""What the overview says about the systems a change reaches.

Only what a reader can act on. A system reached, searched and quiet says
nothing worth a line, and a section listing everything is read as a list of
nothing.
"""

from src.core.review_coverage import (
    Coverage,
    KEYWORD,
    NOTHING,
    REACH,
    SIBLING_SOURCE,
    systems_read,
)
from src.models.code_review import CodeSuggestion, Side, SuggestionCategory


def suggestion(comment, file_name="src/api.py"):
    return CodeSuggestion(
        file_name=file_name,
        start_line=1,
        end_line=1,
        side=Side.RIGHT,
        category=SuggestionCategory.BUG,
        comment=comment,
        suggested_code=None,
    )


def reaching(*names):
    coverage = Coverage()
    coverage.record(REACH, "graph", answered=True)
    coverage.reaches(names)
    return coverage


class TestWhenItAppears:
    def test_nothing_found_and_nothing_said_writes_no_section(self):
        coverage = reaching("acme/web")
        coverage.record(SIBLING_SOURCE, KEYWORD, answered=True, target="acme/web")

        assert systems_read(coverage) == ""

    def test_a_change_reaching_nothing_writes_no_section(self):
        assert systems_read(reaching()) == ""

    def test_code_found_in_a_system_puts_it_in(self):
        coverage = reaching("acme/web")
        coverage.record(
            SIBLING_SOURCE,
            KEYWORD,
            answered=True,
            target="acme/web",
            found=("web/checkout.ts", "web/order.ts"),
        )

        said = systems_read(coverage)

        assert "acme/web" in said
        assert "`web/checkout.ts`" in said
        assert "`web/order.ts`" in said

    def test_a_finding_naming_a_system_puts_it_in(self):
        coverage = reaching("acme/web")
        coverage.record(SIBLING_SOURCE, KEYWORD, answered=True, target="acme/web")

        said = systems_read(
            coverage,
            [suggestion("This removes a field acme/web still reads.")],
        )

        assert "acme/web" in said
        assert "still reads" in said
        assert "`src/api.py`" in said


class TestWhichSystemsItNames:
    def test_a_system_holding_no_code_is_left_out(self):
        """Drawn by hand to group repositories, so there is nothing to find."""
        coverage = reaching("Acme stack")
        coverage.record(
            SIBLING_SOURCE,
            NOTHING,
            answered=False,
            target="Acme stack",
            reason="holds no code of its own",
        )

        assert systems_read(coverage, [suggestion("Acme stack is affected.")]) == ""

    def test_only_the_systems_with_something_to_show(self):
        coverage = reaching("acme/web", "acme/billing")
        coverage.record(
            SIBLING_SOURCE,
            KEYWORD,
            answered=True,
            target="acme/web",
            found=("web/checkout.ts",),
        )
        coverage.record(SIBLING_SOURCE, KEYWORD, answered=True, target="acme/billing")

        said = systems_read(coverage)

        assert "acme/web" in said
        assert "acme/billing" not in said

    def test_many_files_are_counted_rather_than_listed(self):
        coverage = reaching("acme/web")
        coverage.record(
            SIBLING_SOURCE,
            KEYWORD,
            answered=True,
            target="acme/web",
            found=tuple(f"web/file{index}.ts" for index in range(8)),
        )

        said = systems_read(coverage)

        assert "and 3 more" in said
