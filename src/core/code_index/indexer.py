from __future__ import annotations

import hashlib
import subprocess
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from src.core.scope import Scope

from .emit import DEFAULT_FILE_CHARACTER_LIMIT, emit_file_graph
from .interfaces import (
    BulkCodeIndexWriter,
    CodeIndexDigestReader,
    CodeIndexWriter,
    PathScopedCodeIndexWriter,
)
from .models import is_excluded_path

UNWALKED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
        "vendor",
    }
)


@dataclass(frozen=True)
class IndexResult:
    indexed: int
    unchanged: int
    removed: int
    skipped: int


class RepositoryIndexer:
    def __init__(
        self,
        writer: CodeIndexWriter,
        *,
        character_limit: int = DEFAULT_FILE_CHARACTER_LIMIT,
    ) -> None:
        self._writer = writer
        self._character_limit = character_limit

    def index(
        self,
        scope: Scope,
        root: Path,
        *,
        update: bool = False,
        excluded_paths: frozenset[str] = frozenset(),
    ) -> IndexResult:
        root = Path(root).resolve()
        if not root.is_dir():
            raise ValueError(f"{root} is not a directory")

        paths = [
            path
            for path in _repository_files(root)
            if not is_excluded_path(path, excluded_paths)
        ]
        known = self._known_digests(scope) if update else {}
        if not update:
            self._writer.clear(scope)

        indexed = unchanged = skipped = 0
        seen: set[str] = set()
        with self._batch():
            for path in paths:
                content = _read(root / path)
                if content is None:
                    skipped += 1
                    continue
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                seen.add(path)
                if update and known.get(path) == digest:
                    unchanged += 1
                    continue
                if update:
                    self._remove_path(scope, path)
                if emit_file_graph(
                    self._writer,
                    scope,
                    path,
                    content,
                    character_limit=self._character_limit,
                    digest=digest,
                ):
                    indexed += 1
                else:
                    skipped += 1

        removed = 0
        if update:
            for path in sorted(set(known) - seen):
                self._remove_path(scope, path)
                removed += 1
        return IndexResult(
            indexed=indexed, unchanged=unchanged, removed=removed, skipped=skipped
        )

    def _batch(self):
        if isinstance(self._writer, BulkCodeIndexWriter):
            return self._writer.bulk_writes()
        return nullcontext()

    def _known_digests(self, scope: Scope) -> dict[str, str]:
        if isinstance(self._writer, CodeIndexDigestReader):
            return self._writer.file_digests(scope)
        return {}

    def _remove_path(self, scope: Scope, path: str) -> None:
        if isinstance(self._writer, PathScopedCodeIndexWriter):
            self._writer.remove_path(scope, path)


def _repository_files(root: Path) -> list[str]:
    tracked = _git_files(root)
    if tracked is not None:
        return tracked
    return _walked_files(root)


def _git_files(root: Path) -> list[str] | None:
    if not (root / ".git").exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return sorted(
        line for line in completed.stdout.splitlines() if line and not line.isspace()
    )


def _walked_files(root: Path) -> list[str]:
    found: list[str] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in UNWALKED_DIRECTORIES:
                    stack.append(entry)
            elif entry.is_file():
                found.append(entry.relative_to(root).as_posix())
    return sorted(found)


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > DEFAULT_FILE_CHARACTER_LIMIT * 4:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return None
