"""Async wrapper around the repomix CLI."""

import asyncio
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from src.utils.logger import logger

IGNORE_PATTERNS = (
    "*.lock,package-lock.json,yarn.lock,composer.lock,"
    "*.min.js,*.min.css,*.map,"
    "vendor/**,node_modules/**,"
    "*.svg,*.png,*.jpg,*.gif,*.ico,"
    "storage/**,bootstrap/cache/**"
)

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


class Repomix:
    async def pack(self, path: str, style: str = "xml", compress: bool = True) -> str:
        """Pack a local directory into LLM-friendly format."""
        path = _validate_path(path)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            output_file = tmp.name

        cmd = [
            "repomix",
            path,
            "--style",
            style,
            "--output",
            output_file,
            "--ignore",
            IGNORE_PATTERNS,
        ]
        if compress:
            cmd.append("--compress")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_SUBPROCESS_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("repomix pack timed out")

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error(f"repomix failed: {error_msg}")
            raise RuntimeError(f"repomix pack failed: {error_msg}")

        try:
            content = Path(output_file).read_text()
        finally:
            Path(output_file).unlink(missing_ok=True)
        return content

    async def pack_remote(
        self, url: str, style: str = "xml", compress: bool = True
    ) -> str:
        """Pack a remote repo (repomix handles clone/cleanup internally)."""
        url = _validate_url(url)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            output_file = tmp.name

        cmd = [
            "repomix",
            "--remote",
            url,
            "--style",
            style,
            "--output",
            output_file,
            "--ignore",
            IGNORE_PATTERNS,
        ]
        if compress:
            cmd.append("--compress")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_SUBPROCESS_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("repomix pack_remote timed out")

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error(f"repomix remote failed: {error_msg}")
            raise RuntimeError(f"repomix pack_remote failed: {error_msg}")

        try:
            content = Path(output_file).read_text()
        finally:
            Path(output_file).unlink(missing_ok=True)
        return content
