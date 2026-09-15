"""Read one file of a repository, for a reading that has to cite lines.

The manifest reading asks for a fixed handful of paths and can ask for them all
at once. A reading picks its next path from what the last one said, so it reads
one at a time and the same path more than once, which is what the cache is for.
"""

from __future__ import annotations

import base64
from typing import Callable, Optional

import httpx

from src.utils.logger import logger

_GITHUB_API = "https://api.github.com"

#: Past this a file is not read rather than read in part. A citation into a
#: truncated file points at a line the checker would not find.
MAX_BYTES = 400_000


def contents_reader(
    repository: str, token: str, revision: str = ""
) -> Callable[[str], Optional[str]]:
    """A reader for one repository, holding what it has already read."""
    seen: dict[str, Optional[str]] = {}
    client = httpx.Client(
        timeout=20,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        },
    )

    def read(path: str) -> Optional[str]:
        if not path or path.startswith("/") or ".." in path.split("/"):
            return None
        if path not in seen:
            seen[path] = _fetch(client, repository, path, revision)
        return seen[path]

    return read


def _fetch(
    client: httpx.Client, repository: str, path: str, revision: str
) -> Optional[str]:
    try:
        response = client.get(
            f"{_GITHUB_API}/repos/{repository}/contents/{path}",
            params={"ref": revision} if revision else None,
        )
    except httpx.HTTPError as error:
        logger.warning(f"Could not read {repository}/{path}: {error}")
        return None
    if response.status_code != 200:
        return None
    payload = response.json()
    if payload.get("encoding") != "base64" or not payload.get("content"):
        return None
    if int(payload.get("size") or 0) > MAX_BYTES:
        return None
    try:
        return base64.b64decode(payload["content"]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def head_revision(repository: str, token: str) -> str:
    """The commit a repository's default branch is on, or "" where unknown.

    Unknown is not an error. It means a reading of this repository cannot be
    reused, which is the same position as never having done one.
    """
    try:
        response = httpx.get(
            f"{_GITHUB_API}/repos/{repository}/commits",
            params={"per_page": 1},
            timeout=15,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
            },
        )
    except httpx.HTTPError as error:
        logger.warning(f"Could not read where {repository} is: {error}")
        return ""
    if response.status_code != 200:
        return ""
    try:
        commits = response.json()
        return str(commits[0]["sha"]) if commits else ""
    except (ValueError, KeyError, IndexError, TypeError):
        return ""
