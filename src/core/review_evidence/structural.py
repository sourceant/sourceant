from __future__ import annotations

import re
from collections.abc import Callable

from tree_sitter_language_pack import Error, ProcessConfig, detect_language, process

from .models import (
    EvidenceDecision,
    FileEvidence,
    ReviewClaim,
    StructuralFact,
    StructuralPredicate,
)


class CachedChangedFileEvidenceReader:
    _IMPORT_LANGUAGES = frozenset(
        {"java", "javascript", "php", "python", "typescript", "tsx"}
    )

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
        language = detect_language(path)
        if language is None:
            return None
        try:
            content = self._read_content(path)
        except (OSError, RuntimeError, ValueError):
            return None
        if not isinstance(content, str) or len(content) > self._character_limit:
            return None
        try:
            result = process(content, ProcessConfig(language=language))
        except Error:
            return None

        facts: set[StructuralFact] = set()
        _collect_structure(result.structure, facts)
        for name in _imported_names(language, content, result.imports):
            facts.add(StructuralFact(name, StructuralPredicate.IMPORTED))
            facts.add(StructuralFact(name, StructuralPredicate.DEFINED))
        supported_predicates = {StructuralPredicate.DEFINED}
        if language in self._IMPORT_LANGUAGES:
            supported_predicates.add(StructuralPredicate.IMPORTED)
        return FileEvidence(
            path=path,
            language=language,
            facts=frozenset(facts),
            supported_predicates=frozenset(supported_predicates),
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
            if actual and not claim.expected:
                return EvidenceDecision(
                    True,
                    "post-change structure contradicts a factual claim",
                )
        return EvidenceDecision(False)


def _collect_structure(items, facts: set[StructuralFact], prefix: str = "") -> None:
    for item in items:
        name = f"{prefix}.{item.name}" if prefix else item.name
        facts.add(StructuralFact(name, StructuralPredicate.DEFINED))
        _collect_structure(item.children, facts, name)


def _imported_names(language: str, content: str, imports) -> set[str]:
    if language in {"javascript", "typescript", "tsx"}:
        return _javascript_imports(imports)
    if language == "java":
        return _java_imports(imports)
    if language == "php":
        return _php_imports(content)
    if language == "python":
        return _python_imports(imports)
    return set()


def _javascript_imports(imports) -> set[str]:
    names: set[str] = set()
    for item in imports:
        statement = item.source.strip().rstrip(";")
        match = re.match(r"import\s+(?:type\s+)?(.+?)\s+from\s+", statement)
        if not match:
            continue
        clause = match.group(1).strip()
        namespace = re.search(r"\*\s+as\s+([A-Za-z_$][\w$]*)", clause)
        if namespace:
            names.add(namespace.group(1))
        named = re.search(r"\{([^}]*)\}", clause)
        if named:
            for entry in named.group(1).split(","):
                parts = re.split(r"\s+as\s+", entry.strip())
                if parts and parts[0]:
                    names.add(parts[-1].strip())
        default = clause.split(",", 1)[0].strip()
        if default and not default.startswith(("{", "*")):
            names.add(default)
    return names


def _java_imports(imports) -> set[str]:
    names: set[str] = set()
    for item in imports:
        statement = item.source.strip().rstrip(";")
        match = re.match(r"import\s+(?:static\s+)?([\w.]+)$", statement)
        if match and not match.group(1).endswith(".*"):
            names.add(match.group(1).rsplit(".", 1)[-1])
    return names


def _php_imports(content: str) -> set[str]:
    names: set[str] = set()
    for match in re.finditer(
        r"(?m)^\s*use\s+(?!function\s|const\s)([\\\w]+)(?:\s+as\s+(\w+))?\s*;",
        content,
    ):
        names.add(match.group(2) or match.group(1).rsplit("\\", 1)[-1])
    return names


def _python_imports(imports) -> set[str]:
    names: set[str] = set()
    for item in imports:
        statement = item.source.strip()
        direct = re.match(r"import\s+(.+)$", statement)
        if direct:
            for entry in direct.group(1).split(","):
                parts = re.split(r"\s+as\s+", entry.strip())
                name = parts[-1] if len(parts) > 1 else parts[0].split(".", 1)[0]
                if name:
                    names.add(name)
            continue
        imported = re.match(r"from\s+[.\w]+\s+import\s+(.+)$", statement)
        if imported:
            for entry in imported.group(1).strip("()").split(","):
                parts = re.split(r"\s+as\s+", entry.strip())
                if parts and parts[0] != "*":
                    names.add(parts[-1])
    return names
