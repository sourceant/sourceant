# src/utils/line_mapper.py
import re
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from src.models.code_review import CodeSuggestion
from src.utils.diff_parser import ParsedDiff
from src.utils.logger import logger

DEBUG_MODE = True  # Set to True for verbose logging


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

        # 1. Trust but verify: Check if the LLM's given line is valid and the code matches.
        if (line, side) in parsed_file.commentable_lines:
            position = parsed_file.line_to_position[(line, side)]

            # If existing_code is not provided, we cannot verify content. Skip to next strategy.
            if not suggestion.existing_code:
                logger.debug(
                    f"No existing_code provided for {suggestion.file_name}:{line}. Skipping content check."
                )
            # Boundary check to prevent KeyError
            elif position > len(parsed_file.all_lines):
                logger.warning(
                    f"⚠️ Position {position} is out of bounds for {suggestion.file_name}."
                )
            else:
                diff_line_content = parsed_file.all_lines[position - 1][1:].strip()
                suggestion_line_content = suggestion.existing_code.strip().split("\n")[
                    0
                ]

                if diff_line_content == suggestion_line_content:
                    logger.info(
                        f"✅ Confirmed exact match for {suggestion.file_name}:{line} via line number and content."
                    )
                    return position, "exact_match_with_content_check"

        # 2. If the initial check fails, use the best-effort search for correction.
        if suggestion.existing_code and suggestion.existing_code.strip():
            try:
                corrected_line_info = self._find_line_by_code_content(
                    parsed_file, suggestion.existing_code
                )
                if corrected_line_info:
                    actual_line, actual_side = corrected_line_info
                    logger.info(
                        f"🔄 Corrected line number for {suggestion.file_name}. "
                        f"LLM said: {line}, Actual: {actual_line}"
                    )
                    line = actual_line
                    side = actual_side
                else:
                    logger.warning(
                        f"⚠️ Could not find existing_code for {suggestion.file_name} via search. Falling back to heuristics."
                    )
            except Exception as e:
                logger.error(f"Error searching for existing_code: {e}")

        # 3. Use the (potentially corrected) line number to find a commentable position.
        if (line, side) in parsed_file.commentable_lines:
            position = parsed_file.line_to_position[(line, side)]
            if DEBUG_MODE:
                logger.info(
                    f"✅ Found match for {suggestion.file_name}:{line} -> position {position}"
                )
            return position, "found_match"

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

    def _normalize_code(self, code: str) -> str:
        """Normalizes a code snippet for fuzzy matching."""
        # Remove ellipses and other common artifacts
        code = re.sub(r"\.\.\.", "", code)
        # Collapse whitespace and remove leading/trailing whitespace from each line
        lines = [line.strip() for line in code.split("\n")]
        # Filter out empty lines that might result from normalization
        non_empty_lines = [line for line in lines if line]
        return " ".join(non_empty_lines)

    def _find_line_by_code_content(
        self, parsed_file: ParsedDiff, code_content: str
    ) -> Optional[Tuple[int, str]]:
        """Finds the best-matching commentable line for a given code snippet using fuzzy matching."""
        if not code_content.strip():
            return None

        # Normalize the search query from the LLM
        normalized_search_text = self._normalize_code(code_content)
        search_lines_count = len(code_content.strip().split("\n"))

        diff_lines_with_marker = parsed_file.diff_text.split("\n")
        # We need the original content with just the +/- marker stripped for normalization
        diff_lines_for_search = [
            line[1:] if line and line[0] in ("+", "-", " ") else line
            for line in diff_lines_with_marker
        ]

        best_match_ratio = 0.6  # Set a minimum threshold
        best_match_index = -1

        # Use a sliding window to find the best match in the diff
        for i in range(len(diff_lines_for_search) - search_lines_count + 1):
            window_lines = diff_lines_for_search[i : i + search_lines_count]
            window_text = "\n".join(window_lines)
            normalized_window_text = self._normalize_code(window_text)

            if not normalized_window_text:
                continue

            matcher = SequenceMatcher(
                None, normalized_search_text, normalized_window_text
            )
            ratio = matcher.ratio()

            if ratio > best_match_ratio:
                best_match_ratio = ratio
                best_match_index = i

        # If a good match is found, find a commentable line within that matched block
        if best_match_index != -1:
            last_commentable_line_info = None
            # Iterate through the matched block
            for i in range(best_match_index, best_match_index + search_lines_count):
                # Ensure we don't go out of bounds
                if i >= len(diff_lines_with_marker):
                    break

                diff_line_marker = (
                    diff_lines_with_marker[i][0] if diff_lines_with_marker[i] else " "
                )
                if diff_line_marker in ("+", "-"):
                    position = i + 1
                    line_info = parsed_file.position_to_line.get(position)
                    if line_info and line_info in parsed_file.commentable_lines:
                        last_commentable_line_info = line_info

            if last_commentable_line_info:
                logger.info(f"Fuzzy match found with ratio {best_match_ratio:.2f}.")
                return last_commentable_line_info

        logger.debug(
            f"Could not find a suitable anchor point for code: '{code_content[:30]}...'"
        )
        return None

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
