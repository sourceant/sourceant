from __future__ import annotations

import os
import secrets
from pathlib import Path

_APP_DIRECTORY = "sourceant"
_SECRET_NAME = "jwt_secret"


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


def local_jwt_secret() -> str:
    """The secret this installation signs its own tokens with, made once and kept.

    Written with O_EXCL so two processes starting together cannot each make one
    and invalidate the other's tokens. It only ever verifies tokens this
    installation issued: anything verifying another system's tokens is given
    that system's secret and never reaches here.
    """
    location = ensure_data_dir() / _SECRET_NAME
    candidate = secrets.token_urlsafe(48)
    try:
        descriptor = os.open(location, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return location.read_text(encoding="utf-8").strip()
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(candidate)
    return candidate
