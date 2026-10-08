from __future__ import annotations

import importlib
from contextlib import contextmanager
from threading import RLock

from alembic.script import ScriptDirectory
from alembic.script.base import Script

_migration_lock = RLock()


class CompiledScriptDirectory(ScriptDirectory):
    def _load_revisions(self):
        from src._compiled_resources import MIGRATIONS

        yield from super()._load_revisions()
        names = list(MIGRATIONS)
        from importlib.metadata import entry_points

        for entry in entry_points(group="sourceant.migrations"):
            root = entry.value.split(":")[0].split(".")[0]
            try:
                resources = importlib.import_module(root + "._compiled_resources")
            except ModuleNotFoundError as error:
                if error.name != root + "._compiled_resources":
                    raise
                continue
            names.extend(resources.MIGRATIONS)
        for name in dict.fromkeys(names):
            module = importlib.import_module(name)
            yield Script(module, module.revision, module.__file__)

    def run_env(self):
        from src.migrations.env import run_environment

        run_environment()


@contextmanager
def compiled_migrations():
    try:
        importlib.import_module("src._compiled_resources")
    except ModuleNotFoundError as error:
        if error.name != "src._compiled_resources":
            raise
        yield
        return
    with _migration_lock:
        original = ScriptDirectory.from_config
        descriptor = vars(ScriptDirectory)["from_config"]

        def configured(config):
            from pathlib import Path
            from src.utils.migration_paths import migrations_root

            directory = original(config)
            if Path(directory.dir).resolve() != migrations_root().resolve():
                return directory
            return CompiledScriptDirectory(
                directory.dir,
                version_locations=directory.version_locations,
                output_encoding=directory.output_encoding,
                messaging_opts=directory.messaging_opts,
            )

        ScriptDirectory.from_config = staticmethod(configured)
        try:
            yield
        finally:
            ScriptDirectory.from_config = descriptor
