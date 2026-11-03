import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from src.core.context import DefaultContextProvider
from src.core.knowledge import InMemoryKnowledgeRepository
from src.core.mcp import create_mcp_server
from src.core.memory import Memory, InMemoryMemory
from src.core.services import ServiceRegistry


def services():
    memory = InMemoryMemory()
    registry = ServiceRegistry()
    registry.register(Memory, memory, "test")
    return memory, registry


@pytest.mark.asyncio
@pytest.mark.parametrize("category_field", ["type", "kind", "both"])
async def test_knowledge_tools_record_changes_with_actor(monkeypatch, category_field):
    from mcp.server.auth.provider import AccessToken

    monkeypatch.setattr(
        "mcp.server.auth.middleware.auth_context.get_access_token",
        lambda: AccessToken(
            token="test", client_id="engineer", subject="engineer", scopes=[]
        ),
    )
    memory, registry = services()
    store = InMemoryKnowledgeRepository()
    server = create_mcp_server(
        DefaultContextProvider(knowledge=store), knowledge=store, services=registry
    )
    args = {
        "scope": {"repository": "acme/shop"},
        "id": "retry",
        "kind": "decision",
        "status": "accepted",
        "summary": "Retry three times",
    }
    async with create_connected_server_and_client_session(server) as session:
        for tool, changes in (
            ("put_knowledge", {}),
            ("update_context", {"summary": "Retry twice"}),
            ("update_context", {"status": "retired"}),
            ("capture_context", {"id": "timeout", "summary": "Stop after a second"}),
        ):
            args.update(changes)
            sent = dict(args)
            if category_field == "type":
                sent["type"] = sent.pop("kind")
            elif category_field == "both":
                sent["type"] = "decision"
                sent["kind"] = "constraint"
            result = await session.call_tool(tool, sent)
            assert not result.isError, result
            assert result.structuredContent["type"] == "decision"
    events = list(memory.events.values())
    assert [event["type"] for event in events] == [
        "knowledge.captured",
        "knowledge.updated",
        "knowledge.status_changed",
        "knowledge.captured",
    ]
    assert events[1]["attributes"]["knowledge.summary_before"] == "Retry three times"
    assert events[1]["attributes"]["knowledge.summary_after"] == "Retry twice"
    assert events[2]["attributes"]["knowledge.status_before"] == "accepted"
    assert events[2]["attributes"]["knowledge.status_after"] == "retired"
    for event in events:
        participant = event["participants"][0]
        assert memory.entities[participant["actor"]]["name"] == "engineer"
        subject = event["subjects"][0]["subject"]
        assert memory.entities[subject]["type"] == "knowledge.item"
        assert memory.entities[subject]["class"] == "subject"


@pytest.mark.asyncio
async def test_remember_then_recall_returns_when_why_who_and_paths():
    memory, registry = services()
    server = create_mcp_server(DefaultContextProvider(), services=registry)
    scope = {"repository": "acme/shop"}
    async with create_connected_server_and_client_session(server) as session:
        remembered = await session.call_tool(
            "remember",
            {
                "scope": scope,
                "text": "The retry experiment succeeded",
                "when": "2026-04-19T14:02:11Z",
                "why": "The gateway stops accepting requests after five tries",
                "who": "Ana",
                "paths": ["src/retry.py"],
                "valence": 2,
                "importance": 4,
            },
        )
        assert not remembered.isError, remembered
        recalled = await session.call_tool("recall", {"scope": scope, "query": "retry"})
        assert not recalled.isError, recalled
        isolated = await session.call_tool(
            "recall", {"scope": {"repository": "acme/other"}, "query": "retry"}
        )
        assert isolated.structuredContent["results"] == []
    event = recalled.structuredContent["results"][0]
    assert event["id"] == remembered.structuredContent["id"]
    assert event["what"] == "The retry experiment succeeded"
    assert event["why"] == "The gateway stops accepting requests after five tries"
    assert event["who"] == "Ana"
    stored = memory.events[event["id"]]
    assert memory.entities[stored["subjects"][0]["subject"]]["name"] == "src/retry.py"
    assert stored["appraisals"][0]["valence"] == 2
    assert stored["appraisals"][0]["importance"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"text": " "},
        {"when": "2026-04-19T14:02:11"},
        {"valence": 3},
        {"importance": 0},
    ],
)
async def test_remember_rejects_invalid_observations(changes):
    memory, registry = services()
    server = create_mcp_server(DefaultContextProvider(), services=registry)
    arguments = {
        "scope": {"actor": "Ana"},
        "text": "Retry succeeded",
        "when": "2026-04-19T14:02:11Z",
        **changes,
    }
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("remember", arguments)
    assert result.isError
    assert memory.events == {}


@pytest.mark.asyncio
async def test_unconfigured_memory_is_explicit_and_does_not_store(monkeypatch):
    from src.core.services import memory_services
    from src.core.memory import DisabledMemory, Memory

    monkeypatch.delenv("MEMORY_ENGINE_URL", raising=False)
    monkeypatch.delenv("MEMORY_ENGINE_KEY", raising=False)
    registry = ServiceRegistry()
    assert isinstance(memory_services(registry), DisabledMemory)
    assert registry.resolve(Memory) is memory_services(registry)
    server = create_mcp_server(DefaultContextProvider(), services=registry)
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "remember",
            {"scope": {}, "text": "Retry succeeded", "when": "2026-04-19T14:02:11Z"},
        )
        assert not result.isError
        assert result.structuredContent["configured"] is False
        recalled = await session.call_tool("recall", {"scope": {}, "query": "Retry"})
        assert recalled.structuredContent == {"results": [], "configured": False}
