import pytest

from src.core.code_index import CodeNode, CodeTraversal, InMemoryCodeIndex
from src.core.context import ContextRequest, DefaultContextProvider
from src.core.knowledge import (
    InMemoryKnowledgeRepository,
    Knowledge,
    KnowledgeTraversal,
)
from src.core.scope import Scope

PROJECT = Scope.from_mapping({"project": "one"})
OTHER_PROJECT = Scope.from_mapping({"project": "two"})


def test_context_provider_combines_bounded_sources_inside_scope():
    code = InMemoryCodeIndex()
    knowledge = InMemoryKnowledgeRepository()
    for scope in (PROJECT, OTHER_PROJECT):
        code.put_node(scope, CodeNode("handler", frozenset({"Function"})))
        knowledge.put(scope, Knowledge("rule", "rule", "approved", "Validate input"))

    result = DefaultContextProvider(code=code, knowledge=knowledge).get_context(
        ContextRequest(
            PROJECT,
            code=CodeTraversal(PROJECT, ("handler",)),
            knowledge=KnowledgeTraversal(PROJECT, ("rule",)),
        )
    )

    assert [node.id for node in result.code.nodes] == ["handler"]
    assert [item.id for item in result.knowledge.items] == ["rule"]
    assert result.scope == PROJECT
    assert result.truncated is False


def test_context_request_rejects_empty_and_mixed_scope_queries():
    with pytest.raises(ValueError, match="at least one context source"):
        ContextRequest(PROJECT)

    with pytest.raises(ValueError, match="must use the request scope"):
        ContextRequest(PROJECT, code=CodeTraversal(OTHER_PROJECT, ("handler",)))


def test_context_provider_rejects_unconfigured_selected_source():
    request = ContextRequest(PROJECT, code=CodeTraversal(PROJECT, ("handler",)))

    with pytest.raises(ValueError, match="code context is not configured"):
        DefaultContextProvider().get_context(request)
