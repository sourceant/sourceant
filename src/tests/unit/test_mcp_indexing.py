import pytest
from click.testing import CliRunner
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy import create_engine

from src.cli.index_commands import repo_group
from src.core.code_index import SQLCodeIndexRepository
from src.core.context import DefaultContextProvider
from src.core.mcp import create_mcp_server, personal_surface
from src.core.services import ServiceRegistry
from src.mcp_server.indexing import add_index_tools
from src.plugins.builtin.local.folders import RegisteredFolders


@pytest.mark.asyncio
async def test_indexing_registered_files_through_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path / "state"))
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    source = checkout / "billing.py"
    source.write_text("def charge(amount):\n    return amount\n")
    registered = CliRunner().invoke(
        repo_group, ["add", str(checkout), "--name", "local/billing"]
    )
    assert registered.exit_code == 0, registered.output
    code = SQLCodeIndexRepository(
        create_engine(f"sqlite:///{tmp_path / 'index.db'}"), create_schema=True
    )
    server = create_mcp_server(
        DefaultContextProvider(code=code),
        code=code,
        services=ServiceRegistry(),
        surface=personal_surface(),
    )
    add_index_tools(server, RegisteredFolders(), lambda: code)
    async with create_connected_server_and_client_session(server) as session:

        async def call(name, repository="local/billing"):
            result = await session.call_tool(name, {"repository": repository})
            assert not result.isError, result
            return result.structuredContent

        assert (await call("get_index_status"))["has_index"] is False
        assert (await call("index_repository"))["indexed"] == 1
        assert (await call("get_index_status"))["indexed_files"] == 1
        assert (await call("index_repository"))["unchanged"] == 1
        source.write_text("def refund(amount):\n    return -amount\n")
        assert (await call("index_repository"))["indexed"] == 1
        symbols = await session.call_tool(
            "search_code",
            {
                "scope": {"repository": "local/billing"},
                "properties": {"name": "refund"},
            },
        )
        assert not symbols.isError and symbols.structuredContent["total"] == 1
        source.unlink()
        assert (await call("index_repository"))["removed"] == 1
        refused = await session.call_tool(
            "index_repository", {"repository": "local/unregistered"}
        )
        assert refused.isError
