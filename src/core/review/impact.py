"""What a change reaches, read from the code alone.

No model is asked: the changed definitions come from the parser, and who calls
them from the code index or, failing that, from matching names, each saying
which. The reader of a change gets the consumers before any finding, and can
check every one of them against the source.
"""

from __future__ import annotations

from pathlib import PurePosixPath
import re
from typing import Callable

from src.utils.diff_parser import parse_diff

from .call_context import call_context

TEST = re.compile(
    r"(^|/)(tests?|specs?|__tests__|testing)/|(^|/)test_[^/]*$|[._-](test|spec)s?\.[^/.]+$",
    re.I,
)


def is_test(path: str) -> bool:
    return bool(TEST.search(path))


def _link(link) -> dict:
    return {
        "path": link.path,
        "name": link.name,
        "line": link.around,
        "basis": link.basis,
    }


def impact(
    diff: str,
    repository: str,
    read_content: Callable[[str], str | None],
    *,
    revision: str = "",
    reader=None,
    searcher=None,
) -> dict:
    parsed = [one for one in parse_diff(diff) if one.file_path]
    files = []
    for one in parsed:
        lines = one.diff_text.splitlines()
        added = sum(1 for line in lines if line[:1] == "+" and line[:3] != "+++")
        removed = sum(1 for line in lines if line[:1] == "-" and line[:3] != "---")
        files.append(
            {
                "path": one.file_path,
                "additions": added,
                "deletions": removed,
                "binary": bool(one.is_binary_file),
                "test": is_test(one.file_path),
                "added": "\n--- /dev/null" in "\n" + one.diff_text,
            }
        )
    changed = {one["path"] for one in files}
    context = call_context(
        diff,
        repository,
        read_content,
        revision=revision,
        reader=reader,
        searcher=searcher,
    )
    definitions = []
    consumers: dict[tuple[str, str], dict] = {}
    for path, found in context.changed.items():
        for definition in found:
            key = (path, definition.name, definition.start)
            callers = [_link(one) for one in context.callers.get(key, ())]
            definitions.append(
                {
                    "path": path,
                    "name": definition.name,
                    "kind": definition.kind,
                    "start": definition.start,
                    "end": definition.end,
                    "signature": definition.signature,
                    "callers": callers,
                    "callees": [_link(one) for one in context.callees.get(key, ())],
                }
            )
            for caller in callers:
                if caller["path"] in changed:
                    continue
                consumer = consumers.setdefault(
                    (caller["path"], caller["name"]),
                    {**caller, "test": is_test(caller["path"]), "reaches": []},
                )
                consumer["reaches"].append(f"{path}:{definition.name}")
    reached = sorted(
        {str(PurePosixPath(path).parent) for path in changed}
        | {str(PurePosixPath(one["path"]).parent) for one in consumers.values()}
    )
    return {
        "files": files,
        "definitions": definitions,
        "consumers": list(consumers.values()),
        "tests_touched": [one["path"] for one in files if one["test"]],
        "folders": reached,
    }
