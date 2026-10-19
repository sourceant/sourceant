from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from pathlib import PurePosixPath
from typing import Callable

from src.utils.diff_parser import parse_diff
from src.core.code_index.models import is_test_path
from src.core.language_pack import detect_language
from .contracts import dependencies
from .exclusions import review_diff
from .execution import ReviewInput, ReviewInvestigation, ReviewTarget


class ChangeTooLarge(ValueError):
    """The change needs more investigations than a review may plan."""


@dataclass(frozen=True)
class ChangedBehaviorInvestigationSource:
    """Splits a change into investigations that each fit one reading.

    The configured number of independent readings is the least a change is
    split into; a change too large for that many readings is split further,
    up to `limit`.
    """

    cost: Callable[[str], int] | None = None

    def investigations_for(
        self, request: ReviewInput, limit: int
    ) -> tuple[ReviewInvestigation, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("Investigation count must be between one and 200")
        configuration = request.changes.configuration
        diff, _ = review_diff(request.changes.diff, configuration)
        files = [
            one for one in parse_diff(diff) if one.file_path and not one.is_binary_file
        ]
        if not files:
            return ()
        cost = self.cost or (lambda text: len(text) // 4)
        budget = configuration.value("review.reading_budget")
        needed = -(-sum(cost(one.diff_text) for one in files) // budget)
        count = min(
            len(files),
            max(min(configuration.value("review.discovery_passes"), limit), needed),
        )
        if configuration.value("review.investigation_allocation") == "dependencies":
            groups = related_groups(files, request, count)
        else:
            groups = balanced_groups(files, count)
        groups = [part for group in groups for part in _fitted(group, budget, cost)]
        if len(groups) > limit:
            raise ChangeTooLarge(
                f"The change needs {len(groups)} investigations to fit the reading "
                f"budget; at most {limit} are allowed"
            )
        tasks = []
        for group in groups:
            ordered = sorted(group, key=lambda one: one.file_path)
            identity = sha256(
                "\n".join(one.diff_text for one in ordered).encode()
            ).hexdigest()[:16]
            targets = tuple(
                ReviewTarget(one.file_path, tuple(one.hunk_ranges)) for one in ordered
            )
            locations = "\n".join(
                f"{target.path}: before {old_start}:{old_end}, head {start}:{end}"
                for target in targets
                for old_start, old_end, start, end in target.ranges
            )
            tasks.append(
                ReviewInvestigation(
                    name=f"behavior-{identity}",
                    targets=targets,
                    question=(
                        "Investigate the behavior changed at these locations:\n"
                        + locations
                        + "\nFor each changed behavior, follow the value or operation from its producer "
                        "through its real callers and consumers. Identify an input, runtime or failure "
                        "condition that makes the behavior incorrect, and the observable consequence. "
                        "When non-runtime concerns are enabled, establish the violated contract, "
                        "incorrect claim or concrete testing gap and its useful fix rather than "
                        "inventing a runtime failure. Check the relevant tests and repository "
                        "contracts against that chain. "
                        "Use head source to check guards outside the diff. If required evidence is "
                        "missing, identify the gap rather than asserting a defect. Return concrete "
                        "findings in the existing review format; an empty review is valid."
                    ),
                )
            )
        return tuple(tasks)


def target_source(
    task: ReviewInvestigation,
    read_content: Callable[[str], str | None] | None,
    max_chars: int,
) -> tuple[str, tuple[str, ...]]:
    excerpts, omitted = [], []
    remaining = max_chars
    for target in task.targets:
        path = PurePosixPath(target.path)
        if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
            raise ValueError("Investigation source must be repository relative")
        source = read_content(target.path) if read_content else None
        if not isinstance(source, str):
            omitted.append(
                f"{target.path}: head source unavailable (the file may be deleted)"
            )
            continue
        lines = source.splitlines()
        wanted = sorted(
            {
                line
                for _, _, start, end in target.ranges
                for line in range(
                    max(1, start - 8), min(len(lines), max(start, end) + 8) + 1
                )
            }
        )
        header = (
            "\n" if excerpts else ""
        ) + f"Investigation head source: {target.path}\n"
        if len(header) > remaining:
            omitted.append(f"{target.path}: source budget")
            continue
        remaining -= len(header)
        excerpt = []
        for line in wanted:
            rendered = f"{line}: {lines[line - 1]}\n"
            if len(rendered) > remaining:
                omitted.append(f"{target.path}:{line}: source budget")
                break
            excerpt.append(rendered)
            remaining -= len(rendered)
        excerpts.append(header + "".join(excerpt))
    return "".join(excerpts), tuple(omitted)


def _fitted(group, budget, cost):
    """A group in parts that each fit the budget, related files kept adjacent.

    A file larger than the whole budget goes alone rather than being dropped.
    """
    parts, current, running = [], [], 0
    for one in sorted(group, key=lambda one: one.file_path):
        size = cost(one.diff_text)
        if current and running + size > budget:
            parts.append(current)
            current, running = [], 0
        current.append(one)
        running += size
    if current:
        parts.append(current)
    return parts


def balanced_groups(files, limit):
    groups = [[] for _ in range(min(limit, len(files)))]
    weights = [0] * len(groups)
    for file in sorted(files, key=lambda one: (-len(one.diff_text), one.file_path)):
        selected = min(range(len(groups)), key=lambda index: (weights[index], index))
        groups[selected].append(file)
        weights[selected] += len(file.diff_text)
    return groups


def related_groups(files, request, limit):
    ordered = sorted(files, key=lambda one: (-len(one.diff_text), one.file_path))
    count = min(limit, len(files))
    code = [
        one
        for one in ordered
        if detect_language(one.file_path) not in (None, "markdown")
        and not is_test_path(one.file_path)
    ]
    seeds = (code + [one for one in ordered if one not in code])[:count]
    groups = [[one] for one in seeds]
    weights = [len(one.diff_text) for one in seeds]
    paths = {one.file_path for one in files}
    adjacency = {path: set() for path in paths}
    read_limit = request.changes.configuration.value(
        "review.structural_context_file_limit"
    )
    readable = (code + [one for one in ordered if one not in code])[:read_limit]
    contents = {}
    reference_weights = {}
    for file in readable:
        content = request.read_content(file.file_path) if request.read_content else None
        if not isinstance(content, str):
            continue
        contents[file.file_path] = content
        for choices in dependencies(file.file_path, content):
            for target in choices:
                if target in paths:
                    adjacency[file.file_path].add(target)
                    adjacency[target].add(file.file_path)
                    break

    for file in readable:
        if detect_language(file.file_path) != "markdown":
            continue
        content = contents.get(file.file_path, "")
        for target in code:
            source = contents.get(target.file_path, "")
            symbols = set(
                re.findall(
                    r"\b(?:export\s+(?:default\s+)?(?:async\s+)?(?:class|function|const|interface|type)|class|def)\s+([A-Za-z_]\w*)",
                    source,
                )
            )
            matches = sum(
                bool(re.search(r"\b" + re.escape(symbol) + r"\b", content))
                for symbol in sorted(symbols)[:64]
            )
            matches += int(target.file_path in content)
            if matches:
                adjacency[file.file_path].add(target.file_path)
                adjacency[target.file_path].add(file.file_path)
                reference_weights[file.file_path, target.file_path] = matches
                reference_weights[target.file_path, file.file_path] = matches

    if not any(adjacency.values()):
        return balanced_groups(files, limit)

    def module_name(path):
        name = PurePosixPath(path).stem
        for suffix in (".test", ".spec", "_test"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        return name.removeprefix("test_")

    remaining = [one for one in ordered if one not in seeds]
    remaining.sort(
        key=lambda one: (
            detect_language(one.file_path) in (None, "markdown"),
            is_test_path(one.file_path),
            -len(one.diff_text),
            one.file_path,
        )
    )
    target_weight = sum(len(one.diff_text) for one in files) / count
    for file in remaining:

        def priority(index):
            linked = [
                one
                for one in groups[index]
                if one.file_path in adjacency[file.file_path]
            ]
            affinity = sum(
                (
                    4
                    if is_test_path(file.file_path) != is_test_path(one.file_path)
                    and module_name(file.file_path) == module_name(one.file_path)
                    else (
                        2
                        if PurePosixPath(file.file_path).parent
                        == PurePosixPath(one.file_path).parent
                        else 1
                    )
                )
                * reference_weights.get((file.file_path, one.file_path), 1)
                for one in linked
            )
            exceeds = weights[index] + len(file.diff_text) > max(
                2 * target_weight, len(file.diff_text)
            )
            return (exceeds, -affinity, weights[index], index)

        selected = min(range(count), key=priority)
        groups[selected].append(file)
        weights[selected] += len(file.diff_text)
    return groups
