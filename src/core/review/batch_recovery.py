from collections import deque
from copy import deepcopy
from dataclasses import dataclass

from unidiff.patch import Hunk

from src.llms.errors import OutputTruncated
from src.utils.diff_parser import ParsedDiff


def truncated(error: Exception) -> bool:
    seen = set()
    while error is not None and id(error) not in seen:
        if isinstance(error, OutputTruncated):
            return True
        seen.add(id(error))
        error = getattr(error, "cause", None) or error.__cause__
    return False


def split_batch(batch):
    if len(batch) > 1:
        middle = len(batch) // 2
        return batch[:middle], batch[middle:]
    patch = batch[0]._patched_file
    if not patch or patch.is_binary_file:
        return ()
    if len(patch) > 1:
        middle = len(patch) // 2
        halves = patch[:middle], patch[middle:]
    else:
        hunk = patch[0]
        lines = list(hunk)
        middle = len(lines) // 2
        while middle < len(lines) and lines[middle].line_type == "\\":
            middle += 1
        if not middle or middle == len(lines):
            return ()
        source = hunk.source_start + (hunk.source_length == 0)
        target = hunk.target_start + (hunk.target_length == 0)
        halves = []
        for part in (lines[:middle], lines[middle:]):
            source_length = sum(one.is_removed or one.is_context for one in part)
            target_length = sum(one.is_added or one.is_context for one in part)
            smaller = Hunk(
                source if source_length else source - 1,
                source_length,
                target if target_length else target - 1,
                target_length,
                hunk.section_header,
            )
            smaller.extend(part)
            halves.append([smaller])
            source += source_length
            target += target_length
    result = []
    for hunks in halves:
        smaller = deepcopy(patch)
        smaller.clear()
        smaller.extend(hunks)
        result.append([ParsedDiff(smaller)])
    return tuple(result)


@dataclass(frozen=True)
class BatchResult:
    answers: tuple = ()
    gaps: tuple[str, ...] = ()
    errors: tuple[Exception, ...] = ()


def recover_batch(
    read, batch, *, stopped=lambda: False, depth=6, max_calls=15
) -> BatchResult:
    pending = deque([(batch, depth)])
    answers, gaps, errors = [], [], []
    calls = 0
    while pending:
        if stopped():
            break
        part, remaining_depth = pending.popleft()
        reason = "recovery call budget exhausted"
        if calls < max_calls:
            calls += 1
            try:
                answer = read(part)
                if answer is None:
                    raise ValueError("The model returned no review")
            except Exception as error:
                errors.append(error)
                parts = (
                    split_batch(part) if remaining_depth and truncated(error) else ()
                )
                if parts:
                    pending.extend((smaller, remaining_depth - 1) for smaller in parts)
                    continue
                reason = (
                    "output truncated" if truncated(error) else type(error).__name__
                )
            else:
                answers.append(answer)
                continue
        locations = ", ".join(
            f"{one.file_path} ({', '.join(str(h).splitlines()[0] for h in one._patched_file)})"
            for one in part
        )
        gaps.append(f"{locations}: {reason}")
    return BatchResult(tuple(answers), tuple(gaps), tuple(errors))
