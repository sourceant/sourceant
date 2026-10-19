import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, BoundedSemaphore, Event
import subprocess

import pytest
from litellm import ModelResponse

from src.core import parallel
from src.core.review_performance import review_session
from src.llms.litellm_provider import LiteLLMProvider


def test_twenty_slots_are_shared_and_released_after_failure(monkeypatch):
    monkeypatch.setattr(parallel, "MODEL_CONCURRENCY", 20)
    monkeypatch.setattr(parallel, "model_slots", BoundedSemaphore(20))
    admitted = Barrier(21)

    def read(index):
        with parallel.model_call("test"):
            if index < 20:
                admitted.wait(timeout=5)
            if index == 0:
                raise RuntimeError("Call failed")
        return index

    with ThreadPoolExecutor(max_workers=25) as pool:
        first = [pool.submit(read, index) for index in range(20)]
        admitted.wait(timeout=5)
        remaining = [pool.submit(read, index) for index in range(20, 25)]
        with pytest.raises(RuntimeError, match="Call failed"):
            first[0].result()
        assert [one.result() for one in first[1:] + remaining] == list(range(1, 25))
    assert parallel._active_calls == 0


def test_auxiliary_calls_share_in_flight_work_but_discoveries_stay_independent(
    monkeypatch,
):
    captured = Path(__file__).parents[1] / "fixtures/deepseek/review.json"
    response = ModelResponse(**json.loads(captured.read_text()))
    started, release = Event(), Event()
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        started.set()
        assert release.wait(timeout=5)
        return response

    monkeypatch.setattr("litellm.completion", completion)
    provider = LiteLLMProvider("deepseek/deepseek-v4-flash", 131072)

    @review_session
    def read():
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = parallel.submit(
                pool,
                parallel.parallel_map,
                lambda index: provider.generate_text(
                    "Select skills", purpose="skill-selection"
                ),
                range(20),
                20,
            )
            assert started.wait(timeout=5)
            assert len(calls) == 1
            release.set()
            answers = future.result()
        assert len(answers) == 20
        assert len(calls) == 1
        parallel.parallel_map(
            lambda index: provider.generate_text(
                "Read independently", purpose="review"
            ),
            range(3),
        )
        assert len(calls) == 4

    read()
    provider.generate_text("Select skills", purpose="skill-selection")
    assert len(calls) == 5


def test_evaluation_groups_keep_global_indices_and_same_file_candidates(monkeypatch):
    from types import SimpleNamespace
    from src.core.review.evaluation import ModelReviewEvaluator
    from src.core.review.execution import (
        EvaluationStatus,
        FindingEvaluation,
        ReviewEvaluation,
    )

    candidates = [
        SimpleNamespace(file_name=path) for path in ("a.py", "b.py", "a.py", "c.py")
    ]
    request = SimpleNamespace(
        changes=SimpleNamespace(
            configuration=SimpleNamespace(value=lambda key: 2),
        )
    )
    groups = []
    evaluator = ModelReviewEvaluator(object())

    def evaluate(request, supplied, *, pass_id, indices):
        assert supplied is candidates
        assert pass_id == "evaluation:2"
        groups.append(indices)
        return ReviewEvaluation(
            tuple(
                FindingEvaluation(
                    index,
                    EvaluationStatus.SUPPORTED,
                    "Evidence",
                    ("Source",),
                    0 if index == 2 else None,
                )
                for index in indices
            )
        )

    monkeypatch.setattr(evaluator, "_evaluate", evaluate)
    answer = evaluator.evaluate(request, candidates, pass_id="evaluation:2")
    assert sorted(groups) == [(0, 2), (1, 3)]
    assert [one.candidate for one in answer.judgments] == [0, 1, 2, 3]
    assert answer.judgments[2].duplicate_of == 0


def test_snapshot_blob_batches_preserve_head_content_and_file_boundaries(
    tmp_path, monkeypatch
):
    from src.core.review import checkout

    def git(*arguments):
        return subprocess.check_output(["git", "-C", str(tmp_path), *arguments])

    git("init", "-q")
    (tmp_path / "space name.py").write_text("VALUE = 'é'\n")
    (tmp_path / "binary.dat").write_bytes(b"a\0b")
    (tmp_path / "invalid.txt").write_bytes(b"\xff")
    (tmp_path / "large.txt").write_text("x" * 1_000_001)
    (tmp_path / "link.py").symlink_to("space name.py")
    git("add", ".")
    git(
        "-c",
        "user.name=Reviewer",
        "-c",
        "user.email=review@example.com",
        "commit",
        "-qm",
        "test: Add snapshot fixtures",
    )
    head = git("rev-parse", "HEAD").decode().strip()
    (tmp_path / "space name.py").write_text("VALUE = 'changed'\n")
    original = checkout.git
    calls = []

    def read(root, *arguments, **options):
        calls.append(arguments)
        return original(root, *arguments, **options)

    monkeypatch.setattr(checkout, "git", read)
    paths, content = checkout.checkout_source(tmp_path, head)
    assert "link.py" not in paths
    assert content("space name.py") == "VALUE = 'é'\n"
    assert all(
        content(path) is None
        for path in (
            "binary.dat",
            "invalid.txt",
            "large.txt",
            "../space name.py",
            "missing.py",
        )
    )
    assert content("space name.py") == "VALUE = 'é'\n"
    assert calls == [("ls-tree", "-r", "-l", "-z", head), ("cat-file", "--batch")]
