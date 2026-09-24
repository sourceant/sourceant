import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from src.core.context import DefaultContextProvider
from src.core.mcp import create_mcp_server, personal_surface
from src.core.services import ServiceRegistry
from src.core.skills import Skill, SkillLibrary
from src.plugins.builtin.local.folders import RegisteredFolders
from src.plugins.builtin.local.skills import SkillsOnDisk


@pytest.mark.asyncio
async def test_local_skills_are_readable_without_a_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path))
    monkeypatch.setattr(
        "src.plugins.builtin.local.skills.global_skills", lambda: tmp_path / "skills"
    )
    monkeypatch.setattr("src.plugins.builtin.local.skills.elsewhere", lambda: ())
    library = SkillsOnDisk(RegisteredFolders())
    library.write(
        "local",
        Skill(
            "retry-check", "Retry check", "Check retry limits", "Use bounded retries."
        ),
        scope="global",
    )
    services = ServiceRegistry()
    services.register(SkillLibrary, library, "local")
    server = create_mcp_server(
        DefaultContextProvider(), services=services, surface=personal_surface()
    )
    async with create_connected_server_and_client_session(server) as session:
        names = {tool.name for tool in (await session.list_tools()).tools}
        assert {"search_skills", "get_skill"} <= names
        found = await session.call_tool(
            "search_skills", {"scope": {}, "text": "retry limits"}
        )
        assert not found.isError, found
        assert found.structuredContent["skills"][0]["id"] == "retry-check"
        assert "body" not in found.structuredContent["skills"][0]
        read = await session.call_tool(
            "get_skill", {"scope": {}, "id": "retry-check", "max_characters": 3}
        )
        assert not read.isError, read
        assert read.structuredContent["body"] == "Use"
        assert read.structuredContent["truncated"] is True
        missing = await session.call_tool("get_skill", {"scope": {}, "id": "missing"})
        assert missing.isError
        invalid = await session.call_tool("search_skills", {"scope": {}, "limit": 1000})
        assert invalid.isError
        outside = await session.call_tool(
            "search_skills", {"scope": {"repository": "unregistered"}}
        )
        assert outside.isError


@pytest.mark.asyncio
async def test_no_skill_library_advertises_no_skill_tools():
    server = create_mcp_server(DefaultContextProvider(), services=ServiceRegistry())
    async with create_connected_server_and_client_session(server) as session:
        names = {tool.name for tool in (await session.list_tools()).tools}
    assert not {"search_skills", "get_skill"} & names
