"""Async wrapper around a configurable repo-packing tool.

The pack command is read from the REPO_PACK_COMMAND env var (default: "yek").
Any tool that accepts a directory path as its last argument and writes packed
output to stdout will work.
"""

import asyncio
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from src.utils.logger import logger

_ALLOWED_URL_SCHEMES = {"https", "http"}
_SAFE_PATH_RE = re.compile(r"^[a-zA-Z0-9_./ -]+$")
_BASE_DIR = Path("/app")
_SUBPROCESS_TIMEOUT = 300


def _validate_path(path: str) -> str:
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(_BASE_DIR):
        raise ValueError(f"Path escapes base directory: {path}")
    resolved_str = str(resolved)
    if not _SAFE_PATH_RE.match(resolved_str):
        raise ValueError(f"Invalid path: {path}")
    return resolved_str


def _validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES:
        raise ValueError(f"Invalid URL scheme: {parsed.scheme}")
    if not parsed.hostname:
        raise ValueError(f"Invalid URL: {url}")
    return url


class RepoPacker:
    def __init__(self):
        self.command = os.getenv("REPO_PACK_COMMAND", "yek").split()

    async def pack(self, path: str) -> str:
        """Run the configured pack command on a local path, capture stdout."""
        path = _validate_path(path)
        return await self._pack_path(path)

    async def pack_remote(self, url: str) -> str:
        """Clone a remote repo, pack it, clean up."""
        url = _validate_url(url)
        tmpdir = tempfile.mkdtemp()
        try:
            await self._clone(url, tmpdir)
            return await self._pack_path(tmpdir)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def _pack_path(self, path: str) -> str:
        """Run the pack command without path validation (for temp dirs)."""
        proc = await asyncio.create_subprocess_exec(
            *self.command,
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_SUBPROCESS_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("pack timed out")

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error(f"pack failed: {error_msg}")
            raise RuntimeError(f"pack failed: {error_msg}")

        return stdout.decode()

    async def _clone(self, url: str, dest: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "clone",
            "--depth",
            "1",
            url,
            dest,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_SUBPROCESS_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("git clone timed out")

        if proc.returncode != 0:
            raise RuntimeError(f"git clone failed: {stderr.decode().strip()}")
