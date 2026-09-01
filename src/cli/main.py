"""The sourceant command.

Kept importable so a wheel can carry an entry point to it. The ./sourceant at
the repository root runs this same group.
"""

import click
import os
import sys
import subprocess
from src.config.settings import DATABASE_URL, STATELESS_MODE
from src.utils.logger import logger
from src.utils.migration_paths import migrations_root, resolve_version_locations
from alembic.config import Config, CommandLine

from src.cli.index_commands import (
    index_command,
    repo_group,
    requirements_group,
    serve_command,
)


@click.group()
def cli():
    pass


@click.command(
    name="db",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
@click.pass_context
def db_command(ctx):
    if STATELESS_MODE:
        print("Application is in STATELESS_MODE. Skipping database command.")
        logger.warning("Application is in STATELESS_MODE. Skipping database command.")
        sys.exit(0)
    if not DATABASE_URL:
        print("DATABASE_URL not set, skipping database command.")
        logger.warning("DATABASE_URL not set, skipping database command.")
        sys.exit(0)

    sys.argv = ["alembic"] + ctx.args
    cmd = CommandLine()
    options = cmd.parser.parse_args(ctx.args)
    cfg = Config(cmd_opts=options)
    # Installed as a package there is no alembic.ini and no repository to be
    # relative to, so the migrations are found beside the code that ships them.
    cfg.set_main_option("script_location", str(migrations_root()))
    cfg.set_main_option("version_locations", " ".join(resolve_version_locations()))
    fn, positional, kwarg = options.cmd
    fn(
        cfg,
        *[getattr(options, k) for k in positional],
        **{k: getattr(options, k) for k in kwarg},
    )
    # Leave immediately rather than waiting on whatever the migration environment
    # started. A plugin that keeps a thread alive would otherwise hold this command
    # open forever, and everything that waits for migrations to finish with it.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


@click.group(name="code")
def code_group():
    pass


@click.command(name="lint")
def lint_command():
    try:
        subprocess.run(["black", "--check", "."], check=True)
    except subprocess.CalledProcessError as e:
        print(
            "\nLinting failed! Run 'sourceant code lint:fix' to automatically fix linting issues."
        )
        sys.exit(e.returncode)


@click.command(name="lint:fix")
def lint_fix_command():
    try:
        subprocess.run(["black", "."], check=True)
        print("Linting issues fixed successfully.")
    except subprocess.CalledProcessError as e:
        print("Error while fixing linting issues:")
        sys.exit(e.returncode)


cli.add_command(db_command)
code_group.add_command(lint_command)
code_group.add_command(lint_fix_command)
cli.add_command(code_group)
cli.add_command(repo_group)
cli.add_command(index_command)
cli.add_command(requirements_group)
cli.add_command(serve_command)

if __name__ == "__main__":
    cli()
