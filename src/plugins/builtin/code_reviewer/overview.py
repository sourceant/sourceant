import json

from src.core.parallel import parallel_map
from src.core.review.exclusions import review_diff

from src.core.settings.configuration import Configuration
from src.plugins.builtin.code_reviewer.reviewing import (
    CodeReviewer,
    MAX_AT_ONCE,
    _batched,
)
from src.utils.diff_parser import parse_diff
from src.models.code_review import CodeReviewSummary, summary_from


def summarize_changes(
    diff, provider, configuration: Configuration, metadata=None, suggestions=()
):
    diff, omitted = review_diff(diff, configuration)
    if omitted and not diff.strip():
        return CodeReviewSummary(
            overview="All changed files are excluded from review.",
            minor_suggestions=[],
            critical_issues=[],
        )
    batches = _batched(
        parse_diff(diff), CodeReviewer._budget(configuration), provider.count_tokens
    )
    if not batches:
        raise ValueError("The full pull request diff is unavailable")
    include_nitpicks = configuration.value("review.include_nitpicks") is True

    def describe(data, findings=()):
        written = provider.generate_summary(
            list(findings),
            change_context=json.dumps({"metadata": metadata or {}, **data}),
            include_nitpicks=include_nitpicks,
        )
        if not isinstance(written, CodeReviewSummary) or not written.overview.strip():
            raise ValueError("The model did not produce a pull request summary")
        return written

    def describe_batch(batch):
        return describe(
            {"diff": "\n".join(one.diff_text for one in batch)},
            suggestions if len(batches) == 1 else (),
        )

    descriptions = parallel_map(describe_batch, batches, MAX_AT_ONCE)
    while len(descriptions) > 1:
        descriptions = [
            describe(
                {
                    "parts_of_the_same_pull_request": [
                        item.model_dump() for item in descriptions[start : start + 10]
                    ]
                },
                suggestions if len(descriptions) <= 10 else (),
            )
            for start in range(0, len(descriptions), 10)
        ]
    return summary_from(list(suggestions), descriptions[0])
