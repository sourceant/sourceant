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
    from src.plugins.builtin.code_reviewer.prompts import ReviewPrompts

    ReviewPrompts().add_tools(server, personal_surface())
    async with create_connected_server_and_client_session(server) as session:
        prompts = {prompt.name for prompt in (await session.list_prompts()).prompts}
        assert {"review", "context", "remember", "skill:retry-check"} <= prompts
        context = await session.get_prompt("context", {"about": "retries"})
        assert "retries" in context.messages[0].content.text
        names = {tool.name for tool in (await session.list_tools()).tools}
        assert {"search_skills", "get_skill", "save_skill", "delete_skill"} <= names
        saved = await session.call_tool(
            "save_skill",
            {
                "scope": {},
                "id": "requirements-check",
                "name": "Requirements check",
                "description": "Check requirements",
                "kind": "requirements-analysis",
                "content": {
                    "instructions": "Check acceptance criteria.",
                    "examples": ["Missing failure case"],
                },
                "metadata": {"owner": "platform"},
                "properties": {"license": "MIT"},
                "paths": ["specs/**"],
                "applications": {"requirements": True, "review": False},
                "automatic": False,
            },
        )
        assert not saved.isError, saved
        prompts = {prompt.name for prompt in (await session.list_prompts()).prompts}
        assert {"use_skill", "choose_skill", "skill:requirements-check"} <= prompts
        applied = await session.get_prompt(
            "skill:requirements-check", {"task": "Check the checkout requirements"}
        )
        assert "Check acceptance criteria." in applied.messages[0].content.text
        assert "Missing failure case" in applied.messages[0].content.text
        assert "Check the checkout requirements" in applied.messages[0].content.text
        invoked = await session.get_prompt("use_skill", {"id": "requirements-check"})
        assert "Check acceptance criteria." in invoked.messages[0].content.text
        read_saved = await session.call_tool(
            "get_skill", {"scope": {}, "id": "requirements-check"}
        )
        assert not read_saved.isError, read_saved
        assert read_saved.structuredContent["kind"] == "requirements-analysis"
        assert read_saved.structuredContent["properties"] == {"license": "MIT"}
        selected = await session.call_tool(
            "search_skills",
            {"scope": {}, "kind": "requirements-analysis", "purpose": "requirements"},
        )
        assert selected.structuredContent["total"] == 1
        deleted = await session.call_tool(
            "delete_skill", {"scope": {}, "id": "requirements-check"}
        )
        assert deleted.structuredContent["deleted"] is True
        assert "skill:requirements-check" not in {
            prompt.name for prompt in (await session.list_prompts()).prompts
        }
        from mcp.shared.exceptions import McpError

        with pytest.raises(McpError):
            await session.get_prompt("skill:requirements-check")
        with pytest.raises(McpError):
            await session.get_prompt(
                "use_skill", {"id": "retry-check", "repository": "unregistered"}
            )
        imported = await session.call_tool(
            "save_skill",
            {
                "scope": {},
                "id": "imported-check",
                "document": "---\nname: Imported check\ndescription: Check requirements\nlicense: MIT\nmetadata:\n  sourceant:\n    type: requirements-analysis\n---\n\nCheck acceptance criteria.\n",
            },
        )
        assert not imported.isError, imported
        assert imported.structuredContent["properties"]["license"] == "MIT"
        updated = await session.call_tool(
            "save_skill",
            {
                "scope": {},
                "id": "imported-check",
                "name": "Updated check",
                "description": "Check requirements",
                "body": "Check failure cases.",
                "kind": "requirements-analysis",
            },
        )
        assert not updated.isError, updated
        assert updated.structuredContent["body"] == "Check failure cases."
        applied = await session.get_prompt("skill:imported-check")
        assert "Check failure cases." in applied.messages[0].content.text
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
