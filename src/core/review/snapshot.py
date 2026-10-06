from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from src.core.change_context import ChangeSet, ChangedFile
from src.core.code_index import CodeIndexReader, InMemoryCodeIndex
from src.core.code_index.emit import emit_file_graph
from src.core.model import provider_for
from src.core.review.models import Told
from src.core.review_coverage import Coverage
from src.core.scope import Scope
from src.core.services import ServiceRegistry, service_registry
from src.core.settings.configuration import Configuration
from src.utils.diff_parser import parse_diff


@dataclass(frozen=True)
class SnapshotConfiguration(Configuration):
    overrides: Mapping[str, Any] = field(default_factory=dict)

    def value(self, key: str) -> Any:
        return self.overrides[key] if key in self.overrides else super().value(key)


def review_snapshot(
    *,
    repository: str,
    base: str,
    head: str,
    diff: str,
    files: Mapping[str, str],
    title: str,
    description: str,
    configuration: Configuration,
    services: ServiceRegistry = service_registry,
):
    from src.plugins.builtin.code_reviewer.reviewing import CodeReviewer
    from src.core.review import reviewer

    parsed = parse_diff(diff)
    changed = tuple(
        ChangedFile(one.file_path, properties={"patch": one.diff_text})
        for one in parsed
        if one.file_path
    )
    if not changed:
        raise ValueError("The snapshot contains no reviewable diff")
    scope = Scope.from_mapping(
        {"repository": repository, "workspace": configuration.workspace}
    )
    changes = ChangeSet(
        scope,
        changed,
        revision=head,
        base_revision=base,
        title=title,
        description=description,
        diff=diff,
        configuration=configuration,
    )
    index = InMemoryCodeIndex()
    for path, source in files.items():
        emit_file_graph(index, changes.code_scope, path, source)
    services = services.with_service(CodeIndexReader, index, "snapshot")
    model = provider_for(configuration, services, purpose="review")
    if model is None or model.missing_credentials():
        raise ValueError("A configured review model and credentials are required")
    judge = reviewer(services) or CodeReviewer(services=services)
    review = judge.review(
        changes,
        provider=model,
        read_content=files.get,
        code_scope=changes.code_scope,
        code_index=index,
        coverage=Coverage(),
        told=(
            Told(
                "Finding locations",
                "Anchor findings to the head revision using RIGHT-side line numbers. For a removed guard, anchor the issue to the surviving code whose behavior is affected.",
            ),
        ),
    )
    if review is None:
        raise RuntimeError("No reviewer completed the snapshot")
    execution = review.execution or {}
    if any(one["error"] for one in execution.get("reviews", ())):
        raise RuntimeError("A discovery participant failed")
    if any(
        one["error"]
        for one in execution.get("evaluations", ())
        if one["participant"] != "deterministic"
    ):
        raise RuntimeError("An evaluation participant failed")
    return review
