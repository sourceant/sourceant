"""Callers and callees of what a change touched, from the best source on hand.

The stored code graph answers first wherever it knows a changed definition:
its calls were resolved when the repository was indexed. What it does not know
(a language it cannot read, a definition new in this change) is answered from
the change's own source: a text search finds where the name is written, and
the parser keeps only places where it is called. Those answers match by name,
so they are labelled as such.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
import re
from typing import Callable, Sequence

from src.core.code_index import CodeSearch, CodeTraversal
from src.core.scope import Scope
from src.core.search import SearchQuery
from src.utils.diff_parser import parse_diff
from src.utils.logger import logger

from .calls import Definition, calls, changed_definitions, definitions, enclosing
from .calls import excerpt

GRAPH = "resolved by the code index"
NAMED = "matched by name"
PER_DEFINITION = 4
EXCERPT_LINES = 30


@dataclass(frozen=True)
class Link:
    path: str
    name: str
    start: int
    end: int
    around: int
    basis: str


@dataclass
class CallContext:
    changed: dict[str, list[Definition]] = field(default_factory=dict)
    callers: dict[tuple[str, str, int], list[Link]] = field(default_factory=dict)
    callees: dict[tuple[str, str, int], list[Link]] = field(default_factory=dict)

    def rendered(
        self,
        paths: Sequence[str],
        read_content: Callable[[str], str | None],
        max_chars: int,
    ) -> str | None:
        queues = []
        for path in paths:
            for definition in self.changed.get(path, ()):
                key = (path, definition.name, definition.start)
                for title, links in (
                    ("called by", self.callers.get(key, ())),
                    ("calls", self.callees.get(key, ())),
                ):
                    if links:
                        queues.append((path, definition, title, list(links)))
        # In turns, so a budget spent on one definition's many callers still
        # leaves every other changed definition its nearest one.
        blocks, used = [], 0
        while queues and used < max_chars:
            waiting = []
            for path, definition, title, links in queues:
                link = links.pop(0)
                content = read_content(link.path)
                if isinstance(content, str):
                    block = (
                        f"{path}:{definition.start} {definition.name} {title} "
                        f"{link.name} at {link.path}:{link.around} ({link.basis})\n"
                        + excerpt(
                            content, link.start, link.end, link.around, EXCERPT_LINES
                        )
                    )
                    if used + len(block) <= max_chars:
                        blocks.append(block)
                        used += len(block)
                if links:
                    waiting.append((path, definition, title, links))
            queues = waiting
        if not blocks:
            return None
        return (
            "Callers and callees of the definitions this change touched. Each "
            "says how it was found.\n\n" + "\n\n".join(blocks)
        )


def _graph(reader, scopes, changed, read_content, context) -> set[tuple[str, str, int]]:
    """Answer from the stored graph; the definitions it answered for.

    An index filed at the revision under review is preferred to one filed for
    the repository as last read.
    """
    answered = set()
    for path, found in changed.items():
        try:
            files, scope = (), None
            for scope in scopes:
                files = reader.search(
                    CodeSearch(
                        scope=scope,
                        labels=frozenset({"File"}),
                        properties={"file_path": path},
                        limit=1,
                    )
                ).nodes
                if files:
                    break
            if not files:
                continue
            symbols = reader.traverse(
                CodeTraversal(
                    scope=scope,
                    node_ids=(files[0].id,),
                    depth=2,
                    edge_types=frozenset({"DEFINES", "DEFINES_METHOD"}),
                    direction="outbound",
                    node_limit=100,
                )
            ).nodes
        except Exception as error:  # noqa: BLE001 - any index may be unavailable
            logger.info(f"No indexed calls for {path}: {error}")
            continue
        by_name = {}
        for node in symbols:
            if node.properties.get("file_path") == path:
                by_name.setdefault(node.properties.get("name"), node)
        for definition in found:
            node = by_name.get(definition.name)
            if node is None:
                continue
            key = (path, definition.name, definition.start)
            for direction, target in (
                ("inbound", context.callers),
                ("outbound", context.callees),
            ):
                try:
                    near = reader.traverse(
                        CodeTraversal(
                            scope=scope,
                            node_ids=(node.id,),
                            depth=1,
                            edge_types=frozenset({"CALLS"}),
                            direction=direction,
                            node_limit=PER_DEFINITION + 1,
                        )
                    )
                except Exception:  # noqa: BLE001 - as above
                    continue
                links = []
                for one in near.nodes:
                    where = one.properties.get("file_path")
                    if one.id == node.id or not where:
                        continue
                    start = int(one.properties.get("start_line") or 1)
                    end = int(one.properties.get("end_line") or start)
                    if where in changed:
                        # The index read the branch before this change, so a
                        # changed file's lines are found again by name.
                        content = read_content(where)
                        moved = next(
                            (
                                found_again
                                for found_again in (
                                    definitions(where, content)
                                    if isinstance(content, str)
                                    else ()
                                )
                                if found_again.name == one.properties.get("name")
                            ),
                            None,
                        )
                        if moved is None:
                            continue
                        start, end = moved.start, moved.end
                    links.append(
                        Link(
                            where,
                            str(one.properties.get("name")),
                            start,
                            end,
                            start,
                            GRAPH,
                        )
                    )
                if links:
                    target[key] = links[:PER_DEFINITION]
                    answered.add(key)
    return answered


def _named(searcher, scope, changed, read_content, context, answered) -> None:
    """Answer by name for what the graph could not."""
    contents: dict[str, str | None] = {}

    def read(path):
        if path not in contents:
            contents[path] = read_content(path)
        return contents[path]

    def candidates(name):
        paths = list(changed)
        if searcher is not None:
            # The revision under review first; a deployment that indexes only
            # the default branch answers for the repository as last read.
            for where in (
                scope,
                Scope.from_mapping({"repository": scope.get("repository")}),
            ):
                try:
                    result = searcher.search(
                        SearchQuery(scope=where, terms=(name,), limit=20)
                    )
                except (ValueError, RuntimeError, OSError) as error:
                    logger.info(f"No text search for {name}: {error}")
                    break
                if result.unavailable is None:
                    paths.extend(match.path for match in result.matches)
                    break
        return list(dict.fromkeys(paths))

    def nearness(origin, path):
        mine, theirs = PurePosixPath(origin).parts, PurePosixPath(path).parts
        shared = 0
        for one, other in zip(mine[:-1], theirs[:-1]):
            if one != other:
                break
            shared += 1
        return (path != origin, -shared, path)

    def refers_to(caller, content, path, rivals):
        """Whether a file that calls a shared name means this definition.

        Where other files define the same name, a call counts only from this
        file's own folder or from a file that names this file's module the way
        an import or qualified call does.
        """
        here = PurePosixPath(path)
        if PurePosixPath(caller).parent == here.parent:
            return True
        stem, folder = here.stem, here.parent.name
        if re.search(rf"\b{re.escape(folder)}[./]{re.escape(stem)}\b", content):
            return True
        return False

    homes: dict[str, list[str]] = {}
    for path, found in changed.items():
        for definition in found:
            homes.setdefault(definition.name, []).append(path)

    def credited(name, caller, path):
        """Whether a call by name belongs to this file's definition, when
        several changed files define the same name."""
        return min(homes[name], key=lambda one: nearness(caller, one)) == path

    for path, found in changed.items():
        for definition in found:
            key = (path, definition.name, definition.start)
            if key in answered or len(definition.name) < 3:
                continue
            callers = []
            ranked = sorted(
                candidates(definition.name), key=lambda one: nearness(path, one)
            )
            rivals = [
                one
                for one in ranked
                if one != path
                and isinstance(read(one), str)
                and any(
                    found.name == definition.name
                    for found in definitions(one, read(one))
                )
            ]
            for candidate in ranked:
                content = read(candidate)
                if not isinstance(content, str):
                    continue
                if rivals and not refers_to(candidate, content, path, rivals):
                    continue
                spots = [
                    one
                    for one in calls(candidate, content)
                    if one.name == definition.name
                ]
                if not spots or not credited(definition.name, candidate, path):
                    continue
                inside = definitions(candidate, content)
                for spot in spots:
                    holder = enclosing(inside, spot.line)
                    if holder is None or (
                        candidate == path and holder.start == definition.start
                    ):
                        continue
                    callers.append(
                        Link(
                            candidate,
                            holder.name,
                            holder.start,
                            holder.end,
                            spot.line,
                            (
                                NAMED
                                if not rivals
                                else f"{NAMED}; {len(rivals) + 1} definitions share the name"
                            ),
                        )
                    )
                if len(callers) >= PER_DEFINITION:
                    break
            callers = list(
                {(one.path, one.start): one for one in reversed(callers)}.values()
            )[::-1]
            if callers:
                context.callers[key] = callers[:PER_DEFINITION]

            source = read(path)
            if not isinstance(source, str):
                continue
            called = []
            for spot in calls(path, source):
                if not definition.start <= spot.line <= definition.end:
                    continue
                if spot.name == definition.name or spot.name in {
                    one.name for one in called
                }:
                    continue
                for candidate in sorted(
                    candidates(spot.name), key=lambda one: nearness(path, one)
                ):
                    # A name matched outside this folder is as likely another
                    # thing of the same name as the one called here.
                    if PurePosixPath(candidate).parent != PurePosixPath(path).parent:
                        continue
                    content = read(candidate)
                    if not isinstance(content, str):
                        continue
                    target = next(
                        (
                            one
                            for one in definitions(candidate, content)
                            if one.name == spot.name
                        ),
                        None,
                    )
                    if target is not None:
                        called.append(
                            Link(
                                candidate,
                                target.name,
                                target.start,
                                target.end,
                                target.start,
                                NAMED,
                            )
                        )
                        break
                if len(called) >= PER_DEFINITION:
                    break
            if called:
                context.callees[key] = called


def call_context(
    diff: str,
    repository: str,
    read_content: Callable[[str], str | None],
    *,
    revision: str = "",
    reader=None,
    searcher=None,
) -> CallContext:
    context = CallContext()
    if read_content is None:
        return context
    for file in parse_diff(diff):
        if not file.file_path or file.is_binary_file:
            continue
        content = read_content(file.file_path)
        if not isinstance(content, str):
            continue
        found = changed_definitions(
            file.file_path,
            content,
            [(start, end) for _, _, start, end in file.hunk_ranges],
        )
        if found:
            context.changed[file.file_path] = found
    if not context.changed:
        return context
    indexed = Scope.from_mapping({"repository": repository})
    answered = (
        _graph(
            reader,
            (
                (
                    Scope.from_mapping(
                        {"repository": repository, "revision": revision}
                    ),
                    indexed,
                )
                if revision
                else (indexed,)
            ),
            context.changed,
            read_content,
            context,
        )
        if reader is not None and repository
        else set()
    )
    searched = (
        Scope.from_mapping({"repository": repository, "revision": revision})
        if revision
        else indexed
    )
    _named(searcher, searched, context.changed, read_content, context, answered)
    return context
