"""What a deterministic tool found, in the one shape the review reads."""

from __future__ import annotations

from dataclasses import dataclass

# What a finding means for the change, in the words every tool agrees on.
# Anything a tool calls by another name is mapped onto one of these rather than
# carried through, so a threshold can be written once instead of per tool.
ERROR = "error"
WARNING = "warning"
NOTE = "note"

SEVERITIES = (ERROR, WARNING, NOTE)


@dataclass(frozen=True)
class AnalyzerFinding:
    """One thing a tool is certain about.

    Certain is the point. This is the half of a review that does not need a
    model to agree with it, so it carries where it is and what rule it broke
    and nothing that would need interpreting.
    """

    path: str
    start_line: int
    end_line: int
    rule: str
    message: str
    severity: str = WARNING
    tool: str = ""

    def covers(self, path: str, start_line: int, end_line: int, slack: int = 3) -> bool:
        """Whether this is about the same lines as something else.

        The slack is there because a tool points at the line it parsed and a
        reviewer points at the line it was thinking about, and on a multi-line
        statement those are not the same line.
        """
        if self.path != path:
            return False
        return (
            self.start_line - slack <= end_line and start_line - slack <= self.end_line
        )

    def rendered(self) -> str:
        where = (
            f"{self.path}:{self.start_line}"
            if self.start_line == self.end_line
            else f"{self.path}:{self.start_line}-{self.end_line}"
        )
        return f"- [{self.severity}] {where} ({self.tool} {self.rule}): {self.message}"
