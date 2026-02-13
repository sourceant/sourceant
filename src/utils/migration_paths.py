import glob as globmod
import os
from typing import List

from src.utils.logger import logger


def resolve_version_locations() -> List[str]:
    dirs = globmod.glob("src/plugins/builtin/*/migrations")
    dirs += globmod.glob("src/plugins/*/migrations")
    try:
        from importlib.metadata import entry_points
        import importlib.resources

        for ep in entry_points(group="sourceant.migrations"):
            mod = ep.load()
            path = str(importlib.resources.files(mod.__name__))
            if os.path.isdir(path):
                dirs.append(path)
    except Exception as e:
        logger.warning(f"Could not discover entrypoint migrations: {e}")
    return ["src/migrations/versions"] + dirs
