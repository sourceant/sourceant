"""Bringing this machine's index to the schema the code expects.

A deployment migrates before it serves, as its own step. The local command is
the whole product on one machine, so nothing stands between the install and the
first request: it has to migrate itself.

That leaves one shape to recover from. A local index built by a version that
served without migrating carries the few tables the runtime creates for itself
and no migration history, which no migration can be applied to. The index is
read back out of the repositories it was built from, so it is rebuilt rather
than repaired.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import Engine, inspect

from src.utils.logger import logger
from src.utils.migration_paths import migrations_root, resolve_version_locations


def ensure(engine: Optional[Engine], database_url: str) -> None:
    """Migrate this machine's index, rebuilding one that cannot be migrated."""
    if engine is None:
        return
    unmigrated = _has_tables_without_history(engine)
    if unmigrated:
        kept = _set_aside(database_url)
        if kept is None:
            logger.warning(
                "This index has tables but no migration history, and it is not a "
                "file that can be set aside. Migrating it will fail."
            )
        else:
            logger.warning(f"Rebuilding the index. The old one is at {kept}.")
            engine.dispose()
    _upgrade(database_url)


def _has_tables_without_history(engine: Engine) -> bool:
    names = set(inspect(engine).get_table_names())
    return bool(names) and "alembic_version" not in names


def _set_aside(database_url: str) -> Optional[Path]:
    path = _file_of(database_url)
    if path is None or not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    kept = path.with_name(f"{path.name}.{stamp}.unmigrated")
    shutil.move(str(path), str(kept))
    return kept


def _file_of(database_url: str) -> Optional[Path]:
    if not database_url.startswith("sqlite"):
        return None
    _, _, rest = database_url.partition("///")
    return Path("/" + rest.lstrip("/")) if rest else None


def _upgrade(database_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(migrations_root()))
    config.set_main_option("version_locations", " ".join(resolve_version_locations()))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "heads")
