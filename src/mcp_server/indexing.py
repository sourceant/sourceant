from dataclasses import asdict
from pathlib import Path
from threading import Lock
from typing import Any

from src.cli.index_commands import _excluded_paths
from src.core.code_index import CodeIndexWriter, CodeSearch
from src.core.code_index.interfaces import (
    CodeIndexDigestReader,
    PathScopedCodeIndexWriter,
)
from src.core.code_index.indexer import RepositoryIndexer
from src.core.environment import LOCAL


def add_index_tools(server, repositories, read_index):
    lock = Lock()

    @server.tool(
        name="get_index_status",
        description="Check whether a registered local repository has indexed files.",
        structured_output=True,
    )
    def get_index_status(repository: str) -> dict[str, Any]:
        entry = repositories.named(LOCAL, repository)
        result = read_index().search(
            CodeSearch(entry.scope, labels=frozenset({"File"}), limit=1)
        )
        return {
            "repository": entry.name,
            "indexed_files": result.total,
            "has_index": result.total > 0,
            "freshness": "unknown",
        }

    @server.tool(
        name="index_repository",
        description=(
            "Update the index of one registered local repository. Reparse changed "
            "files only. Returns after indexing completes; no source is uploaded."
        ),
        structured_output=True,
    )
    def index_repository(repository: str) -> dict[str, Any]:
        entry = repositories.named(LOCAL, repository)
        index = read_index()
        if not isinstance(index, CodeIndexWriter):
            raise ValueError("The configured index cannot be written to")
        if not isinstance(index, CodeIndexDigestReader) or not isinstance(
            index, PathScopedCodeIndexWriter
        ):
            raise ValueError(
                "The configured index does not support incremental updates"
            )
        if not lock.acquire(blocking=False):
            raise ValueError("An MCP index update is already running; retry later")
        try:
            result = RepositoryIndexer(index).index(
                entry.scope,
                Path(entry.path),
                update=True,
                excluded_paths=_excluded_paths(entry.name),
            )
        finally:
            lock.release()
        return {"repository": entry.name, "status": "complete", **asdict(result)}
