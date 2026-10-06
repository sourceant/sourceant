from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from src.core.change_context import ChangeSet, ChangedFile
from src.core.code_index import CodeIndexReader, InMemoryCodeIndex
from src.core.code_index.emit import emit_file_graph
from src.core.model import provider_for
from src.core.parallel import SharedReader
from src.core.review_coverage import Coverage
from src.core.review.models import Told
from src.core.scope import Scope
from src.core.services import ServiceRegistry
from src.core.settings.configuration import Configuration
from src.core.settings.definitions import get
from src.models.code_review import CodeReview
from src.utils.diff_parser import parse_diff


@dataclass(frozen=True)
class CheckoutConfiguration(Configuration):
    overrides: Mapping[str, Any] = field(default_factory=dict)

    def value(self, key: str) -> Any:
        return self.overrides.get(key, get(key).default)

    def with_workspace(self) -> CheckoutConfiguration:
        return self


def git(root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        timeout=120,
    )
    if completed.returncode:
        raise ValueError("The requested Git snapshot is unavailable")
    return completed.stdout


def read_checkout(
    root: Path,
    base: str,
    head: str,
    repository: str,
    *,
    diff: str | None = None,
    title: str = "",
    description: str = "",
    configuration: Configuration | None = None,
    services: ServiceRegistry | None = None,
) -> CodeReview:
    from src.plugins.builtin.code_reviewer.reviewing import CodeReviewer
    from src.core.review import reviewer

    root = root.resolve(strict=True)
    for revision in (base, head):
        if len(revision) != 40 or any(
            one not in "0123456789abcdef" for one in revision
        ):
            raise ValueError("Base and head must be full commit SHAs")
        if (
            git(root, "rev-parse", f"{revision}^{{commit}}").decode().strip()
            != revision
        ):
            raise ValueError("The requested commit does not match the snapshot")
    if git(root, "rev-parse", "HEAD").decode().strip() != head:
        raise ValueError("The checkout must be at the requested head")
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("The checkout contains changes outside the requested snapshot")
    if diff is None:
        diff = git(root, "diff", f"{base}...{head}", "--").decode("utf-8")
    parsed = parse_diff(diff)
    files = tuple(
        ChangedFile(one.file_path, properties={"patch": one.diff_text})
        for one in parsed
        if one.file_path
    )
    if not files:
        raise ValueError("The snapshot contains no reviewable diff")
    configuration = configuration or CheckoutConfiguration(repository=repository)
    changes = ChangeSet(
        Scope.from_mapping({"repository": repository}),
        files,
        revision=head,
        base_revision=base,
        diff=diff,
        title=title,
        description=description,
        configuration=configuration,
    )
    entries = {}
    for entry in git(root, "ls-tree", "-r", "-z", head).split(b"\0"):
        if not entry:
            continue
        info, path = entry.split(b"\t", 1)
        mode, kind, object_id = info.split(b" ")
        if kind == b"blob" and mode in (b"100644", b"100755"):
            entries[path.decode("utf-8")] = object_id.decode("ascii")

    def content(path: str) -> str | None:
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts or path not in entries:
            return None
        if int(git(root, "cat-file", "-s", entries[path])) > 1_000_000:
            return None
        raw = git(root, "cat-file", "blob", entries[path])
        if b"\0" in raw:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    read_content = SharedReader(content)
    services = services or ServiceRegistry()
    index = InMemoryCodeIndex()
    for path in entries:
        source = content(path)
        if source is not None:
            emit_file_graph(index, changes.code_scope, path, source)
    services = services.with_service(CodeIndexReader, index, "checkout")
    model = provider_for(configuration, services, purpose="review")
    if model is None or model.missing_credentials():
        raise ValueError("A configured review model and credentials are required")
    judge = reviewer(services) or CodeReviewer(services=services)
    review = judge.review(
        changes,
        provider=model,
        read_content=read_content,
        root=root,
        code_scope=changes.code_scope,
        coverage=Coverage(),
        code_index=index,
        told=(
            Told(
                "Finding locations",
                "Anchor findings to the head revision using RIGHT-side line numbers. For a removed guard, anchor the issue to the surviving code whose behavior is affected.",
            ),
        ),
    )
    if review is None:
        raise RuntimeError("No reviewer completed the snapshot")
    if review.execution and any(one["error"] for one in review.execution["reviews"]):
        raise RuntimeError("A discovery participant failed")
    if review.execution and any(
        one["error"]
        for one in review.execution["evaluations"]
        if one["participant"] != "deterministic"
    ):
        raise RuntimeError("An evaluation participant failed")
    return review
