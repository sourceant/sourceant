# src/utils/diff_parser.py
from unidiff import PatchSet, PatchedFile
from typing import List, Dict, Tuple, Set


class ParsedDiff:
    """Represents the parsed diff for a single file, with mappings for comment positions."""

    def __init__(self, patched_file: PatchedFile):
        self.file_path = patched_file.path
        self.diff_text = str(patched_file)
        # (line_in_file, side) -> position_in_diff_hunk
        self.line_to_position: Dict[Tuple[int, str], int] = {}
        # set of (line_in_file, side)
        self.commentable_lines: Set[Tuple[int, str]] = set()

        self._parse_hunks(patched_file)

    def _parse_hunks(self, patched_file: PatchedFile):
        """Parses the hunks to build the line-to-position mapping."""
        for hunk in patched_file:
            # Position is the 1-based index within the hunk
            position_in_hunk = 0
            for line in hunk:
                position_in_hunk += 1
                if line.is_added:
                    line_num = line.target_line_no
                    side = "RIGHT"
                    self.commentable_lines.add((line_num, side))
                    self.line_to_position[(line_num, side)] = position_in_hunk
                elif line.is_removed:
                    line_num = line.source_line_no
                    side = "LEFT"
                    self.commentable_lines.add((line_num, side))
                    self.line_to_position[(line_num, side)] = position_in_hunk


def parse_diff(diff_text: str) -> List[ParsedDiff]:
    """Parses a raw diff string into a list of ParsedDiff objects."""
    if not diff_text:
        return []
    patch_set = PatchSet.from_string(diff_text)
    return [ParsedDiff(pf) for pf in patch_set]
