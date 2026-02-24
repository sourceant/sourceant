"""Tests for the RepoPacker utility."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.utils.repo_packer import RepoPacker, _validate_path, _validate_url


def _make_proc(returncode=0, stdout=b"packed output", stderr=b""):
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


class TestPack:
    @pytest.mark.asyncio
    @patch("src.utils.repo_packer.asyncio.create_subprocess_exec")
    async def test_pack_runs_configured_command(self, mock_exec, monkeypatch):
        monkeypatch.setenv("REPO_PACK_COMMAND", "custom-tool --flag")
        proc = _make_proc()
        mock_exec.return_value = proc

        packer = RepoPacker()
        await packer.pack("/app/repo")

        mock_exec.assert_awaited_once()
        args = mock_exec.call_args[0]
        assert args == ("custom-tool", "--flag", "/app/repo")

    @pytest.mark.asyncio
    @patch("src.utils.repo_packer.asyncio.create_subprocess_exec")
    async def test_pack_returns_stdout(self, mock_exec):
        proc = _make_proc(stdout=b"file contents here")
        mock_exec.return_value = proc

        packer = RepoPacker()
        result = await packer.pack("/app/repo")

        assert result == "file contents here"

    @pytest.mark.asyncio
    @patch("src.utils.repo_packer.asyncio.create_subprocess_exec")
    async def test_pack_raises_on_nonzero_exit(self, mock_exec):
        proc = _make_proc(returncode=1, stderr=b"something broke")
        mock_exec.return_value = proc

        packer = RepoPacker()
        with pytest.raises(RuntimeError, match="something broke"):
            await packer.pack("/app/repo")

    @pytest.mark.asyncio
    @patch("src.utils.repo_packer.asyncio.create_subprocess_exec")
    async def test_pack_raises_on_timeout(self, mock_exec):
        proc = AsyncMock()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        proc.kill = MagicMock()
        mock_exec.return_value = proc

        packer = RepoPacker()
        with pytest.raises(RuntimeError, match="timed out"):
            await packer.pack("/app/repo")
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_pack_validates_path(self):
        packer = RepoPacker()
        with pytest.raises(ValueError, match="escapes base directory"):
            await packer.pack("/etc/passwd")

    @pytest.mark.asyncio
    @patch("src.utils.repo_packer.asyncio.create_subprocess_exec")
    async def test_default_command_is_yek(self, mock_exec, monkeypatch):
        monkeypatch.delenv("REPO_PACK_COMMAND", raising=False)
        proc = _make_proc()
        mock_exec.return_value = proc

        packer = RepoPacker()
        await packer.pack("/app/repo")

        args = mock_exec.call_args[0]
        assert args == ("yek", "/app/repo")


class TestPackRemote:
    @pytest.mark.asyncio
    @patch("src.utils.repo_packer.shutil.rmtree")
    @patch("src.utils.repo_packer.tempfile.mkdtemp", return_value="/tmp/test123")
    @patch("src.utils.repo_packer.asyncio.create_subprocess_exec")
    async def test_clones_then_packs(self, mock_exec, mock_mkdtemp, mock_rmtree):
        clone_proc = _make_proc()
        pack_proc = _make_proc(stdout=b"packed remote")
        mock_exec.side_effect = [clone_proc, pack_proc]

        packer = RepoPacker()
        result = await packer.pack_remote("https://github.com/o/r.git")

        assert result == "packed remote"

        # First call: git clone
        clone_args = mock_exec.call_args_list[0][0]
        assert clone_args[:4] == ("git", "clone", "--depth", "1")
        assert clone_args[4] == "https://github.com/o/r.git"
        assert clone_args[5] == "/tmp/test123"

        # Second call: pack command on tmpdir
        pack_args = mock_exec.call_args_list[1][0]
        assert pack_args[-1] == "/tmp/test123"

        # Cleanup
        mock_rmtree.assert_called_once_with("/tmp/test123", ignore_errors=True)

    @pytest.mark.asyncio
    @patch("src.utils.repo_packer.shutil.rmtree")
    @patch("src.utils.repo_packer.tempfile.mkdtemp", return_value="/tmp/test123")
    @patch("src.utils.repo_packer.asyncio.create_subprocess_exec")
    async def test_raises_on_clone_failure(self, mock_exec, mock_mkdtemp, mock_rmtree):
        clone_proc = _make_proc(returncode=128, stderr=b"repo not found")
        mock_exec.return_value = clone_proc

        packer = RepoPacker()
        with pytest.raises(RuntimeError, match="git clone failed"):
            await packer.pack_remote("https://github.com/o/r.git")

        mock_rmtree.assert_called_once_with("/tmp/test123", ignore_errors=True)

    @pytest.mark.asyncio
    async def test_validates_url(self):
        packer = RepoPacker()
        with pytest.raises(ValueError, match="Invalid URL scheme"):
            await packer.pack_remote("ftp://evil.com/repo.git")

    @pytest.mark.asyncio
    @patch("src.utils.repo_packer.shutil.rmtree")
    @patch("src.utils.repo_packer.tempfile.mkdtemp", return_value="/tmp/test123")
    @patch("src.utils.repo_packer.asyncio.create_subprocess_exec")
    async def test_cleanup_on_pack_failure(self, mock_exec, mock_mkdtemp, mock_rmtree):
        clone_proc = _make_proc()
        pack_proc = _make_proc(returncode=1, stderr=b"pack error")
        mock_exec.side_effect = [clone_proc, pack_proc]

        packer = RepoPacker()
        with pytest.raises(RuntimeError, match="pack error"):
            await packer.pack_remote("https://github.com/o/r.git")

        mock_rmtree.assert_called_once_with("/tmp/test123", ignore_errors=True)


class TestValidation:
    def test_validate_path_rejects_outside_base(self):
        with pytest.raises(ValueError, match="escapes base directory"):
            _validate_path("/etc/passwd")

    def test_validate_path_accepts_app_path(self):
        assert _validate_path("/app/repo") == "/app/repo"

    def test_validate_url_rejects_invalid_scheme(self):
        with pytest.raises(ValueError, match="Invalid URL scheme"):
            _validate_url("ftp://example.com/repo")

    def test_validate_url_rejects_missing_host(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            _validate_url("https://")

    def test_validate_url_accepts_https(self):
        assert (
            _validate_url("https://github.com/o/r.git") == "https://github.com/o/r.git"
        )

    def test_validate_url_accepts_http(self):
        assert _validate_url("http://github.com/o/r.git") == "http://github.com/o/r.git"


class TestCustomCommand:
    @pytest.mark.asyncio
    @patch("src.utils.repo_packer.asyncio.create_subprocess_exec")
    async def test_custom_command_from_env(self, mock_exec, monkeypatch):
        monkeypatch.setenv("REPO_PACK_COMMAND", "repomix --stdout")
        proc = _make_proc(stdout=b"repomix output")
        mock_exec.return_value = proc

        packer = RepoPacker()
        assert packer.command == ["repomix", "--stdout"]

        result = await packer.pack("/app/test")
        args = mock_exec.call_args[0]
        assert args == ("repomix", "--stdout", "/app/test")
        assert result == "repomix output"
