"""Semgrep, read for what it is certain of.

One tool rather than an aggregator, deliberately. An aggregator runs the real
linter for each language, which is better output, but it installs that
language's toolchain to do it and this image carries git and nothing else.
Semgrep is one package, reads more than thirty languages from their syntax
alone, and needs none of them installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from .interfaces import AnalyzerFailed
from .models import ERROR, NOTE, WARNING, AnalyzerFinding

# Long enough for a large change, short enough that a tool which has stopped
# answering does not hold the review behind it.
TIMEOUT = 120

AS_SEVERITY = {
    "ERROR": ERROR,
    "WARNING": WARNING,
    "INFO": NOTE,
}


class SemgrepAnalyzer:
    name = "semgrep"

    def __init__(self, config: str = "p/default", timeout: int = TIMEOUT) -> None:
        # Which rules to read by. A registry pack is fetched once and kept by
        # semgrep itself; a path is read from disk and needs nothing.
        self._config = config
        self._timeout = timeout

    def available(self) -> bool:
        return shutil.which("semgrep") is not None

    def examine(self, root: Path, paths: Sequence[str]) -> Sequence[AnalyzerFinding]:
        readable = [one for one in paths if (root / one).is_file()]
        if not readable:
            return ()
        return tuple(self._read(self._run(root, readable)))

    def _run(self, root: Path, paths: Sequence[str]) -> dict:
        """Ask semgrep, tolerating the exit code it uses to mean it found things.

        A linter that found nothing and a linter that found something are both
        successful runs and they differ by exit code, so the code is read
        rather than checked.
        """
        try:
            completed = subprocess.run(
                [
                    "semgrep",
                    "scan",
                    "--json",
                    "--quiet",
                    "--no-git-ignore",
                    f"--config={self._config}",
                    *paths,
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise AnalyzerFailed(f"could not run semgrep: {e}") from e
        # 0 found nothing, 1 found something. Anything else is semgrep itself
        # failing, and its findings are not worth guessing at.
        if completed.returncode > 1:
            raise AnalyzerFailed(
                f"semgrep stopped with {completed.returncode}: "
                f"{completed.stderr.strip()[:500]}"
            )
        try:
            return json.loads(completed.stdout or "{}")
        except ValueError as e:
            raise AnalyzerFailed(f"could not read what semgrep answered: {e}") from e

    def _read(self, answered: dict):
        for result in answered.get("results") or ():
            extra = result.get("extra") or {}
            start = (result.get("start") or {}).get("line")
            end = (result.get("end") or {}).get("line")
            if not isinstance(start, int):
                continue
            yield AnalyzerFinding(
                path=str(result.get("path") or ""),
                start_line=start,
                end_line=end if isinstance(end, int) else start,
                rule=str(result.get("check_id") or "").rsplit(".", 1)[-1],
                message=" ".join(str(extra.get("message") or "").split()),
                severity=AS_SEVERITY.get(str(extra.get("severity") or ""), WARNING),
                tool=self.name,
            )
