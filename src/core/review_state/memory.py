from src.core.scope import Scope

from .models import FindingQuery, FindingResult, ReviewFinding


class InMemoryReviewStateRepository:
    def __init__(self) -> None:
        self._findings: dict[tuple[Scope, str], ReviewFinding] = {}

    def put_finding(self, scope: Scope, finding: ReviewFinding) -> None:
        self._findings[(scope, finding.id)] = finding

    def search(self, query: FindingQuery) -> FindingResult:
        matches = [
            finding
            for (scope, _), finding in self._findings.items()
            if scope == query.scope
            and (not query.states or finding.state in query.states)
            and all(
                finding.properties.get(key) == value
                for key, value in query.properties.items()
            )
        ]
        findings = tuple(matches[query.offset : query.offset + query.limit])
        return FindingResult(
            findings=findings,
            total=len(matches),
            has_more=query.offset + len(findings) < len(matches),
        )
