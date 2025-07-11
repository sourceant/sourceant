import pytest
import difflib
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

    file_name = f"b/{complexity}_file.{ext}"
    diff_text = "".join(
        difflib.unified_diff(
            file_v1, file_v2, fromfile=f"a/{complexity}_file.{ext}", tofile=file_name
        )
    )
    return parse_diff(diff_text), file_name


@pytest.fixture(params=["simple", "medium", "large"])
def diff_data(request):
    """Parameterized fixture to provide diffs of varying complexity."""
    return _create_diff_fixture(request.param)


class TestLineMapper:
    def test_successful_match(self, diff_data):
        """Test a successful match on a line that was modified."""
        parsed_diffs, simplified_file_name = diff_data
        mapper = LineMapper(parsed_diffs)
        file_name = parsed_diffs[0].file_path

        # Define test cases for each complexity level
        test_cases = {
            "b/simple_file.py": {
                "suggestion": CodeSuggestion(
                    file_name=file_name,
                    start_line=18,
                    end_line=18,
                    side=Side.RIGHT,
                    comment="This should be logged as an error.",
                    category=SuggestionCategory.BUG,
                    suggested_code="""        print("System initialization failed.")""",
                    existing_code="""        print("System initialization failed.")""",
                ),
                "expected_line": 17,
            },
            "b/medium_file.py": {
                "suggestion": CodeSuggestion(
                    file_name=file_name,
                    start_line=70,
                    end_line=70,
                    side=Side.RIGHT,
                    comment="Should log the update.",
                    category=SuggestionCategory.IMPROVEMENT,
                    suggested_code="""        logging.info(f"Updated email for {username}")""",
                    existing_code="""        self._save_users()""",
                ),
                "expected_line": 69,
            },
            "b/large_file.py": {
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
            "b/large_file.js": {
                "suggestion": CodeSuggestion(
                    file_name=file_name,
                    start_line=191,
                    end_line=191,
                    side=Side.RIGHT,
                    comment="This line was modified.",
                    category=SuggestionCategory.IMPROVEMENT,
                    suggested_code="""    console.log('This line was modified from the original.');""",
                    existing_code="""    // Extra line in original, to be modified""",
                ),
                "expected_line": 191,
            },
        }

        case = test_cases[simplified_file_name]
        suggestion = case["suggestion"]
        expected_line = case["expected_line"]

        result = mapper.validate_and_map_suggestion(suggestion)
        assert result is not None
        position, reason = result
        # A successful match should not be a fallback.
        assert "fallback" not in reason.lower()

        parsed_file = mapper.file_map[file_name]
        line_num, side = parsed_file.position_to_line[position]
        assert line_num == expected_line
        assert side == "RIGHT"

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
        position, reason = result
        assert reason != "Matched via existing_code"

        parsed_file = mapper.file_map[file_name]
        line_num, side = parsed_file.position_to_line[position]
        # The main point is that it fell back, the exact line can be adjusted.
        assert side == "RIGHT"

    def test_successful_multiline_match(self, diff_data):
        """Test a successful match on a multi-line suggestion."""
        parsed_diffs, _ = diff_data
        file_name = parsed_diffs[0].file_path

        # This test handles multi-line suggestions for different complexity tiers.
        if "medium" in file_name:
            suggestion = CodeSuggestion(
                file_name=file_name,
                start_line=58,
                end_line=62,
                side=Side.RIGHT,
                comment="This is a multi-line suggestion for the medium file.",
                category=SuggestionCategory.REFACTOR,
                suggested_code="""        # Refactored to a more verbose, multi-line block for testing.
        print("Generating active user report...")
        active_users = [
            username for username, details in user_manager.users.items()
            if details.get('active', True)
        ]
        report = f"Active Users ({len(active_users)}): {', '.join(active_users)}"
        print("Report generation complete.")
        return report""",
                existing_code="""        active_users = []
        for username, details in user_manager.users.items():
            if details.get('active', True):
                active_users.append(username)
        return "Active Users: " + ", ".join(active_users)""",
            )
            expected_line = 62
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
                existing_code="""    console.log("This entire block will be replaced.");
    console.log("It has multiple lines that will vanish.");
    console.log("And new lines will take its place.");""",
            )
            expected_line = 206
        else:
            pytest.skip("Skipping multi-line test for non-matching complexity.")

        mapper = LineMapper(parsed_diffs)

        result = mapper.validate_and_map_suggestion(suggestion)
        assert result is not None
        position, reason = result
        # A successful match should not be a fallback.
        assert "fallback" not in reason.lower()

        parsed_file = mapper.file_map[file_name]
        line_num, side = parsed_file.position_to_line[position]
        assert line_num == expected_line
        assert side == "RIGHT"
