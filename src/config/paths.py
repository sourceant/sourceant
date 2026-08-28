from __future__ import annotations

import os
from pathlib import Path

_APP_DIRECTORY = "sourceant"


def data_dir() -> Path:
    override = os.getenv("SOURCEANT_HOME")
    if override:
        return Path(override).expanduser()
    base = os.getenv("XDG_DATA_HOME")
    if base:
        return Path(base).expanduser() / _APP_DIRECTORY
    return Path.home() / ".local" / "share" / _APP_DIRECTORY


def ensure_data_dir() -> Path:
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def default_database_url() -> str:
    return f"sqlite:///{ensure_data_dir() / 'sourceant.db'}"
