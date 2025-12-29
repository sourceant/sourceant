# src/utils/line_mapper.py

from typing import List, Optional, Tuple

from src.models.code_review import CodeSuggestion
from src.utils.diff_parser import ParsedDiff
from src.utils.logger import logger
from src.config.settings import DEBUG_MODE


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
        logger.info(f"\n\n--- Validating suggestion for {suggestion.file_name} ---")
        logger.info(f"Suggestion details: {suggestion}")

        if not suggestion.file_name or not suggestion.end_line:
            logger.warning(f"Suggestion missing file_name or end_line: {suggestion}")
            return None

        # Normalize suggestion file_name by removing git prefixes if present
        normalized_file_name = suggestion.file_name
        if suggestion.file_name.startswith(("a/", "b/")):
            normalized_file_name = suggestion.file_name[2:]
            logger.info(
                f"Normalized suggestion file_name: '{suggestion.file_name}' -> '{normalized_file_name}'"
            )

        parsed_file = self.file_map.get(normalized_file_name)
        if not parsed_file:
            logger.warning(
                f"File not found in diff after normalization: '{suggestion.file_name}' -> '{normalized_file_name}'"
            )
            logger.warning(f"Available files: {list(self.file_map.keys())}")
            return None

        # Strategy 1: Find a precise anchor by matching content within the relevant hunk.
        logger.info("Attempting Strategy 1: Content Match")
        anchor_info = self._find_anchor_by_content_match(parsed_file, suggestion)
        if anchor_info:
            line, side = anchor_info
            position = parsed_file.line_to_position.get((line, side))
            if position:
                logger.info(
                    f"✅ Strategy 1 SUCCESS: Found precise anchor at line {line} via content match."
                )
                return position, "content_match"
            else:
                logger.warning(
                    f"⚠️ Strategy 1 WARNING: Content match found for line {line}, but no position mapped."
                )
        else:
            logger.info("Strategy 1 FAILED: No content match found.")

        # Strategy 3: Use the suggestion's line number as a last resort.
        logger.info("Attempting Strategy 3: Exact Line Number Match")
        line = suggestion.end_line
        side = suggestion.side.value if suggestion.side else "RIGHT"
        if (line, side) in parsed_file.commentable_lines:
            position = parsed_file.line_to_position[(line, side)]
            logger.info(
                f"✅ Strategy 3 SUCCESS: Found match for {suggestion.file_name}:{line} via line number."
            )
            return position, "line_number_match"
        else:
            logger.info(f"Strategy 3 FAILED: Line {line} is not a commentable line.")

        if strict_mode:
            logger.warning(
                f"❌ Strict mode enabled. No match for {suggestion.file_name}:{line}. Halting."
            )
            return None

        # Strategy 4: Find the closest commentable line as a final fallback.
        logger.info("Attempting Strategy 4: Closest Line Fallback")
        closest_line = parsed_file.find_closest_commentable_line(line, side)
        if closest_line:
            closest_line_num, closest_side = closest_line
            position = parsed_file.line_to_position[closest_line]
            logger.warning(
                f"✅ Strategy 4 SUCCESS: Adjusted {suggestion.file_name}:{line} -> {closest_line_num} (position {position})"
            )
            return position, f"adjusted_from_{line}_to_{closest_line_num}"
        else:
            logger.info("Strategy 4 FAILED: No close commentable line found.")

        logger.error(
            f"❌ All strategies failed. Cannot map suggestion: {suggestion.file_name}:{line} ({side})"
        )
        if DEBUG_MODE:
            logger.debug(
                f"Available lines for {suggestion.file_name}: {parsed_file.debug_info()}"
            )
        return None

    def _find_anchor_by_content_match(
        self, parsed_file: ParsedDiff, suggestion: CodeSuggestion
    ) -> Optional[Tuple[int, str]]:
        """Finds an anchor by matching content within the relevant diff hunk."""
        if not suggestion.existing_code:
            logger.debug("No existing_code in suggestion, skipping content match.")
            return None

        # Strategy A: Multi-line block matching (new, more robust)
        result = self._multiline_block_search(parsed_file, suggestion)
        if result:
            return result

        # Strategy B: Exact content search across all diff content
        result = self._exact_content_search(parsed_file, suggestion)
        if result:
            return result

        # Strategy C: Partial content match with similarity scoring
        result = self._partial_content_search(parsed_file, suggestion)
        if result:
            return result

        # Strategy D: Proximity + content similarity hybrid
        result = self._proximity_content_search(parsed_file, suggestion)
        if result:
            return result

        self._debug_content_search(parsed_file, suggestion)
        logger.debug("--- All content matching strategies failed. ---")
        return None

    def _normalize_diff_line(self, line: str) -> str:
        """Normalize a diff line by removing prefix (+/-/space) and stripping."""
        if line and line[0] in ["+", "-", " "]:
            return line[1:].strip()
        return line.strip()

    def _multiline_block_search(
        self, parsed_file: ParsedDiff, suggestion: CodeSuggestion
    ) -> Optional[Tuple[int, str]]:
        """
        Search for a multi-line block of existing_code within the diff.
        This handles the case where existing_code spans multiple lines.
        """
        existing_lines = []
        for line in suggestion.existing_code.splitlines():
            normalized = self._normalize_diff_line(line)
            if normalized:
                existing_lines.append(normalized)

        if not existing_lines:
            return None

        diff_lines = parsed_file.all_lines
        num_diff_lines = len(diff_lines)
        num_existing = len(existing_lines)

        best_match_start = None
        best_match_score = 0

        for start_pos in range(num_diff_lines):
            if start_pos + num_existing > num_diff_lines:
                break

            match_count = 0
            for i, existing_line in enumerate(existing_lines):
                diff_line = diff_lines[start_pos + i]
                normalized_diff = self._normalize_diff_line(diff_line)

                if normalized_diff == existing_line:
                    match_count += 1
                elif self._lines_similar(normalized_diff, existing_line, threshold=0.8):
                    match_count += 0.8

            score = match_count / num_existing
            if score > best_match_score:
                best_match_score = score
                best_match_start = start_pos

        if best_match_score >= 0.7 and best_match_start is not None:
            position = best_match_start + 1
            if position in parsed_file.position_to_line:
                line_num, side = parsed_file.position_to_line[position]
                logger.info(
                    f"✅ Multi-line block match found (score: {best_match_score:.2f})! "
                    f"Position {position} -> Line {line_num} ({side})"
                )
                return (line_num, side)

        return None

    def _exact_content_search(
        self, parsed_file: ParsedDiff, suggestion: CodeSuggestion
    ) -> Optional[Tuple[int, str]]:
        """Search for exact content matches across all diff lines."""
        lines_to_match = [
            self._normalize_diff_line(line)
            for line in suggestion.existing_code.splitlines()
            if self._normalize_diff_line(line)
        ]

        if not lines_to_match:
            return None

        for pos in range(1, len(parsed_file.all_lines) + 1):
            if pos in parsed_file.position_to_line:
                line_num, side = parsed_file.position_to_line[pos]
                line_content = parsed_file.all_lines[pos - 1]
                clean_content = self._normalize_diff_line(line_content)

                for match_line in lines_to_match:
                    if clean_content == match_line:
                        logger.info(
                            f"✅ Exact content match found! Line {line_num} ({side}): '{clean_content[:50]}...'"
                        )
                        return (line_num, side)
        return None

    def _partial_content_search(
        self, parsed_file: ParsedDiff, suggestion: CodeSuggestion
    ) -> Optional[Tuple[int, str]]:
        """Search for partial content matches with similarity scoring."""
        lines_to_match = [
            self._normalize_diff_line(line)
            for line in suggestion.existing_code.splitlines()
            if self._normalize_diff_line(line)
        ]

        if not lines_to_match:
            return None

        best_score = 0
        best_match = None

        for pos in range(1, len(parsed_file.all_lines) + 1):
            if pos in parsed_file.position_to_line:
                line_num, side = parsed_file.position_to_line[pos]
                line_content = parsed_file.all_lines[pos - 1]
                clean_content = self._normalize_diff_line(line_content)

                for match_line in lines_to_match:
                    if self._lines_similar(clean_content, match_line, threshold=0.6):
                        proximity_bonus = self._calculate_proximity_score(
                            line_num, suggestion.end_line
                        )
                        score = 0.8 + proximity_bonus * 0.2

                        if score > best_score:
                            best_score = score
                            best_match = (line_num, side)

        if best_match and best_score >= 0.7:
            logger.info(
                f"✅ Partial content match found (score: {best_score:.2f})! Line {best_match[0]} ({best_match[1]})"
            )
            return best_match
        return None

    def _proximity_content_search(
        self, parsed_file: ParsedDiff, suggestion: CodeSuggestion
    ) -> Optional[Tuple[int, str]]:
        """Search using proximity to suggested line number + content similarity."""
        lines_to_match = [
            self._normalize_diff_line(line)
            for line in suggestion.existing_code.splitlines()
            if self._normalize_diff_line(line)
        ]

        if not lines_to_match:
            return None

        target_line = suggestion.end_line
        search_range = 10

        candidates = []
        for pos in range(1, len(parsed_file.all_lines) + 1):
            if pos in parsed_file.position_to_line:
                line_num, side = parsed_file.position_to_line[pos]

                if abs(line_num - target_line) <= search_range:
                    line_content = parsed_file.all_lines[pos - 1]
                    clean_content = self._normalize_diff_line(line_content)

                    content_score = max(
                        self._calculate_line_similarity(clean_content, match_line)
                        for match_line in lines_to_match
                    )
                    proximity_score = self._calculate_proximity_score(
                        line_num, target_line
                    )
                    total_score = content_score * 0.6 + proximity_score * 0.4

                    if total_score > 0.4:
                        candidates.append((total_score, line_num, side, clean_content))

        if candidates:
            best_score, best_line, best_side, content = max(candidates)
            logger.info(
                f"✅ Proximity content match found (score: {best_score:.2f})! Line {best_line} ({best_side}): '{content[:50]}...'"
            )
            return (best_line, best_side)

        return None

    def _calculate_line_similarity(self, line1: str, line2: str) -> float:
        """Calculate similarity score between two lines."""
        if not line1 or not line2:
            return 0.0

        # Try exact match first
        if line1 == line2:
            return 1.0

        # Use existing _lines_similar logic
        if self._lines_similar(line1, line2, threshold=0.6):
            return 0.8
        elif self._lines_similar(line1, line2, threshold=0.4):
            return 0.6
        else:
            return 0.0

    def _calculate_proximity_score(self, actual_line: int, target_line: int) -> float:
        """Calculate proximity score (1.0 = exact match, decreases with distance)."""
        distance = abs(actual_line - target_line)
        if distance == 0:
            return 1.0
        elif distance <= 2:
            return 0.8
        elif distance <= 5:
            return 0.6
        elif distance <= 10:
            return 0.4
        else:
            return 0.2

    def _debug_content_search(
        self, parsed_file: ParsedDiff, suggestion: CodeSuggestion
    ):
        """Enhanced debugging for content search failures."""
        if not DEBUG_MODE:
            return

        logger.debug(f"Content search debug for {suggestion.file_name}:")
        logger.debug(f"  LLM lines: {suggestion.start_line}-{suggestion.end_line}")
        logger.debug(f"  LLM existing_code: {repr(suggestion.existing_code)}")
        logger.debug(f"  Available hunks: {parsed_file.hunk_ranges}")

        # Show what we're searching through
        lines_to_match = [
            line.strip() for line in suggestion.existing_code.splitlines()
        ]
        logger.debug(f"  Searching for: {lines_to_match}")

        logger.debug("  Available diff content:")
        for pos in range(1, min(len(parsed_file.all_lines) + 1, 20)):  # Limit output
            if pos in parsed_file.position_to_line:
                line_num, side = parsed_file.position_to_line[pos]
                line_content = parsed_file.all_lines[pos - 1]
                clean = (
                    line_content[1:].strip()
                    if line_content and line_content[0] in ["+", "-", " "]
                    else line_content
                )
                logger.debug(
                    f"    Pos {pos} -> Line {line_num} ({side}): {repr(clean[:40])}"
                )

        if len(parsed_file.all_lines) > 20:
            logger.debug(
                f"    ... (showing first 20 of {len(parsed_file.all_lines)} lines)"
            )

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
                    if position and position <= len(parsed_file.all_lines):
                        line_content = parsed_file.all_lines[position - 1].rstrip()
                        report.append(
                            f"- Line {line_num} ({side}) -> Position {position}: `{line_content}`"
                        )
                    else:
                        report.append(
                            f"- Line {line_num} ({side}) -> Position {position}: [INVALID POSITION]"
                        )

            report.append("\n### Raw Diff Lines:")
            for i, line in enumerate(parsed_file.all_lines):
                report.append(f"P{i+1}: `{line.rstrip()}`")

            report.append("")

        return "\n".join(report)

    def _lines_similar(self, line1: str, line2: str, threshold: float = 0.8) -> bool:
        """Check if two lines are similar enough (handles minor formatting differences)."""
        if not line1 or not line2:
            return False

        # Remove extra whitespace and compare
        clean1 = " ".join(line1.split())
        clean2 = " ".join(line2.split())

        if clean1 == clean2:
            return True

        # Calculate simple character-based similarity
        if len(clean1) == 0 or len(clean2) == 0:
            return False

        # Count matching characters in order
        matches = sum(1 for a, b in zip(clean1, clean2) if a == b)
        similarity = matches / max(len(clean1), len(clean2))

        return similarity >= threshold
