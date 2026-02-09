import pytest
import difflib
from textwrap import dedent
from pathlib import Path
from src.utils.diff_parser import parse_diff
from src.utils.line_mapper import LineMapper
from src.models.code_review import CodeSuggestion, Side, SuggestionCategory


# Helper to create tiered diff fixtures
def _create_diff_fixture(complexity: str):
    base_path = Path(__file__).parent
    data_path = base_path / "data"

    # Determine file extension based on available files
    if (data_path / f"{complexity}_before.dummy.py").exists():
        ext = "py"
        before_file = data_path / f"{complexity}_before.dummy.py"
        after_file = data_path / f"{complexity}_after.dummy.py"
    elif (data_path / f"{complexity}_before.dummy.js").exists():
        ext = "js"
        before_file = data_path / f"{complexity}_before.dummy.js"
        after_file = data_path / f"{complexity}_after.dummy.js"
    else:
        pytest.fail(f"No test files found for complexity '{complexity}'")

    file_v1 = before_file.read_text().splitlines(keepends=True)
    file_v2 = after_file.read_text().splitlines(keepends=True)

    # Use git-style diff headers but return clean file name for testing
    git_file_name = f"b/{complexity}_file.{ext}"
    diff_text = "".join(
        difflib.unified_diff(
            file_v1,
            file_v2,
            fromfile=f"a/{complexity}_file.{ext}",
            tofile=git_file_name,
        )
    )
    # Parser will normalize the path, so test should expect the clean version
    clean_file_name = f"{complexity}_file.{ext}"
    return parse_diff(diff_text), clean_file_name


@pytest.fixture(params=["simple", "medium", "large"])
def diff_data(request):
    """Parameterized fixture to provide diffs of varying complexity."""
    return _create_diff_fixture(request.param)


class TestLineMapper:
    def test_successful_match(self, diff_data):
        """Test a successful match on a line that was modified."""
        parsed_diffs, file_name = diff_data
        mapper = LineMapper(parsed_diffs)

        # Define test cases for each complexity level
        test_cases = {
            "simple_file.py": {
                "suggestion": CodeSuggestion(
                    file_name=file_name,
                    start_line=89,
                    end_line=89,
                    side=Side.RIGHT,
                    comment="Use a more specific error message.",
                    category=SuggestionCategory.REFACTOR,
                    suggested_code="""logging.error("Critical system initialization failure.")""",
                    existing_code="""logging.error("System initialization failed.")""",
                ),
                "expected_line": 89,
            },
            "medium_file.py": {
                "suggestion": CodeSuggestion(
                    file_name=file_name,
                    start_line=79,
                    end_line=79,
                    side=Side.RIGHT,
                    comment="Should log the update.",
                    category=SuggestionCategory.IMPROVEMENT,
                    suggested_code="""        logging.info(f"Updated email for {username}")""",
                    existing_code="""        self._save_users()""",
                ),
                "expected_line": 61,
            },
            "large_file.py": {
                "suggestion": CodeSuggestion(
                    file_name=file_name,
                    start_line=60,
                    end_line=60,
                    side=Side.RIGHT,
                    comment="Should be a more complex transformation.",
                    category=SuggestionCategory.REFACTOR,
                    suggested_code="""        return [{"id": item.get('id'), "value": item.get('value'), "processed_at": datetime.datetime.now().isoformat()} for item in data]""",
                    existing_code="""        return [{"transformed": True, **item} for item in data]""",
                ),
                "expected_line": 88,
            },
            "large_file.js": {
                "suggestion": CodeSuggestion(
                    file_name=file_name,
                    start_line=176,
                    end_line=176,
                    side=Side.RIGHT,
                    comment="This comment was modified.",
                    category=SuggestionCategory.IMPROVEMENT,
                    suggested_code="""    console.log('This line was modified from the original.');""",
                    existing_code="""        // This line was deleted. (Change 2: Line Deletion)""",
                ),
                "expected_line": 176,
            },
        }

        case = test_cases.get(file_name)
        if not case:
            pytest.skip(f"No test case for {file_name}")

        suggestion = case["suggestion"]
        expected_line = case["expected_line"]

        result = mapper.validate_and_map_suggestion(suggestion)
        assert result is not None
        mapping, reason = result
        # A successful match should not be a fallback.
        assert "fallback" not in reason.lower()

        assert mapping["line"] == expected_line
        assert mapping["side"] == "RIGHT"
        assert "position" in mapping

    def test_failed_match_fallback(self, diff_data):
        """Test fallback to line number when existing_code does not match."""
        parsed_diffs, _ = diff_data
        mapper = LineMapper(parsed_diffs)
        file_name = parsed_diffs[0].file_path

        suggestion = CodeSuggestion(
            file_name=file_name,
            start_line=5,
            end_line=5,
            side=Side.RIGHT,
            comment="This comment will use fallback.",
            category=SuggestionCategory.REFACTOR,
            suggested_code="print('hello')",
            existing_code="this code does not exist",
        )

        result = mapper.validate_and_map_suggestion(suggestion)
        assert result is not None
        mapping, reason = result
        assert reason != "Matched via existing_code"

        assert mapping["side"] == "RIGHT"
        assert "position" in mapping

    def test_successful_multiline_match(self, diff_data):
        """Test a successful match on a multi-line suggestion."""
        parsed_diffs, _ = diff_data
        file_name = parsed_diffs[0].file_path

        # This test handles multi-line suggestions for different complexity tiers.
        if "medium" in file_name:
            suggestion = CodeSuggestion(
                file_name=file_name,
                start_line=81,
                end_line=89,
                side=Side.RIGHT,
                comment="Refactored to a more verbose, multi-line block for testing.",
                category=SuggestionCategory.REFACTOR,
                suggested_code=dedent(
                    """
                    # This is a more concise version of the report generation.
                    active_users = [u for u, d in user_manager.users.items() if d.get("active", True)]
                    return f"Active Users ({len(active_users)}): {', '.join(active_users)}"
                    """
                ).strip(),
                existing_code=dedent(
                    """
                    # Refactored to a more verbose, multi-line block for testing.
                    print("Generating active user report...")
                    active_users = [
                        username
                        for username, details in user_manager.users.items()
                        if details.get("active", True)
                    ]
                    report = f"Active Users ({len(active_users)}): {', '.join(active_users)}"
                    print("Report generation complete.")
                    """
                ).strip(),
            )
            expected_line = 81
        elif "large" in file_name:
            suggestion = CodeSuggestion(
                file_name=file_name,
                start_line=205,
                end_line=207,
                side=Side.RIGHT,
                comment="This is a multi-line suggestion for the large file.",
                category=SuggestionCategory.REFACTOR,
                suggested_code="""    console.log("This is the new block.");
    console.log("It has replaced the old one completely.");""",
                existing_code="""    console.log("This is the new block.");
    console.log("It has replaced the old one completely.");""",
            )
            expected_line = 205
        else:
            pytest.skip("Skipping multi-line test for non-matching complexity.")

        mapper = LineMapper(parsed_diffs)

        result = mapper.validate_and_map_suggestion(suggestion)
        assert result is not None
        mapping, reason = result
        # A successful match should not be a fallback.
        assert "fallback" not in reason.lower()

        assert mapping["line"] == expected_line
        assert mapping["side"] == "RIGHT"
        assert "position" in mapping

    def test_successful_match_with_line_shift(self, diff_data):
        """Tests a successful match where the line number has shifted due to preceding changes."""
        parsed_diffs, file_name = diff_data

        # This test is specific to the 'large' fixture where line numbers shift.
        if "large" not in file_name:
            pytest.skip("This test is specific to the 'large' complexity fixture.")

        mapper = LineMapper(parsed_diffs)

        # This suggestion targets 'Change 5', which is after 'Change 4' (a block replacement that shifts lines).
        suggestion = CodeSuggestion(
            file_name=parsed_diffs[0].file_path,
            start_line=221,  # Line number in the 'before' file
            end_line=221,
            side=Side.RIGHT,
            comment="This comment was modified, and its line number shifted.",
            category=SuggestionCategory.REFACTOR,
            suggested_code="""    // This is a critically important comment.""",
            existing_code="""    // This is a critically important comment.""",
        )
        expected_line = 220  # Expected line number in the 'after' file

        result = mapper.validate_and_map_suggestion(suggestion)
        assert result is not None
        mapping, reason = result
        assert "fallback" not in reason.lower()

        assert mapping["line"] == expected_line
        assert mapping["side"] == "RIGHT"
        assert "position" in mapping
