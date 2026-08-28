from __future__ import annotations

from pathlib import Path

import click

from src.cli.local_index import (
    add_repository,
    find_repository,
    list_repositories,
    registry_path,
    remove_repository,
    repository_name,
)


def _store():
    from src.config.db import get_engine
    from src.core.code_index.sql import SQLCodeIndexRepository

    engine = get_engine()
    if engine is None:
        raise click.ClickException(
            "Indexing needs somewhere to keep the graph. Turn off stateless mode."
        )
    return SQLCodeIndexRepository(engine)


@click.group(name="repo")
def repo_group():
    """Choose which repositories the local graph covers."""


@repo_group.command(name="add")
@click.argument("path", type=click.Path(exists=True, file_okay=False))
@click.option("--name", default="", help="Name the graph stores it under.")
def repo_add_command(path, name):
    """Register a repository so it is indexed with the others."""
    entry = add_repository(Path(path), name=name)
    click.echo(f"{entry.name}  {entry.path}")


@repo_group.command(name="remove")
@click.argument("path", type=click.Path())
def repo_remove_command(path):
    """Stop covering a repository. Its graph is left alone."""
    if not remove_repository(Path(path)):
        raise click.ClickException(f"{path} was not registered")
    click.echo(f"Removed {path}")


@repo_group.command(name="list")
def repo_list_command():
    """Show every repository the local graph covers."""
    entries = list_repositories()
    if not entries:
        click.echo(f"Nothing registered yet. Registry lives at {registry_path()}")
        return
    for entry in entries:
        click.echo(f"{entry.name}  {entry.path}")


@click.command(name="index")
@click.argument("path", type=click.Path(exists=True), required=False)
@click.option(
    "--all", "index_all", is_flag=True, help="Index every registered repository."
)
@click.option(
    "--update",
    is_flag=True,
    help="Reparse only the files that changed since the last run.",
)
@click.option(
    "--scip",
    type=click.Path(exists=True, dir_okay=False),
    help="Load a SCIP index produced by another indexer instead of parsing.",
)
@click.option("--revision", default="", help="Revision to record with a SCIP import.")
def index_command(path, index_all, update, scip, revision):
    """Read a repository into the local code graph."""
    from src.core.code_index.indexer import RepositoryIndexer
    from src.core.code_index.scip import ScipJsonImporter

    store = _store()

    if scip:
        target = Path(path or ".").expanduser().resolve()
        entry = find_repository(target)
        name = entry.name if entry else repository_name(target)
        if not revision:
            raise click.ClickException("A SCIP import needs --revision")
        from src.core.scope import Scope

        scope = Scope.from_mapping({"repository": name, "revision": revision})
        result = ScipJsonImporter(store).import_json(scope, Path(scip).read_bytes())
        click.echo(f"{name}  {result.documents} documents  {result.symbols} symbols")
        return

    if index_all:
        targets = list_repositories()
        if not targets:
            raise click.ClickException(
                "No repositories registered. Use: sourceant repo add ."
            )
    else:
        target = Path(path or ".").expanduser().resolve()
        entry = find_repository(target)
        targets = [entry] if entry else [add_repository(target)]

    indexer = RepositoryIndexer(store)
    for entry in targets:
        result = indexer.index(
            entry.scope,
            Path(entry.path),
            update=update,
            excluded_paths=_excluded_paths(entry.name),
        )
        click.echo(
            f"{entry.name}  indexed {result.indexed}  unchanged {result.unchanged}  "
            f"removed {result.removed}  skipped {result.skipped}"
        )


def _excluded_paths(repository: str) -> frozenset[str]:
    from src.core.settings import value_of

    configured = value_of("initialization.excluded_paths", repository=repository)
    if isinstance(configured, (list, tuple)):
        return frozenset(item for item in configured if isinstance(item, str) and item)
    return frozenset()


@click.command(name="serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True, type=int)
def serve_command(host, port):
    """Run the HTTP server, including the MCP endpoint."""
    import uvicorn

    uvicorn.run("src.api.main:app", host=host, port=port)
