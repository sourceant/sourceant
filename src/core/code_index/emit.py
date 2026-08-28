from __future__ import annotations

import re

from src.core.language_pack import Error, ProcessConfig, detect_language, process
from src.core.scope import Scope

from .interfaces import CodeIndexWriter
from .models import CodeEdge, CodeNode

DEFAULT_FILE_CHARACTER_LIMIT = 500_000

QUOTED = re.compile(r"""["'`]([^"'`\n]+)["'`]|<([^>\n]+)>""")
LEADING = re.compile(r"^(?:from|import|use|require|include|#include)\s+", re.IGNORECASE)


def import_source(raw: object) -> str | None:
    """The module an import names, out of the statement it was written as.

    The parser answers with the whole statement rather than the module in it:
    `from app.charge import charge`, `"example.com/who/thing"`, or a grouped
    block spanning lines. Stored like that an import matches nothing, so a
    repository draws as one island per file.

    A quoted or bracketed path is the module wherever it appears, which covers
    every language that writes one. Otherwise it is the first word after the
    keyword. A block is not one module at all and is left out, rather than kept
    as a node standing for nothing.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip().rstrip(";").strip()
    if not text or "\n" in text:
        return None

    quoted = QUOTED.search(text)
    if quoted:
        return (quoted.group(1) or quoted.group(2)).strip() or None

    stripped = LEADING.sub("", text, count=1)
    words = stripped.split()
    word = words[0] if words else ""
    return word.rstrip(",").strip("\"'`<>").strip() or None


def emit_file_graph(
    writer: CodeIndexWriter,
    scope: Scope,
    path: str,
    content: str,
    *,
    character_limit: int = DEFAULT_FILE_CHARACTER_LIMIT,
    digest: str = "",
) -> bool:
    language = detect_language(path)
    if language is None:
        return False
    if not isinstance(content, str) or len(content) > character_limit:
        return False
    try:
        result = process(content, ProcessConfig(language=language))
    except (Error, RuntimeError):
        return False

    file_id = f"file:{path}"
    writer.put_node(
        scope,
        CodeNode(
            file_id,
            frozenset({"File"}),
            _file_properties(path, language, digest),
        ),
    )
    for position, item in enumerate(result.imports):
        named = import_source(item.source)
        if named is None:
            continue
        import_id = f"import:{path}:{position}"
        writer.put_node(
            scope,
            CodeNode(
                import_id,
                frozenset({"Import"}),
                {"file_path": path, "name": named},
            ),
        )
        writer.put_edge(
            scope,
            CodeEdge(
                f"imports:{file_id}:{import_id}",
                file_id,
                import_id,
                "IMPORTS",
            ),
        )
    _emit_structure(writer, scope, path, file_id, result.structure)
    return True


def _file_properties(path: str, language: str, digest: str) -> dict:
    properties = {
        "file_path": path,
        "kind": language,
        "name": path.rsplit("/", 1)[-1],
    }
    if digest:
        properties["digest"] = digest
    return properties


def _emit_structure(writer, scope, path, parent_id, items) -> None:
    for position, item in enumerate(items):
        if not item.name:
            _emit_structure(writer, scope, path, parent_id, item.children)
            continue
        symbol_id = f"symbol:{path}:{item.name}:{item.span.start_line}:{position}"
        writer.put_node(
            scope,
            CodeNode(
                symbol_id,
                frozenset({str(item.kind)}),
                {
                    "file_path": path,
                    "kind": str(item.kind),
                    "name": item.name,
                    "signature": item.signature,
                    "start_line": item.span.start_line + 1,
                    "end_line": item.span.end_line + 1,
                },
            ),
        )
        writer.put_edge(
            scope,
            CodeEdge(
                f"defines:{parent_id}:{symbol_id}",
                parent_id,
                symbol_id,
                "DEFINES",
            ),
        )
        _emit_structure(writer, scope, path, symbol_id, item.children)
