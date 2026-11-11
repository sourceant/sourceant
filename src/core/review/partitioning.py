from dataclasses import dataclass
from hashlib import sha256
from typing import Callable
import re

from src.utils.diff_parser import parse_diff


@dataclass(frozen=True)
class PlannedReviewChunk:
    id: str
    files: tuple
    input_tokens: int


@dataclass(frozen=True)
class ArtifactTarget:
    path: str
    digest: str
    reason: str


@dataclass(frozen=True)
class ReviewPartition:
    chunks: tuple[PlannedReviewChunk, ...]
    artifacts: tuple[ArtifactTarget, ...]
    manifest: tuple[str, ...]
    digest: str


from .investigations import ChangeTooLarge


class PartitionCapacityError(ChangeTooLarge):
    pass


def partition(
    diff: str,
    *,
    cost: Callable[[str], int],
    input_budget: int,
    maximum_parts: int = 200,
    adjacency=None,
) -> ReviewPartition:
    if input_budget < 1 or maximum_parts < 1:
        raise ValueError("Partition budgets must be positive")
    from src.core.review.batch_recovery import split_batch

    files = parse_diff(diff)
    artifacts, pending, fitted = [], [], []
    for file in files:
        if artifact_file(file):
            artifacts.append(
                ArtifactTarget(
                    file.file_path,
                    sha256(file.diff_text.encode()).hexdigest(),
                    "binary or model data; inspect provenance and consumers",
                )
            )
        else:
            pending.append([file])
    by_path = {batch[0].file_path: batch for batch in pending}
    if len(by_path) != len(pending):
        raise PartitionCapacityError(
            "Duplicate file patches need an unambiguous review target"
        )
    ordered, visited = [], set()
    for seed in sorted(by_path):
        stack = [seed]
        while stack:
            path = stack.pop()
            if path in visited or path not in by_path:
                continue
            visited.add(path)
            ordered.append(by_path[path])
            stack.extend(sorted((adjacency or {}).get(path, ()), reverse=True))
    pending = ordered
    while pending:
        batch = pending.pop(0)
        size = cost("\n".join(file.diff_text for file in batch))
        if size <= input_budget:
            fitted.append((batch, size))
        else:
            halves = split_batch(batch)
            if not halves:
                raise PartitionCapacityError(
                    "A changed range cannot fit the model input budget"
                )
            pending[0:0] = list(halves)
        if len(fitted) > maximum_parts * max(1, len(files)):
            raise PartitionCapacityError(
                "The change needs more investigations to fit the reading budget than the configured partition capacity allows"
            )
    groups, current, running = [], [], 0
    for batch, size in fitted:
        candidate = current + batch
        combined = cost("\n".join(file.diff_text for file in candidate))
        if current and (
            combined > input_budget
            or {file.file_path for file in current} & {file.file_path for file in batch}
        ):
            groups.append((current, running))
            current, running = batch, size
        else:
            current, running = candidate, combined
    if current:
        groups.append((current, running))
    if len(groups) > maximum_parts:
        raise PartitionCapacityError(
            "The change needs more investigations to fit the reading budget than the configured partition capacity allows"
        )
    chunks = tuple(
        PlannedReviewChunk(
            sha256("\n".join(file.diff_text for file in batch).encode()).hexdigest(),
            tuple(batch),
            size,
        )
        for batch, size in groups
    )
    return ReviewPartition(
        chunks,
        tuple(artifacts),
        tuple(file.file_path for file in files),
        sha256(diff.encode()).hexdigest(),
    )


def change_map(partitioned, *, maximum_characters=16000):
    entries = []
    seen = set()
    for chunk in partitioned.chunks:
        for file in chunk.files:
            if file.file_path in seen:
                continue
            seen.add(file.file_path)
            symbols = re.findall(
                r"(?m)^[+ ]\s*(?:async\s+)?(?:def|class|function|interface|type)\s+(\w+)",
                file.diff_text,
            )
            entries.append(
                f"{file.file_path}: +{file._patched_file.added} -{file._patched_file.removed}; symbols: {', '.join(dict.fromkeys(symbols))[:400]}"
            )
    entries.extend(
        f"{artifact.path}: artifact; source reading omitted"
        for artifact in partitioned.artifacts
    )
    result = "\n".join(entries)
    if len(result) > maximum_characters:
        result = (
            result[:maximum_characters]
            + "\nRemaining change-map entries omitted to fit the context budget."
        )
    return result


def relationships(services, scope):
    from src.core.code_index import CodeIndexReader, CodeGraphReader, CodeGraphQuery

    if not scope.get("revision"):
        return {}
    try:
        reader = services.resolve(CodeIndexReader)
    except LookupError:
        return {}
    if not isinstance(reader, CodeGraphReader):
        return {}
    try:
        result = reader.graph(CodeGraphQuery(scope, include_tests=True))
    except Exception:
        return {}
    if result.truncated:
        return {}
    paths = {node.id: node.properties.get("file_path") for node in result.nodes}
    adjacent = {}
    for edge in result.edges:
        source, target = paths.get(edge.source_id), paths.get(edge.target_id)
        if source and target and source != target:
            adjacent.setdefault(source, set()).add(target)
            adjacent.setdefault(target, set()).add(source)
    return adjacent


def artifact_file(file):
    return file.is_binary_file or file.file_path.endswith(
        (".tiktoken", ".tflite", ".task")
    )


def artifact_checks(diff):
    from base64 import b64decode
    from pathlib import PurePosixPath

    files = parse_diff(diff)
    sources = [file for file in files if not artifact_file(file)]
    checks = []
    for file in files:
        if not artifact_file(file):
            continue
        gaps = ["Artifact provenance has not been verified"]
        consumers = tuple(
            source.file_path
            for source in sources
            if PurePosixPath(file.file_path).name in source.diff_text
        )
        if not consumers:
            gaps.append("No loader reference was found in changed source")
        integrity = False
        if file.file_path.endswith(".tiktoken") and file._patched_file.is_added_file:
            try:
                ranks = set()
                tokens = set()
                for hunk in file._patched_file:
                    for line in hunk:
                        if not line.is_added:
                            continue
                        token, rank = line.value.strip().split()
                        decoded, number = b64decode(token, validate=True), int(rank)
                        if number < 0 or number in ranks or decoded in tokens:
                            raise ValueError("Duplicate or invalid tokenizer entry")
                        ranks.add(number)
                        tokens.add(decoded)
                integrity = bool(ranks) and ranks == set(range(len(ranks)))
            except (ValueError, TypeError):
                integrity = False
        if not integrity:
            gaps.append("Artifact integrity could not be established from the patch")
        checks.append(
            {
                "path": file.file_path,
                "patch_digest": sha256(file.diff_text.encode()).hexdigest(),
                "integrity_verified": integrity,
                "changed_consumers": consumers,
                "gaps": tuple(gaps),
            }
        )
    return tuple(checks)


class DefaultReviewPartitionSource:
    partition = staticmethod(partition)


def partition_source(services):
    from .interfaces import ReviewPartitionSource

    try:
        return services.resolve(ReviewPartitionSource)
    except LookupError:
        return DefaultReviewPartitionSource()


def integration_packet(request, reviews, provider):
    import json

    limit = max(1000, reading_budget(request.changes.configuration, provider) // 2)
    entries = []
    for review in reviews:
        if review.result is None:
            continue
        entries.append(
            {
                "reading": f"{review.participant}:{review.repetition}",
                "summary": (
                    review.result.summary.overview[:1000]
                    if review.result.summary
                    else ""
                ),
                "findings": [
                    {
                        "path": finding.file_name,
                        "line": finding.start_line,
                        "claim": finding.comment[:600],
                    }
                    for finding in (review.result.code_suggestions or ())[:20]
                ],
            }
        )
    lines = [
        "Cross-file integration review. These are source excerpts and completed chunk findings, not a complete diff. "
        "Report only additional concrete interaction defects supported by these excerpts, using original file line numbers.",
        request.changes.title[:2000],
        request.changes.description[:6000],
    ]
    for file in parse_diff(request.changes.diff):
        sample = []
        for hunk in file._patched_file:
            for line in hunk:
                if line.is_added or line.is_removed:
                    sample.append(
                        f"{line.line_type} {line.target_line_no or line.source_line_no}: {line.value.rstrip()}"
                    )
                if len(sample) >= 12:
                    break
            if len(sample) >= 12:
                break
        item = f"File: {file.file_path}\n" + "\n".join(sample)[:1600]
        if provider.count_tokens("\n".join([*lines, item])) > limit:
            lines.append(
                "Additional source excerpts omitted to fit the integration budget."
            )
            break
        lines.append(item)
    for entry in entries:
        item = json.dumps(entry)
        if provider.count_tokens("\n".join([*lines, item])) > limit:
            lines.append(
                "Additional chunk findings omitted to fit the integration budget."
            )
            break
        lines.append(item)
    return "\n".join(lines)


def review_cost(provider):
    method = getattr(type(provider), "review_input_tokens", None)
    return (
        (lambda text: method(provider, text))
        if method
        else getattr(provider, "count_tokens", len)
    )


def reading_budget(configuration, provider):
    requested = int(configuration.value("review.reading_budget") or 50000)
    context = getattr(provider, "token_limit", None)
    if isinstance(context, int) and context > 0:
        output = int(configuration.value("review.output_tokens") or 8192)
        profile_output = getattr(provider, "_max_output_tokens", 0)
        if isinstance(profile_output, int):
            output = max(output, profile_output)
        requested = min(requested, context - output - 8192)
    if requested < 1:
        raise PartitionCapacityError(
            "The model context cannot fit the review instructions and output reserve"
        )
    return requested
