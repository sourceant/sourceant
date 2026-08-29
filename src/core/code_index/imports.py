"""Finding the imports a language's own reader does not report.

The parser answers with imports for some languages and with nothing for others.
A repository in one of the others draws as one file per island however good the
resolver is, because there is nothing to resolve: the connections were never
read.

There is no way around that being per-language work. Every tool that does this
well is in the same position. An indexer that runs the real compiler knows
exactly what a name binds to and is written per language. Stack graphs replace
the compiler with binding rules and are written per language. Working out an
import from its text alone, as this repository also does, still needs to know
what an import looks like, which is a question only the grammar answers.

So this is a query per language, run only where the reader returned nothing.
Adding a language is a line in the table below, which is the smallest that work
gets.
"""

from __future__ import annotations

from src.core.language_pack import get_parser
from src.utils.logger import logger

# The node holding the module a file depends on, per grammar. Written against
# the grammar rather than against the text, so a name spanning lines, aliased,
# or grouped is still one match.
QUERIES: dict[str, str] = {
    # use App\Models\User; and use Illuminate\Http\Request as Req;
    "php": "(namespace_use_clause (qualified_name) @module)",
    # require 'json' and require_relative 'charge'
    "ruby": """
        (call
          method: (identifier) @method
          arguments: (argument_list (string (string_content) @module))
          (#match? @method "^(require|require_relative|load|autoload)$"))
    """,
    # using System; and using App.Models;
    "csharp": "(using_directive (qualified_name) @module) (using_directive (identifier) @module)",
}

# What a grammar calls the language a block of another one is written in.
EMBEDS: dict[str, tuple[str, ...]] = {
    "vue": ("typescript", "javascript"),
    "svelte": ("typescript", "javascript"),
}


def _matches(language: str, source: str) -> list[str]:
    query = QUERIES.get(language)
    if query is None:
        return []
    try:
        from tree_sitter import Query, QueryCursor
        from tree_sitter_language_pack import get_language

        tree = get_parser(language).parse(source.encode("utf-8"))
        captures = QueryCursor(Query(get_language(language), query)).captures(
            tree.root_node
        )
    except Exception as error:  # noqa: BLE001 - any grammar or query problem
        logger.debug(f"No imports read for {language}: {error}")
        return []

    # The API answers with a mapping of capture name to nodes, or with a list of
    # pairs, depending on its version. Both say the same thing.
    if isinstance(captures, dict):
        nodes = [node for node in captures.get("module", [])]
    else:
        nodes = [node for node, name in captures if name == "module"]

    found: list[str] = []
    for node in nodes:
        text = node.text.decode("utf-8", "replace").strip() if node.text else ""
        if text:
            found.append(text)
    return found


def _scripts(source: str) -> str:
    """The code out of a file that is mostly not code.

    A single-file component is a template with a script in it. The script is an
    ordinary module in an ordinary language, and its imports are the file's.
    """
    out: list[str] = []
    lowered = source.lower()
    at = lowered.find("<script")
    while at != -1:
        opened = source.find(">", at)
        closed = lowered.find("</script", opened)
        if opened == -1 or closed == -1:
            break
        out.append(source[opened + 1 : closed])
        at = lowered.find("<script", closed)
    return "\n".join(out)


def read(language: str, source: str) -> list[str]:
    """Every module this file depends on, where the reader reported none."""
    if language in EMBEDS:
        from src.core.language_pack import Error, ProcessConfig, process

        script = _scripts(source)
        if not script.strip():
            return []
        for embedded in EMBEDS[language]:
            try:
                result = process(script, ProcessConfig(language=embedded))
            except (Error, RuntimeError):
                continue
            if result.imports:
                return [item.source for item in result.imports]
        return []
    return _matches(language, source)
