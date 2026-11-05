import json
from pathlib import Path

from litellm import ModelResponse

from src.core.review.batch_recovery import recover_batch, split_batch
from src.llms.litellm_provider import LiteLLMProvider
from src.models.code_review import CodeReview
from src.utils.diff_parser import parse_diff

FIXTURES = Path(__file__).parents[1] / "fixtures"


def patches():
    return parse_diff((FIXTURES / "review-overview/large.diff").read_text())


def test_splitting_preserves_every_changed_line_and_original_locations():
    for patch in patches():
        pending = [[patch]]
        leaves = []
        while pending:
            batch = pending.pop(0)
            parts = split_batch(batch)
            if parts:
                pending[0:0] = parts
            else:
                leaves.extend(batch)
        expected = [
            (line.line_type, line.source_line_no, line.target_line_no, line.value)
            for hunk in patch._patched_file
            for line in hunk
        ]
        actual = [
            (line.line_type, line.source_line_no, line.target_line_no, line.value)
            for leaf in leaves
            for one in parse_diff(leaf.diff_text)
            for hunk in one._patched_file
            for line in hunk
        ]
        assert actual == expected


def test_truncated_batch_recovers_without_repeating_successful_siblings(monkeypatch):
    provider = LiteLLMProvider("deepseek/deepseek-flash", 131072)
    exhausted = json.loads(
        (FIXTURES / "deepseek/output-budget-exhausted.json").read_text()
    )
    success = json.loads((FIXTURES / "deepseek/review.json").read_text())
    batch = patches()[:3]
    calls = []

    def read(parts):
        calls.append(tuple(one.file_path for one in parts))
        sample = exhausted if len(parts) > 1 else success
        monkeypatch.setattr(
            "src.llms.litellm_provider.litellm.completion",
            lambda **kwargs: ModelResponse(**sample),
        )
        return provider.generate_code_review(
            diff="\n".join(one.diff_text for one in parts), parsed_files=parts
        )

    outcome = recover_batch(read, batch)
    assert not outcome.gaps
    assert len(outcome.answers) == len(batch)
    assert all(isinstance(one, CodeReview) for one in outcome.answers)
    assert all(calls.count((one.file_path,)) == 1 for one in batch)


def test_unrecoverable_batch_preserves_successful_siblings(monkeypatch):
    from src.llms.errors import LLMError, OutputTruncated

    batch = patches()[:2]
    captured = json.loads((FIXTURES / "deepseek/review.json").read_text())
    answer = CodeReview.model_validate_json(
        captured["choices"][0]["message"]["content"]
    )

    def read(parts):
        if batch[0].file_path in [one.file_path for one in parts]:
            raise LLMError.wrapping("Review generation", OutputTruncated("truncated"))
        return answer

    result = recover_batch(read, batch, depth=2)
    assert result.answers == (answer,)
    assert result.gaps
    assert all(batch[0].file_path in gap for gap in result.gaps)
    assert all("output truncated" in gap for gap in result.gaps)


def test_cancellation_stops_recovery():
    calls = []
    result = recover_batch(
        lambda batch: calls.append(batch), patches()[:1], stopped=lambda: True
    )
    assert not calls
    assert not result.answers
    assert not result.gaps


def test_recovery_stops_at_the_call_budget():
    from src.llms.errors import OutputTruncated

    calls = []

    def read(batch):
        calls.append(batch)
        raise OutputTruncated("truncated")

    result = recover_batch(read, patches()[:4], max_calls=3)
    assert len(calls) == 3
    assert result.gaps
    assert any("recovery call budget exhausted" in gap for gap in result.gaps)
