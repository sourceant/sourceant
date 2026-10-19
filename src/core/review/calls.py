"""Who calls what changed, and what it calls, in any language the parser reads.

A call is found from the grammar's own tree rather than per language: grammars
name the node a call, an invocation or an application, a shell names it a
command, and the rest at least mark the argument list, in which case the name
is the identifier written just before it. Single-file components are read
through their script blocks, at their real line numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Iterable, Sequence

from src.core.language_pack import Error, ProcessConfig, detect_language, get_parser
from src.core.language_pack import process

CALL = re.compile(r"(?:^|_)(?:call|invocation|application)(?:_|$)")
ARGUMENTS = frozenset({"arguments", "argument_list", "argument_part"})
FIELDS = ("function", "name", "method", "callee", "constructor")
NAME = re.compile(r"([A-Za-z_$][\w$]*)\W*$")
SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.S | re.I)


@dataclass(frozen=True)
class Definition:
    path: str
    name: str
    kind: str
    start: int
    end: int
    signature: str


@dataclass(frozen=True)
class Call:
    path: str
    name: str
    line: int


def _units(path: str, content: str) -> list[tuple[str, str, int]]:
    """The code in a file, as (language, source, lines before it)."""
    language = detect_language(path)
    if language in {"vue", "svelte"}:
        units = []
        for match in SCRIPT.finditer(content):
            attributes, source = match.group(1), match.group(2)
            embedded = (
                "typescript"
                if re.search(r"""lang\s*=\s*["']?ts""", attributes)
                else "javascript"
            )
            units.append((embedded, source, content.count("\n", 0, match.start(2))))
        return units
    return [(language, content, 0)] if language else []


DEFINITION = re.compile(
    r"^(?:function|method)_(?:definition|declaration|signature|item)$"
)
NAME_FIELDS = ("name", "declarator", "pattern")


def _first_name(node) -> str | None:
    """The first plain name in a node, passing over types and namespaces."""
    stack = [node]
    while stack:
        current = stack.pop(0)
        if current.child_count == 0:
            kind = current.type
            if (
                ("identifier" in kind or kind in {"value_name", "word"})
                and "type" not in kind
                and "namespace" not in kind
            ):
                return current.text.decode("utf-8", "replace")
            continue
        if current.type in {"parameter_list", "parameters", "formal_parameter_list"}:
            continue
        stack = list(current.children) + stack
    return None


def _parsed_definitions(path, language, source, offset) -> list[Definition]:
    """Definitions read from the tree itself, for grammars the symbol reader
    reports nothing for."""
    try:
        tree = get_parser(language).parse(source.encode("utf-8"))
    except Exception:  # noqa: BLE001 - a grammar that cannot read this file
        return []
    lines = source.splitlines()
    found = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        kind = node.type
        takes_parameters = any("parameter" in one.type for one in node.children)
        if not (DEFINITION.match(kind) or (kind == "let_binding" and takes_parameters)):
            continue
        holder = next(
            (
                child
                for field in NAME_FIELDS
                if (child := node.child_by_field_name(field)) is not None
                and child.is_named
            ),
            None,
        )
        parent = node.parent
        if (
            holder is None
            and parent is not None
            and parent.type.endswith(("operator", "assignment"))
        ):
            holder = parent.child_by_field_name("lhs") or parent.named_children[0]
        if holder is None:
            holder = next(
                (one for one in node.named_children if "body" not in one.type), None
            )
        name = _first_name(holder) if holder is not None else None
        if not name:
            continue
        end = node.end_point[0]
        anchor = (
            parent if parent is not None and parent.type.endswith("signature") else node
        )
        following = anchor.next_named_sibling
        if (
            kind.endswith("signature")
            and following is not None
            and "body" in following.type
        ):
            end = following.end_point[0]
        first = node.start_point[0] + 1
        found.append(
            Definition(
                path,
                name,
                "Function",
                first + offset,
                end + 1 + offset,
                (lines[first - 1].strip() if first <= len(lines) else "")[:160],
            )
        )
    return found


def definitions(path: str, content: str) -> list[Definition]:
    found = []
    for language, source, offset in _units(path, content):
        found.extend(_parsed_definitions(path, language, source, offset))
        try:
            structure = process(source, ProcessConfig(language=language)).structure
        except (Error, RuntimeError):
            continue
        lines = source.splitlines()

        def walk(items):
            for item in items:
                if item.name:
                    first = item.span.start_line + 1
                    found.append(
                        Definition(
                            path,
                            item.name,
                            str(item.kind),
                            first + offset,
                            item.span.end_line + 1 + offset,
                            (
                                item.signature
                                or (
                                    lines[first - 1].strip()
                                    if first <= len(lines)
                                    else ""
                                )
                            )[:160],
                        )
                    )
                walk(item.children)

        walk(structure)
    unique: dict[tuple[str, int], Definition] = {}
    for one in sorted(found, key=lambda one: (one.start, one.name, -one.end)):
        if one.name != "function":
            unique.setdefault((one.name, one.start), one)
    return list(unique.values())


def _leaf_name(node) -> str | None:
    """The last name written in a node, which is what a call calls."""
    named = None
    stack = [node]
    while stack:
        current = stack.pop()
        if current.child_count == 0:
            kind = current.type
            if "identifier" in kind or kind in {
                "word",
                "name",
                "constant",
                "value_name",
            }:
                text = current.text.decode("utf-8", "replace")
                if named is None or current.start_byte > named[0]:
                    named = (current.start_byte, text)
        stack.extend(current.children)
    return named[1] if named else None


def calls(path: str, content: str) -> list[Call]:
    found: dict[tuple[str, int], Call] = {}
    for language, source, offset in _units(path, content):
        try:
            tree = get_parser(language).parse(source.encode("utf-8"))
        except Exception:  # noqa: BLE001 - a grammar that cannot read this file
            continue
        encoded = source.encode("utf-8")
        stack = [(tree.root_node, False)]
        while stack:
            node, declaring = stack.pop()
            # A signature or declarator names what is being defined; a call
            # written there is the definition itself.
            declaring = (
                declaring
                or "signature" in node.type
                or node.type == "function_declarator"
            )
            stack.extend((child, declaring) for child in node.children)
            if not node.is_named or declaring:
                continue
            name = None
            if CALL.search(node.type) or node.type == "command":
                callee = next(
                    (
                        child
                        for field in FIELDS
                        if (child := node.child_by_field_name(field)) is not None
                    ),
                    node.named_children[0] if node.named_children else None,
                )
                name = _leaf_name(callee) if callee is not None else None
            elif node.type in ARGUMENTS:
                written = encoded[: node.start_byte].decode("utf-8", "replace")
                match = NAME.search(written[-200:])
                name = match.group(1) if match else None
            if name and len(name) > 1:
                line = node.start_point[0] + 1 + offset
                found.setdefault((name, line), Call(path, name, line))
    return sorted(found.values(), key=lambda one: (one.line, one.name))


def enclosing(found: Sequence[Definition], line: int) -> Definition | None:
    """The innermost definition a line is written in."""
    inside = [one for one in found if one.start <= line <= one.end]
    return min(inside, key=lambda one: one.end - one.start) if inside else None


def changed_definitions(
    path: str, content: str, ranges: Iterable[tuple[int, int]]
) -> list[Definition]:
    """The innermost definitions a change's lines fall in."""
    spans = list(ranges)
    found = definitions(path, content)
    touched = [
        one
        for one in found
        if any(one.start <= end and start <= one.end for start, end in spans)
    ]
    return [
        one
        for one in touched
        if not any(
            other is not one and one.start <= other.start and other.end <= one.end
            for other in touched
        )
    ]


def excerpt(content: str, start: int, end: int, around: int, limit: int = 40) -> str:
    """Lines of a definition, kept near one line when it is long."""
    lines = content.splitlines()
    if end - start + 1 > limit:
        start = max(start, around - limit // 2)
        end = min(end, start + limit - 1)
    return "\n".join(
        f"{number}: {lines[number - 1]}"
        for number in range(max(1, start), min(len(lines), end) + 1)
    )


def resolve(
    names: Iterable[str],
    candidates: Callable[[str], Iterable[str]],
    read_content: Callable[[str], str | None],
) -> dict[str, list[Definition]]:
    """Where each name is defined, among the files that mention it."""
    found: dict[str, list[Definition]] = {}
    for name in names:
        for path in candidates(name):
            content = read_content(path)
            if not isinstance(content, str):
                continue
            found.setdefault(name, []).extend(
                one for one in definitions(path, content) if one.name == name
            )
    return found
