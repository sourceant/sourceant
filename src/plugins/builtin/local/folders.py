"""The folders this computer has been pointed at."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from src.cli.local_index import (
    RegistryError,
    add_repository,
    list_repositories,
    remove_repository,
    resolve_repository,
)

from .errors import Refused


class RegisteredFolders:
    """Registered folders, kept in one file beside the index.

    Never written into the folders themselves, so covering a repository does
    not modify it. The workspace argument is ignored; there is only one.
    """

    def all(self, workspace: str = "") -> Sequence[Any]:
        try:
            return list(list_repositories())
        except RegistryError as error:
            raise Refused(500, str(error)) from error

    def named(self, workspace: str, name: str, *, register: bool = False) -> Any:
        try:
            found = resolve_repository(name, register=register)
        except (RegistryError, ValueError, OSError) as error:
            raise Refused(400, str(error)) from error
        if found is not None:
            return found
        if not self.all(workspace):
            raise Refused(404, "No repositories are registered")
        raise Refused(404, f"{name} is not registered")

    def add(self, workspace: str, path: str, *, name: str = "") -> Any:
        try:
            return add_repository(Path(path), name=name)
        except RegistryError as error:
            raise Refused(400, str(error)) from error

    def remove(self, workspace: str, path: str) -> bool:
        try:
            return remove_repository(Path(path))
        except RegistryError as error:
            raise Refused(400, str(error)) from error
