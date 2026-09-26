from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from src.config.paths import ensure_data_dir
from src.core.scope import Scope
from src.utils.moments import utc

REGISTRY_NAME = "repositories.json"


class RegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class RegisteredRepository:
    name: str
    path: str
    # When it was last read into the graph. Empty means never, which is what a
    # client needs to tell "nothing here yet" from "nothing has changed".
    indexed_at: str = ""

    @property
    def scope(self) -> Scope:
        return Scope.from_mapping({"repository": self.name})


def registry_path() -> Path:
    return ensure_data_dir() / REGISTRY_NAME


def repository_name(path: Path) -> str:
    remote = _git_remote(path)
    if remote:
        return remote
    return path.resolve().name


def add_repository(path: Path, *, name: str = "") -> RegisteredRepository:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError(f"{resolved} is not a directory")
    entry = RegisteredRepository(
        name=name or repository_name(resolved), path=str(resolved)
    )
    entries = list_repositories()
    # Registering the same folder under the same name again is not a reason to
    # forget it was read: the graph it filled is still there.
    for item in entries:
        if item.path == entry.path and item.name == entry.name:
            entry = replace(entry, indexed_at=item.indexed_at)
    kept = [item for item in entries if item.path != entry.path]
    kept.append(entry)
    _write(kept)
    return entry


def remove_repository(path: Path) -> bool:
    resolved = str(Path(path).expanduser().resolve())
    entries = list_repositories()
    kept = [item for item in entries if item.path != resolved]
    if len(kept) == len(entries):
        return False
    _write(kept)
    return True


def list_repositories() -> list[RegisteredRepository]:
    location = registry_path()
    if not location.exists():
        return []
    try:
        payload = json.loads(location.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RegistryError(
            f"{location} could not be read, so the registered repositories are "
            f"unknown. Move it aside to start again: {error}"
        ) from error
    if not isinstance(payload, list):
        raise RegistryError(f"{location} does not hold a list of repositories")
    entries = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name, path = item.get("name"), item.get("path")
        read = item.get("indexed_at")
        if isinstance(name, str) and isinstance(path, str) and name and path:
            entries.append(
                RegisteredRepository(
                    name=name,
                    path=path,
                    indexed_at=read if isinstance(read, str) else "",
                )
            )
    return sorted(entries, key=lambda item: item.path)


def find_repository(path: Path) -> RegisteredRepository | None:
    resolved = str(Path(path).expanduser().resolve())
    for entry in list_repositories():
        if entry.path == resolved:
            return entry
    return None


def mark_indexed(name: str, when: datetime | None = None) -> None:
    """Record that a repository has just been read.

    By name rather than by path, because two folders may be registered under
    one name, and one read of either fills the graph they share.
    """
    moment = utc(when or datetime.now(timezone.utc)) or ""
    entries = list_repositories()
    if not any(entry.name == name for entry in entries):
        return
    _write(
        [
            replace(entry, indexed_at=moment) if entry.name == name else entry
            for entry in entries
        ]
    )


def _write(entries: list[RegisteredRepository]) -> None:
    payload = [
        {"name": entry.name, "path": entry.path, "indexed_at": entry.indexed_at}
        for entry in sorted(entries, key=lambda item: item.path)
    ]
    target = registry_path()
    # Written beside the registry and moved over it, so a failure part way
    # through leaves the previous list intact rather than a half a file.
    handle, temporary = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _git_remote(path: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return _owner_and_name(completed.stdout.strip())


def _owner_and_name(url: str) -> str:
    if not url:
        return ""
    trimmed = url.rstrip("/")
    if trimmed.endswith(".git"):
        trimmed = trimmed[: -len(".git")]
    if trimmed.startswith("git@") and ":" in trimmed:
        trimmed = trimmed.split(":", 1)[1]
    segments = [segment for segment in trimmed.split("/") if segment]
    if len(segments) >= 2:
        return "/".join(segments[-2:])
    return segments[-1] if segments else ""
