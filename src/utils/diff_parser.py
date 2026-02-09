# src/utils/diff_parser.py
from unidiff import PatchSet, PatchedFile
from typing import List, Dict, Tuple, Set, Optional
from src.utils.logger import logger


class ParsedDiff:
    """Represents the parsed diff for a single file, with mappings for comment positions."""

    def __init__(self, patched_file: PatchedFile):
        self._patched_file = patched_file
        self.file_path = patched_file.path
        self.diff_text = str(patched_file)
        # (line_in_file, side) -> position_in_diff (global position across all hunks)
        self.line_to_position: Dict[Tuple[int, str], int] = {}
        # (global_position) -> (line_in_file, side)
        self.position_to_line: Dict[int, Tuple[int, str]] = {}
        # set of (line_in_file, side) - lines that can receive comments
        self.commentable_lines: Set[Tuple[int, str]] = set()
        # All lines in the diff including context (for better LLM understanding)
        self.all_lines: List[str] = []
        # Line ranges for each hunk (for debugging)
        self.hunk_ranges: List[Tuple[int, int, int, int]] = []

        self._parse_hunks(patched_file)

    def _parse_hunks(self, patched_file: PatchedFile):
        """Parses the hunks to build comprehensive line-to-position mappings."""
        global_position = 0

        for hunk in patched_file:
            # Track hunk ranges for debugging
            self.hunk_ranges.append(
                (
                    hunk.source_start,
                    hunk.source_start + hunk.source_length - 1,
                    hunk.target_start,
                    hunk.target_start + hunk.target_length - 1,
                )
            )

            for line in hunk:
                global_position += 1

                if line.is_added:
                    line_num = line.target_line_no
                    side = "RIGHT"
                    self.commentable_lines.add((line_num, side))
                    self.line_to_position[(line_num, side)] = global_position
                    self.position_to_line[global_position] = (line_num, side)
                    self.all_lines.append(line.value)
                elif line.is_removed:
                    line_num = line.source_line_no
                    side = "LEFT"
                    self.commentable_lines.add((line_num, side))
                    self.line_to_position[(line_num, side)] = global_position
                    self.position_to_line[global_position] = (line_num, side)
                    self.all_lines.append(line.value)
                elif line.is_context:
                    # Context lines exist on both sides
                    source_line_num = line.source_line_no
                    target_line_num = line.target_line_no

                    self.all_lines.append(line.value)

                    # For context lines, we map the position to both sides for completeness,
                    # even though they are not directly commentable.
                    if source_line_num:
                        self.line_to_position[(source_line_num, "LEFT")] = (
                            global_position
                        )
                        self.position_to_line[global_position] = (
                            source_line_num,
                            "LEFT",
                        )
                    if target_line_num:
                        self.line_to_position[(target_line_num, "RIGHT")] = (
                            global_position
                        )
                        # If both source and target lines exist, RIGHT side takes precedence for position_to_line
                        self.position_to_line[global_position] = (
                            target_line_num,
                            "RIGHT",
                        )

    def find_closest_commentable_line(
        self, target_line: int, side: str = "RIGHT"
    ) -> Optional[Tuple[int, str]]:
        """Find the closest commentable line to the target line."""
        if (target_line, side) in self.commentable_lines:
            return (target_line, side)

        # Look for nearby commentable lines (within 5 lines)
        for offset in range(1, 6):
            # Check line above
            if (target_line - offset, side) in self.commentable_lines:
                return (target_line - offset, side)
            # Check line below
            if (target_line + offset, side) in self.commentable_lines:
                return (target_line + offset, side)

        # If no nearby line found, try the other side
        other_side = "LEFT" if side == "RIGHT" else "RIGHT"
        if (target_line, other_side) in self.commentable_lines:
            return (target_line, other_side)

        return None

    def get_line_context(self, line_num: int, side: str = "RIGHT") -> str:
        """Get context information about a line for debugging."""
        if (line_num, side) in self.commentable_lines:
            position = self.line_to_position.get((line_num, side))
            return f"Line {line_num} ({side}) - Position: {position} - COMMENTABLE"
        # This check is tricky now since all_lines doesn't store line numbers directly.
        # We can find the position and check if it's not in commentable_lines.
        position = self.line_to_position.get((line_num, side))
        if position:
            return f"Line {line_num} ({side}) - Position: {position} - CONTEXT"

        else:
            return f"Line {line_num} ({side}) - NOT FOUND IN DIFF"

    @property
    def changed_line_count(self) -> int:
        """Return the number of added + removed lines in this diff."""
        return len(self.commentable_lines)

    def to_decoupled_format(self) -> str:
        """Convert the parsed diff into a decoupled old/new hunk format.

        Separates removed and added code into distinct labeled blocks per hunk,
        making it clearer for the LLM what changed.
        """
        patched_file = self._patched_file
        parts = [f"## File: {self.file_path}"]

        if patched_file.is_removed_file:
            parts.append("[file deleted]")
            return "\n".join(parts)

        if patched_file.is_rename:
            parts.append(
                f"[renamed from {patched_file.source_file} to {patched_file.target_file}]"
            )

        for hunk_idx, hunk in enumerate(patched_file):
            old_lines = []
            new_lines = []

            for line in hunk:
                value = line.value.rstrip("\n")
                if line.is_context:
                    old_lines.append(f" {value}")
                    new_lines.append(f"{line.target_line_no}  {value}")
                elif line.is_removed:
                    old_lines.append(f"-{value}")
                elif line.is_added:
                    new_lines.append(f"{line.target_line_no} +{value}")

            if old_lines:
                parts.append(
                    f"__old hunk__\n" f"@@ {hunk.source_start},{hunk.source_length} @@"
                )
                parts.extend(old_lines)
            if new_lines:
                parts.append(
                    f"__new hunk__\n" f"@@ {hunk.target_start},{hunk.target_length} @@"
                )
                parts.extend(new_lines)

        return "\n".join(parts)

    def debug_info(self) -> str:
        """Return debug information about the parsed diff."""
        info = [
            f"File: {self.file_path}",
            f"Hunks: {len(self.hunk_ranges)}",
            f"Commentable lines: {len(self.commentable_lines)}",
            f"All lines: {len(self.all_lines)}",
            "Hunk ranges (source_start-end, target_start-end):",
        ]
        for i, (ss, se, ts, te) in enumerate(self.hunk_ranges):
            info.append(f"  Hunk {i+1}: {ss}-{se} -> {ts}-{te}")
        return "\n".join(info)


def parse_diff(diff_text: str) -> List[ParsedDiff]:
    """Parse a diff string into a list of ParsedDiff objects."""
    try:
        patch_set = PatchSet(diff_text)
        return [ParsedDiff(pf) for pf in patch_set]
    except Exception as e:
        logger.error(f"Failed to parse diff: {e}")
        return []
