# src/utils/line_mapper.py
from typing import List, Optional, Tuple
from src.utils.diff_parser import ParsedDiff
from src.utils.logger import logger
from src.config.settings import DEBUG_MODE
from src.models.code_review import CodeSuggestion


class LineMapper:
    """
    Utility class for mapping and validating line numbers in code review suggestions.
    Provides enhanced line number accuracy and fallback mechanisms.
    """

    def __init__(self, parsed_files: List[ParsedDiff]):
        self.parsed_files = parsed_files
        self.file_map = {pf.file_path: pf for pf in parsed_files}

    def validate_and_map_suggestion(
        self, suggestion: CodeSuggestion, strict_mode: bool = False
    ) -> Optional[Tuple[int, str]]:
        """
        Validate and map a code suggestion to the correct position.

        Args:
            suggestion: CodeSuggestion object
            strict_mode: If True, only accept exact line matches

        Returns:
            Tuple of (position, adjusted_reason) or None if cannot be mapped
        """
        if not suggestion.file_name or not suggestion.end_line:
            logger.warning(f"Suggestion missing file_name or end_line: {suggestion}")
            return None

        parsed_file = self.file_map.get(suggestion.file_name)
        if not parsed_file:
            logger.warning(f"File not found in diff: {suggestion.file_name}")
            return None

        line = suggestion.end_line
        side = suggestion.side.value if suggestion.side else "RIGHT"

        # New: Try to anchor the suggestion using existing_code if available
        if suggestion.existing_code and suggestion.existing_code.strip():
            try:
                # Find the line number by searching for the existing code in the parsed diff
                corrected_line_info = self._find_line_by_code_content(
                    parsed_file, suggestion.existing_code
                )

                if corrected_line_info:
                    actual_line, actual_side = corrected_line_info
                    if actual_line != line or actual_side != side:
                        logger.info(
                            f"🔄 Corrected line number for {suggestion.file_name}. "
                            f"LLM said: {line} ({side}), Actual: {actual_line} ({actual_side})"
                        )
                        line = actual_line
                        side = actual_side
                    else:
                        logger.info(
                            f"✅ Confirmed suggestion placement via existing_code for {suggestion.file_name}:{line}"
                        )
                else:
                    logger.warning(
                        f"⚠️ Could not find existing_code in diff for {suggestion.file_name}. Falling back to line numbers."
                    )
            except Exception as e:
                logger.error(f"Error searching for existing_code: {e}")

        # Try exact match first
        if (line, side) in parsed_file.commentable_lines:
            position = parsed_file.line_to_position[(line, side)]
            if DEBUG_MODE:
                logger.info(
                    f"✅ Exact match: {suggestion.file_name}:{line} -> position {position}"
                )
            return position, "exact_match"

        if strict_mode:
            if DEBUG_MODE:
                logger.warning(
                    f"❌ Strict mode: No exact match for {suggestion.file_name}:{line}"
                )
            return None

        # Try to find nearby commentable line
        closest_line = parsed_file.find_closest_commentable_line(line, side)
        if closest_line:
            closest_line_num, closest_side = closest_line
            position = parsed_file.line_to_position[closest_line]
            if DEBUG_MODE:
                logger.warning(
                    f"🔀 Adjusted: {suggestion.file_name}:{line} -> {closest_line_num} (position {position})"
                )
            return position, f"adjusted_from_{line}_to_{closest_line_num}"

        logger.error(
            f"❌ Cannot map suggestion: {suggestion.file_name}:{line} ({side})"
        )
        if DEBUG_MODE:
            logger.debug(
                f"Available lines for {suggestion.file_name}: {parsed_file.debug_info()}"
            )
        return None

    def _find_line_by_code_content(
        self, parsed_file: ParsedDiff, code_content: str
    ) -> Optional[Tuple[int, str]]:
        """Finds the first commentable line number that contains the given code content."""
        search_lines = [line.strip() for line in code_content.strip().split("\n")]
        if not search_lines:
            return None

        # Get the raw diff lines to search within
        diff_lines = parsed_file.diff_text.split("\n")

        for i, diff_line in enumerate(diff_lines):
            # We only care about added or removed lines
            if not (diff_line.startswith("+") or diff_line.startswith("-")):
                continue

            # Strip the diff prefix (+, -) and compare for an exact match
            stripped_diff_line = diff_line[1:].strip()
            if search_lines[0] == stripped_diff_line:
                # If it's a multi-line block, check subsequent lines for an exact match too
                is_match = True
                for j, search_line in enumerate(search_lines[1:]):
                    next_diff_line_index = i + 1 + j
                    if next_diff_line_index >= len(diff_lines):
                        is_match = False
                        break

                    next_diff_line = diff_lines[next_diff_line_index]
                    # Ensure the subsequent line is also a diff line and its content matches exactly
                    if not (
                        next_diff_line.startswith(("+", "-"))
                        and next_diff_line[1:].strip() == search_line
                    ):
                        is_match = False
                        break

                if is_match:
                    # The `i` is 0-based index in `diff_lines`, so `i+1` is the 1-based global position in the diff.
                    matched_global_position = i + 1
                    line_info = parsed_file.position_to_line.get(
                        matched_global_position
                    )

                    if line_info and line_info in parsed_file.commentable_lines:
                        # Return the exact matched line if it's commentable
                        return line_info
                    else:
                        logger.warning(
                            f"Matched code block for '{code_content[:20]}...' at position {matched_global_position}, "
                            f"but it's not a direct commentable line or could not be mapped. "
                            f"Falling back to original line number heuristic in validate_and_map_suggestion."
                        )
                        return None  # Indicate it couldn't find a direct commentable line for this text match

        return None

    def get_enhanced_diff_context(self, max_files: int = 5) -> str:
        """
        Generate enhanced context about the diff structure for the LLM.
        Includes line number mappings and hunk information.
        """
        context_parts = ["## 📊 **Diff Structure Information**\n"]

        files_processed = 0
        for parsed_file in self.parsed_files:
            if files_processed >= max_files:
                context_parts.append(
                    f"... and {len(self.parsed_files) - max_files} more files"
                )
                break

            context_parts.append(f"### File: `{parsed_file.file_path}`")
            context_parts.append(f"- **Hunks**: {len(parsed_file.hunk_ranges)}")
            context_parts.append(
                f"- **Commentable lines**: {len(parsed_file.commentable_lines)}"
            )

            if parsed_file.hunk_ranges:
                context_parts.append("- **Hunk ranges**:")
                for i, (ss, se, ts, te) in enumerate(parsed_file.hunk_ranges):
                    context_parts.append(
                        f"  - Hunk {i+1}: Lines {ss}-{se} (OLD) → {ts}-{te} (NEW)"
                    )

            # Show some example commentable lines
            commentable_sample = list(parsed_file.commentable_lines)[:3]
            if commentable_sample:
                context_parts.append("- **Example commentable lines**:")
                for line_num, side in commentable_sample:
                    context_parts.append(f"  - Line {line_num} ({side})")

            context_parts.append("")
            files_processed += 1

        context_parts.append(
            "**⚠️ IMPORTANT**: Only suggest comments on the commentable lines shown above!"
        )
        return "\n".join(context_parts)

    def generate_line_mapping_report(self) -> str:
        """Generate a detailed report of line mappings for debugging."""
        report = ["# Line Mapping Report\n"]

        for parsed_file in self.parsed_files:
            report.append(f"## File: {parsed_file.file_path}")
            report.append(f"- Commentable lines: {len(parsed_file.commentable_lines)}")
            report.append(f"- Total lines in diff: {len(parsed_file.all_lines)}")
            report.append(f"- Hunks: {len(parsed_file.hunk_ranges)}")

            if parsed_file.commentable_lines:
                report.append("### Commentable Lines:")
                for line_num, side in sorted(parsed_file.commentable_lines):
                    position = parsed_file.line_to_position.get((line_num, side))
                    report.append(f"- Line {line_num} ({side}) -> Position {position}")

            report.append("")

        return "\n".join(report)
