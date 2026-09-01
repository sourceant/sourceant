import glob as globmod
import os
from pathlib import Path
from typing import List

from src.utils.logger import logger


def migrations_root() -> Path:
    """Where the migrations live, whether this is a checkout or a wheel."""
    return Path(__file__).resolve().parent.parent / "migrations"


def resolve_version_locations() -> List[str]:
    package = migrations_root().parent
    dirs = globmod.glob(str(package / "plugins" / "builtin" / "*" / "migrations"))
    dirs += globmod.glob(str(package / "plugins" / "*" / "migrations"))
    try:
        from importlib.metadata import entry_points
        import importlib.util

        for ep in entry_points(group="sourceant.migrations"):
            # Locate the module rather than loading it. Importing a plugin to read
            # its migrations directory also starts it, and a plugin that leaves a
            # thread running keeps the migration process alive after it is done.
            spec = importlib.util.find_spec(ep.value.split(":")[0])
            locations = list(getattr(spec, "submodule_search_locations", None) or [])
            path = locations[0] if locations else os.path.dirname(spec.origin or "")
            if path and os.path.isdir(path):
                dirs.append(path)
    except Exception as e:
        logger.warning(f"Could not discover entrypoint migrations: {e}")
    return [str(migrations_root() / "versions")] + dirs
