import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from src.core.code_index import CodeNode, InMemoryCodeIndex
from src.core.context import DefaultContextProvider
from src.core.knowledge import InMemoryKnowledgeRepository, Knowledge
from src.core.scope import Scope
from src.mcp_server import create_mcp_server

PROJECT = Scope.from_mapping({"project": "one"})
OTHER_PROJECT = Scope.from_mapping({"project": "two"})


@pytest.mark.asyncio
async def test_mcp_get_context_uses_protocol_boundary_and_isolates_scope():
    code = InMemoryCodeIndex()
    knowledge = InMemoryKnowledgeRepository()
    for scope, summary in ((PROJECT, "Use one"), (OTHER_PROJECT, "Use two")):
        code.put_node(
            scope,
            CodeNode("handler", frozenset({"Function"}), {"scope": summary}),
        )
        knowledge.put(scope, Knowledge("rule", "rule", "approved", summary))

    server = create_mcp_server(DefaultContextProvider(code=code, knowledge=knowledge))
    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()
        result = await session.call_tool(
            "get_context",
            {
                "scope": {"project": "one"},
                "code_node_ids": ["handler"],
                "knowledge_ids": ["rule"],
            },
        )

    assert [tool.name for tool in tools.tools] == ["get_context"]
    assert result.isError is False
    assert result.structuredContent["scope"] == {"project": "one"}
    assert result.structuredContent["code"]["nodes"][0]["properties"] == {
        "scope": "Use one"
    }
    assert result.structuredContent["knowledge"]["items"][0]["summary"] == "Use one"
    assert result.structuredContent["truncated"] is False


@pytest.mark.asyncio
async def test_mcp_get_context_rejects_unbounded_and_empty_requests():
    server = create_mcp_server(DefaultContextProvider(code=InMemoryCodeIndex()))

    async with create_connected_server_and_client_session(server) as session:
        excessive = await session.call_tool(
            "get_context",
            {
                "scope": {"project": "one"},
                "code_node_ids": ["handler"],
                "depth": 4,
            },
        )
        excessive_limit = await session.call_tool(
            "get_context",
            {
                "scope": {"project": "one"},
                "code_node_ids": ["handler"],
                "limit": 51,
            },
        )
        empty = await session.call_tool(
            "get_context",
            {"scope": {"project": "one"}},
        )

    assert excessive.isError is True
    assert excessive_limit.isError is True
    assert empty.isError is True
