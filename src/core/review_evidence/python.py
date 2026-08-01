from __future__ import annotations

import ast
from collections.abc import Callable

from .models import (
    EvidenceDecision,
    FileEvidence,
    ReviewClaim,
    StructuralFact,
    StructuralPredicate,
)


class CachedChangedFileEvidenceReader:
    def __init__(
        self,
        read_content: Callable[[str], str | None],
        *,
        character_limit: int = 500_000,
    ) -> None:
        if character_limit < 1:
            raise ValueError("character_limit must be positive")
        self._read_content = read_content
        self._character_limit = character_limit
        self._cache: dict[str, FileEvidence | None] = {}

    def read(self, path: str) -> FileEvidence | None:
        if path not in self._cache:
            self._cache[path] = self._extract(path)
        return self._cache[path]

    def _extract(self, path: str) -> FileEvidence | None:
        if not path.endswith(".py"):
            return None
        try:
            content = self._read_content(path)
        except (OSError, RuntimeError, ValueError):
            return None
        if not isinstance(content, str) or len(content) > self._character_limit:
            return None
        try:
            tree = ast.parse(content)
        except (SyntaxError, ValueError):
            return None

        facts: set[StructuralFact] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".", 1)[0]
                    local_name = alias.asname or module
                    facts.add(StructuralFact(module, StructuralPredicate.IMPORTED))
                    facts.add(StructuralFact(local_name, StructuralPredicate.IMPORTED))
                    facts.add(StructuralFact(local_name, StructuralPredicate.DEFINED))
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    facts.add(StructuralFact(local_name, StructuralPredicate.IMPORTED))
                    facts.add(StructuralFact(local_name, StructuralPredicate.DEFINED))
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                for target in _assignment_targets(node):
                    facts.add(StructuralFact(target, StructuralPredicate.DEFINED))
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                facts.add(StructuralFact(node.name, StructuralPredicate.DEFINED))
        return FileEvidence(
            path=path,
            language="python",
            facts=frozenset(facts),
            supported_predicates=frozenset(
                {StructuralPredicate.IMPORTED, StructuralPredicate.DEFINED}
            ),
        )


class StructuralReviewEvidenceValidator:
    def validate(
        self,
        claims: list[ReviewClaim],
        evidence: FileEvidence | None,
    ) -> EvidenceDecision:
        if evidence is None or not claims:
            return EvidenceDecision(False)
        for claim in claims:
            if claim.predicate not in evidence.supported_predicates:
                continue
            actual = StructuralFact(claim.subject, claim.predicate) in evidence.facts
            if actual != claim.expected:
                return EvidenceDecision(
                    True,
                    "post-change structure contradicts a factual claim",
                )

        return EvidenceDecision(False)


def _assignment_targets(node: ast.Assign | ast.AnnAssign | ast.NamedExpr) -> set[str]:
    roots = node.targets if isinstance(node, ast.Assign) else [node.target]
    names: set[str] = set()
    for root in roots:
        for target in ast.walk(root):
            if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store):
                names.add(target.id)
    return names
