"""What a review can know without asking a model.

A reviewer that spends a pass rediscovering an unused import has spent that
pass badly, and a reviewer that reports one is telling the reader something a
tool already told them. So the deterministic half is run first: what it finds
goes to the model as fact, anything the model then says about the same lines is
dropped as already covered, and a change with more errors than a threshold
allows is reported without being read at all.

Nothing here posts a comment. These findings shape the review; they are not
part of it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from src.core.services import ServiceRegistry, service_registry
from src.core.parallel import parallel_map
from src.utils.logger import logger

from .interfaces import Analyzer
from .models import (
    ERROR,
    NOTE,
    SEVERITIES,
    WARNING,
    AnalyzerFinding,
    AnalyzerCoverage,
    AnalyzerReport,
)
from .semgrep import SemgrepAnalyzer

# The most findings worth carrying into a prompt. Past this the list stops
# telling a reviewer where to look and starts being something to skim.
MOST_REPORTED = 100


@dataclass(frozen=True)
class Analysis:
    """What the deterministic half of a review found, and what it could read."""

    findings: tuple[AnalyzerFinding, ...] = ()
    #: Kept apart because a language no tool covers is not a language with
    #: nothing wrong in it.
    ran: tuple[str, ...] = ()
    unavailable: tuple[str, ...] = ()
    coverage: tuple[AnalyzerCoverage, ...] = ()
    requested: tuple[str, ...] = ()
    file_languages: tuple[tuple[str, str], ...] = ()

    @property
    def unchecked(self) -> tuple[str, ...]:
        checked = {path for one in self.coverage for path in one.paths}
        return tuple(path for path in self.requested if path not in checked)

    def __bool__(self) -> bool:
        return bool(self.findings)

    def counted(self, severity: str) -> int:
        return sum(1 for one in self.findings if one.severity == severity)

    def covering(self, path: str, start_line: int, end_line: int):
        """Whatever a tool already said about these lines."""
        return [one for one in self.findings if one.covers(path, start_line, end_line)]

    def rendered(self) -> str | None:
        """What a review is told, before it reads anything itself."""
        if not self.findings:
            return None

        def worst_first(one):
            # A contributed tool can report a severity of its own. An unknown
            # one sorts last rather than stopping the review.
            rank = (
                SEVERITIES.index(one.severity)
                if one.severity in SEVERITIES
                else len(SEVERITIES)
            )
            return (rank, one.path, one.start_line)

        shown = sorted(self.findings, key=worst_first)[:MOST_REPORTED]
        return (
            "## Static Analysis Observations\n"
            "Tools reported these potential issues. Verify them against the source "
            "and surrounding context; tool reports can be false positives. Avoid "
            "duplicating a report unless you establish a different issue.\n\n"
            + "\n".join(one.rendered() for one in shown)
        )


def about_the_change(analysis: "Analysis | None", touched) -> "Analysis | None":
    """Only what the tools found on lines this change actually wrote.

    A tool reads whole files, so most of what it reports was already there and
    is somebody else's to answer for. A review that raises it is talking about
    the repository rather than the change, and a threshold that counts it
    stops every branch in a codebase that has any history.
    """
    if not analysis or not analysis.findings:
        return analysis
    kept = tuple(
        one
        for one in analysis.findings
        if any(
            one.start_line <= line <= one.end_line for line in touched.get(one.path, ())
        )
    )
    if len(kept) != len(analysis.findings):
        logger.info(
            f"Ignored {len(analysis.findings) - len(kept)} findings on lines "
            "this change did not touch"
        )
    return replace(analysis, findings=kept)


def touched_lines(parsed_files) -> dict:
    """Which lines of which files the change wrote, as a tool would number them."""
    written = {}
    for one in parsed_files:
        if not one.file_path:
            continue
        written[one.file_path] = {
            line for line, side in one.commentable_lines if side == "RIGHT"
        }
    return written


def also_reported(review, analysis: "Analysis | None"):
    """Put what the tools found into the summary a reader actually sees.

    Called after the summary is settled, never before. A review's summary is
    written once by the reviewer and then replaced by the one that describes
    the whole change, so anything added to the first is thrown away.

    This has to happen for the filtering to be honest: a model finding is
    dropped for repeating a tool, so the tool has to be what says it instead.
    """
    if review is None or not analysis or not analysis.findings:
        return review
    if getattr(review, "execution", None):
        return review
    if getattr(review, "summary", None) is None:
        return review
    errors = [one for one in analysis.findings if one.severity == ERROR]
    rest = [one for one in analysis.findings if one.severity != ERROR]
    said = set(review.summary.critical_issues) | set(review.summary.minor_suggestions)

    def unsaid(findings):
        return [
            one.rendered().lstrip("- ")
            for one in findings[:MOST_REPORTED]
            if one.rendered().lstrip("- ") not in said
        ]

    review.summary.critical_issues = list(review.summary.critical_issues) + unsaid(
        errors
    )
    review.summary.minor_suggestions = list(review.summary.minor_suggestions) + unsaid(
        rest
    )
    return review


def analyzers(services: ServiceRegistry = service_registry) -> list[Analyzer]:
    contributed = list(services.contributions(Analyzer))
    return contributed or [SemgrepAnalyzer()]


def examine(
    root: Path,
    paths: Sequence[str],
    services: ServiceRegistry = service_registry,
) -> Analysis:
    """Run what is available over these files and gather what it found.

    A tool that is not installed, or that fails, is recorded as not having run
    rather than as having found nothing. The difference is the whole value of
    the answer: one of them means the change is clean.
    """

    def run(analyzer):
        name = getattr(analyzer, "name", type(analyzer).__name__)
        supports = getattr(analyzer, "supports", None)
        try:
            applicable = tuple(
                path for path in paths if supports is None or supports(path)
            )
        except Exception:
            return (), (), (name,), AnalyzerCoverage(name, unavailable=tuple(paths))
        unsupported = tuple(path for path in paths if path not in applicable)
        languages = tuple(getattr(analyzer, "languages", ()))
        checks = tuple(getattr(analyzer, "checks", ()))
        if not applicable:
            return (
                (),
                (),
                (),
                AnalyzerCoverage(
                    name, unsupported=unsupported, languages=languages, checks=checks
                ),
            )
        try:
            if not analyzer.available():
                return (
                    (),
                    (),
                    (name,),
                    AnalyzerCoverage(
                        name,
                        unsupported=unsupported,
                        unavailable=applicable,
                        languages=languages,
                        checks=checks,
                    ),
                )
            inspect = getattr(analyzer, "inspect", None)
            if inspect is not None:
                report = inspect(root, applicable)
                if not isinstance(report, AnalyzerReport):
                    raise ValueError("An analyzer must return AnalyzerReport")
                return (
                    report.findings,
                    (name,),
                    (),
                    replace(
                        report.coverage,
                        unsupported=(*unsupported, *report.coverage.unsupported),
                    ),
                )
            findings = tuple(analyzer.examine(root, applicable))
            return (
                findings,
                (name,),
                (),
                AnalyzerCoverage(
                    name,
                    paths=applicable if supports is not None else (),
                    unsupported=unsupported if supports is not None else tuple(paths),
                    languages=languages,
                    checks=checks,
                ),
            )
        except Exception:
            logger.warning("%s could not read this change", name, exc_info=True)
            return (
                (),
                (),
                (name,),
                AnalyzerCoverage(
                    name,
                    unsupported=unsupported,
                    unavailable=applicable,
                    languages=languages,
                    checks=checks,
                ),
            )

    outcomes = parallel_map(run, analyzers(services))
    from src.core.language_pack import detect_language

    file_languages = tuple((path, detect_language(path) or "unknown") for path in paths)
    return Analysis(
        findings=tuple(one for found, _, _, _ in outcomes for one in found),
        ran=tuple(one for _, ran, _, _ in outcomes for one in ran),
        unavailable=tuple(
            one for _, _, unavailable, _ in outcomes for one in unavailable
        ),
        coverage=tuple(coverage for _, _, _, coverage in outcomes),
        requested=tuple(paths),
        file_languages=file_languages,
    )


__all__ = [
    "ERROR",
    "MOST_REPORTED",
    "NOTE",
    "SEVERITIES",
    "WARNING",
    "Analysis",
    "Analyzer",
    "AnalyzerFinding",
    "SemgrepAnalyzer",
    "AnalyzerCoverage",
    "AnalyzerReport",
    "about_the_change",
    "also_reported",
    "analyzers",
    "touched_lines",
    "examine",
]
