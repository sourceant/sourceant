import json

from src.core.parallel import parallel_map
from src.core.review.exclusions import review_diff
from src.core.review.planning import build_chunks, reading_budget

from src.core.settings.configuration import Configuration
from src.core.services import service_registry
from src.plugins.builtin.code_reviewer.reviewing import (
    MAX_AT_ONCE,
)
from src.utils.diff_parser import parse_diff
from src.models.code_review import CodeReviewSummary, summary_from
from src.core.review_performance import background, stage, shared


def prepare_overview(
    diff, provider, configuration, metadata=None, *, services=service_registry
):
    provider, batches = _parts(diff, provider, configuration, services)
    if len(batches) < 2:
        return None

    def prepare():
        with stage("overview-preparation", batches=len(batches)):
            return _descriptions(
                batches,
                provider,
                configuration,
                metadata,
                (),
                workers=max(1, MAX_AT_ONCE // 4),
            )

    return background(prepare)


def _parts(diff, provider, configuration, services):
    from src.core.model import purpose_provider

    provider = purpose_provider(configuration, services, "review-overview", provider)
    diff, omitted = review_diff(diff, configuration)
    parsed = shared(("parsed-diff", diff), lambda: parse_diff(diff))
    model = getattr(provider, "model", id(provider))
    budget = reading_budget(configuration, provider)

    def cost(text):
        return shared(("diff-tokens", model, text), lambda: provider.count_tokens(text))

    batches = shared(
        ("batches", model, budget, tuple(one.diff_text for one in parsed)),
        lambda: [
            list(chunk.parsed_files)
            for chunk in build_chunks(
                diff,
                cost=cost,
                input_budget=budget,
                maximum_parts=int(configuration.value("review.maximum_parts") or 64),
            )
        ],
    )
    return provider, batches


def _describe(provider, configuration, metadata, data, findings=()):
    written = provider.generate_summary(
        list(findings),
        change_context=json.dumps({"metadata": metadata or {}, **data}),
        include_nitpicks=configuration.value("review.include_nitpicks") is True,
    )
    if not isinstance(written, CodeReviewSummary) or not written.overview.strip():
        raise ValueError("The model did not produce a pull request summary")
    return written


def _descriptions(
    batches, provider, configuration, metadata, suggestions, *, workers=MAX_AT_ONCE
):
    def describe_batch(batch):
        return _describe(
            provider,
            configuration,
            metadata,
            {"diff": "\n".join(one.diff_text for one in batch)},
            suggestions if len(batches) == 1 else (),
        )

    return parallel_map(describe_batch, batches, workers)


def summarize_changes(
    diff,
    provider,
    configuration: Configuration,
    metadata=None,
    suggestions=(),
    *,
    services=service_registry,
    prepared=None,
):
    diff, omitted = review_diff(diff, configuration)
    if omitted and not diff.strip():
        return CodeReviewSummary(
            overview="All changed files are excluded from review.",
            minor_suggestions=[],
            critical_issues=[],
        )
    provider, batches = _parts(diff, provider, configuration, services)
    if not batches:
        raise ValueError("The full pull request diff is unavailable")
    descriptions = (
        prepared.result()
        if prepared is not None
        else _descriptions(batches, provider, configuration, metadata, suggestions)
    )
    while len(descriptions) > 1:
        groups = _summary_groups(
            descriptions, provider, reading_budget(configuration, provider)
        )
        descriptions = [
            _describe(
                provider,
                configuration,
                metadata,
                {"parts_of_the_same_pull_request": group},
                suggestions if len(groups) == 1 else (),
            )
            for group in groups
        ]
    summary = summary_from(list(suggestions), descriptions[0])
    changed = {one.file_path for one in parse_diff(diff) if one.file_path}
    # A behavior is only as good as the code it points at; one placed outside
    # this diff cannot be checked by its reader, so it is not shown.
    anchored = []
    for behavior in summary.behaviors:
        located = [
            where for where in behavior.locations if where.rpartition(":")[0] in changed
        ]
        if located:
            anchored.append(behavior.model_copy(update={"locations": located}))
    summary.behaviors = anchored
    return summary


def _summary_groups(descriptions, provider, budget):
    from src.core.review.planning import ReviewCapacityError

    groups, current = [], []
    for description in descriptions:
        record = description.model_dump(mode="json")
        limit = max(32, budget // 3)
        if provider.count_tokens(json.dumps(record)) > limit:
            record = {"overview": "", "omitted_summary_details": True}
            if provider.count_tokens(json.dumps(record)) > limit:
                raise ReviewCapacityError("A summary cannot fit the reduction budget")
            low, high = 0, len(description.overview)
            while low <= high:
                middle = (low + high) // 2
                candidate = {**record, "overview": description.overview[:middle]}
                if provider.count_tokens(json.dumps(candidate)) <= limit:
                    record = candidate
                    low = middle + 1
                else:
                    high = middle - 1
        combined = {"parts_of_the_same_pull_request": [*current, record]}
        if current and provider.count_tokens(json.dumps(combined)) > budget:
            groups.append(current)
            current = []
        current.append(record)
    if current:
        groups.append(current)
    if len(groups) >= len(descriptions):
        raise ReviewCapacityError(
            "Summary reduction cannot make progress within its budget"
        )
    return groups
