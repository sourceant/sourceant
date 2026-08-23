"""Read the manifests a repository publishes, so its declared dependencies can
be proposed as topology relationships.

Only files a repository already keeps at its root are read, and only the ones
that declare package identity. Nothing is cloned and nothing is executed.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any, Iterable, Mapping, Sequence

import httpx

from src.core.topology.inference import PARSERS, RepositoryManifest, parse_manifest
from src.utils.logger import logger

_GITHUB_API = "https://api.github.com"

# The manifests worth asking for, in the order a repository is likely to lead with.
CANDIDATES: tuple[str, ...] = tuple(PARSERS.keys())


async def _fetch_one(
    client: httpx.AsyncClient,
    token: str,
    entity_id: str,
    repository: str,
    path: str,
) -> RepositoryManifest | None:
    try:
        response = await client.get(
            f"{_GITHUB_API}/repos/{repository}/contents/{path}",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
            },
        )
    except httpx.HTTPError as error:
        logger.warning(f"Could not read {repository}/{path}: {error}")
        return None

    # A repository simply not having a manifest is the common case, not a fault.
    if response.status_code != 200:
        return None

    payload = response.json()
    if payload.get("encoding") != "base64" or not payload.get("content"):
        return None
    try:
        content = base64.b64decode(payload["content"]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None

    return parse_manifest(entity_id, repository, path, content)


async def read_manifests(
    assets: Sequence[Mapping[str, Any]],
    token: str,
    candidates: Iterable[str] = CANDIDATES,
) -> tuple[RepositoryManifest, ...]:
    """
    Read every candidate manifest from every asset concurrently.

    ``assets`` carry an ``entity_id`` and a ``repository`` full name. An asset
    that is not a repository, or whose manifests cannot be read, contributes
    nothing rather than failing the whole reading.
    """
    wanted = [
        (str(asset["entity_id"]), str(asset["repository"]), path)
        for asset in assets
        if asset.get("entity_id") and asset.get("repository")
        for path in candidates
    ]
    if not wanted:
        return ()

    # Bounded so a system with many repositories cannot trip secondary limits.
    semaphore = asyncio.Semaphore(8)

    async with httpx.AsyncClient(timeout=15) as client:

        async def _guarded(entity_id: str, repository: str, path: str):
            async with semaphore:
                return await _fetch_one(client, token, entity_id, repository, path)

        results = await asyncio.gather(
            *[_guarded(*item) for item in wanted], return_exceptions=True
        )

    manifests: list[RepositoryManifest] = []
    for result in results:
        if isinstance(result, RepositoryManifest):
            manifests.append(result)
        elif isinstance(result, BaseException):
            logger.warning(f"Manifest read failed: {result}")
    return tuple(manifests)
