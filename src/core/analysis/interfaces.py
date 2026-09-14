"""What runs over a checkout and reports what it is certain of."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

from .models import AnalyzerFinding


class AnalyzerFailed(RuntimeError):
    """The tool could not read the change.

    Raised rather than answered with nothing, because the two mean opposite
    things: one is a change with nothing wrong in it and the other is a change
    nobody managed to look at.
    """


@runtime_checkable
class Analyzer(Protocol):
    """A tool that reads code and answers without being asked to interpret.

    Contributed rather than registered, so a deployment can run several and a
    second one is added without the review learning its name.
    """

    name: str

    def available(self) -> bool:
        """Whether this can run at all here.

        Asked before the work rather than discovered during it, so a tool that
        was never installed is reported as a gap in the reading instead of as
        a change with nothing wrong with it.
        """
        ...

    def examine(
        self, root: Path, paths: Sequence[str]
    ) -> Sequence[AnalyzerFinding]: ...
