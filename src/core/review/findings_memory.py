from dataclasses import replace

from src.core.scope import Scope

from .findings import FindingQuery, FindingResult, ReviewFinding


class InMemoryFindingStore:
    def __init__(self) -> None:
        self._findings: dict[tuple[Scope, str], ReviewFinding] = {}

    def put_finding(self, scope: Scope, finding: ReviewFinding) -> None:
        known = self._findings.get((scope, finding.id))
        # Whatever was decided about it stands. Only set_state changes that.
        state = known.state if known else finding.state
        self._findings[(scope, finding.id)] = replace(finding, state=state)

    def get_finding(self, scope: Scope, identifier: str) -> ReviewFinding | None:
        return self._findings.get((scope, identifier))

    def set_state(self, scope: Scope, identifier: str, state: str) -> bool:
        known = self._findings.get((scope, identifier))
        if known is None:
            return False
        self._findings[(scope, identifier)] = replace(known, state=state)
        return True

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
