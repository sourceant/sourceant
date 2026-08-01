from __future__ import annotations

import ast
from collections.abc import Callable

from .models import (
    FileEvidence,
    StructuralFact,
    StructuralPredicate,
)


class CachedPythonFileEvidenceReader:
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
        conditional_facts: set[StructuralFact] = set()
        _collect_facts(tree.body, facts, conditional_facts)
        return FileEvidence(
            path=path,
            language="python",
            facts=frozenset(facts),
            conditional_facts=frozenset(conditional_facts),
            supported_predicates=frozenset(
                {StructuralPredicate.IMPORTED, StructuralPredicate.DEFINED}
            ),
        )


def _collect_facts(
    nodes: list[ast.stmt],
    facts: set[StructuralFact],
    conditional_facts: set[StructuralFact],
    *,
    prefix: str = "",
    conditional: bool = False,
) -> None:
    target = conditional_facts if conditional else facts
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name.split(".", 1)[0]
                local_name = alias.asname or module
                target.add(
                    StructuralFact(
                        _qualified(prefix, module), StructuralPredicate.IMPORTED
                    )
                )
                target.add(
                    StructuralFact(
                        _qualified(prefix, local_name), StructuralPredicate.IMPORTED
                    )
                )
                target.add(
                    StructuralFact(
                        _qualified(prefix, local_name), StructuralPredicate.DEFINED
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                local_name = alias.asname or alias.name
                target.add(
                    StructuralFact(
                        _qualified(prefix, local_name), StructuralPredicate.IMPORTED
                    )
                )
                target.add(
                    StructuralFact(
                        _qualified(prefix, local_name), StructuralPredicate.DEFINED
                    )
                )
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for name in _assignment_targets(node):
                target.add(
                    StructuralFact(
                        _qualified(prefix, name), StructuralPredicate.DEFINED
                    )
                )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            target.add(
                StructuralFact(
                    _qualified(prefix, node.name), StructuralPredicate.DEFINED
                )
            )
        elif isinstance(node, ast.ClassDef):
            class_name = _qualified(prefix, node.name)
            target.add(StructuralFact(class_name, StructuralPredicate.DEFINED))
            _collect_facts(
                node.body,
                facts,
                conditional_facts,
                prefix=class_name,
                conditional=conditional,
            )
        elif isinstance(node, (ast.If, ast.With, ast.AsyncWith)):
            _collect_facts(
                node.body,
                facts,
                conditional_facts,
                prefix=prefix,
                conditional=True,
            )
            _collect_facts(
                node.orelse if isinstance(node, ast.If) else [],
                facts,
                conditional_facts,
                prefix=prefix,
                conditional=True,
            )
        elif isinstance(node, ast.Try):
            branches = [node.body, node.orelse, node.finalbody]
            branches.extend(handler.body for handler in node.handlers)
            for branch in branches:
                _collect_facts(
                    branch,
                    facts,
                    conditional_facts,
                    prefix=prefix,
                    conditional=True,
                )


def _assignment_targets(node: ast.Assign | ast.AnnAssign | ast.NamedExpr) -> set[str]:
    roots = node.targets if isinstance(node, ast.Assign) else [node.target]
    names: set[str] = set()
    for root in roots:
        for target in ast.walk(root):
            if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store):
                names.add(target.id)
    return names


def _qualified(prefix: str, name: str) -> str:
    return f"{prefix}.{name}" if prefix else name
