import json
from concurrent.futures import ThreadPoolExecutor

from src.plugins.builtin.code_reviewer.reviewing import (
    CodeReviewer,
    MAX_AT_ONCE,
    _batched,
)
from src.utils.diff_parser import parse_diff
from src.models.code_review import CodeReviewSummary


def summarize_pull_request(diff, provider, repository, metadata=None, suggestions=()):
    batches = _batched(
        parse_diff(diff), CodeReviewer._budget(repository), provider.count_tokens
    )
    if not batches:
        raise ValueError("The full pull request diff is unavailable")

    def describe(data, findings=()):
        written = provider.generate_summary(
            list(findings),
            change_context=json.dumps({"metadata": metadata or {}, **data}),
        )
        if not isinstance(written, CodeReviewSummary) or not written.overview.strip():
            raise ValueError("The model did not produce a pull request summary")
        return written

    def describe_batch(batch):
        return describe(
            {"diff": "\n".join(one.diff_text for one in batch)},
            suggestions if len(batches) == 1 else (),
        )

    with ThreadPoolExecutor(max_workers=min(len(batches), MAX_AT_ONCE)) as pool:
        descriptions = list(pool.map(describe_batch, batches))
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
    return descriptions[0]
