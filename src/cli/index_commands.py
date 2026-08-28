from __future__ import annotations

from pathlib import Path

import click

from src.cli.local_index import (
    RegistryError,
    add_repository,
    find_repository,
    list_repositories,
    registry_path,
    remove_repository,
    repository_name,
)


def _engine():
    from src.config.db import get_engine

    engine = get_engine()
    if engine is None:
        raise click.ClickException(
            "This needs somewhere to keep what it reads. Turn off stateless mode."
        )
    return engine


def _store():
    from src.core.code_index.sql import SQLCodeIndexRepository

    return SQLCodeIndexRepository(_engine())


class _Registry(click.Group):
    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except RegistryError as error:
            raise click.ClickException(str(error)) from error


class _RegistryCommand(click.Command):
    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except RegistryError as error:
            raise click.ClickException(str(error)) from error


@click.group(name="repo", cls=_Registry)
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


@click.command(name="index", cls=_RegistryCommand)
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


@click.group(name="requirements")
def requirements_group():
    """Bring in what the software is meant to do."""


@requirements_group.command(name="import")
@click.argument("repository")
@click.option(
    "--label",
    "labels",
    multiple=True,
    help="Only issues carrying one of these labels. Repeatable.",
)
@click.option(
    "--dry-run", is_flag=True, help="Show what would be stored, store nothing."
)
def requirements_import_command(repository, labels, dry_run):
    """Read a GitHub repository's issues in as requirements."""
    from src.core.knowledge import SQLKnowledgeRepository
    from src.core.requirements import (
        DEFAULT_LABELS,
        GitHubIssueRequirements,
        KnowledgeBackedRequirements,
        SQLRequirementsRepository,
    )
    from src.core.scope import Scope
    from src.integrations.github.github import GitHub

    if "/" not in repository:
        raise click.ClickException("Name the repository as owner/name")
    owner, name = repository.split("/", 1)

    engine = _engine()
    github = GitHub()
    source = GitHubIssueRequirements(
        lambda _repository, wanted: github.list_issues(
            owner, name, labels=tuple(wanted), state="all"
        ),
        labels=labels or DEFAULT_LABELS,
    )
    scope = Scope.from_mapping({"repository": repository})
    found = source.sync(scope)
    if not found:
        click.echo(f"{repository}  nothing to import")
        return

    if dry_run:
        for item in found:
            click.echo(f"{item.id}  ({item.status})  {item.summary}")
        click.echo(f"{repository}  {len(found)} would be imported")
        return

    store = KnowledgeBackedRequirements(
        SQLRequirementsRepository(engine), SQLKnowledgeRepository(engine)
    )
    for item in found:
        store.put(scope, item)
    click.echo(f"{repository}  imported {len(found)}")


@click.command(name="serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True, type=int)
def serve_command(host, port):
    """Run the HTTP server, including the MCP endpoint."""
    import uvicorn

    from src.config import settings

    # Being the local command is what opens the routes that change what this
    # machine indexes. Set on the module rather than read from the environment
    # here, because settings was imported before this ran and uvicorn does not
    # re-execute an imported module. Deployments run uvicorn directly and leave
    # it off; an operator who means to sets SOURCEANT_LOCAL.
    settings.LOCAL_MODE = True
    uvicorn.run("src.api.main:app", host=host, port=port)
