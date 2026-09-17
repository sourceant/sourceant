import json
from pathlib import Path

import httpx
import litellm
import pytest
from litellm.llms.custom_httpx.http_handler import HTTPHandler

from src.llms.litellm_provider import LiteLLMProvider

FIXTURES = Path(__file__).parents[1] / "fixtures/deepseek"


@pytest.fixture
def transport(monkeypatch, request):
    sent = []
    responses = iter(getattr(request, "param", []))

    def respond(request):
        body = json.loads(request.content)
        sent.append(body)
        rejected = body.get("tool_choice") == "required" and body.get("thinking") != {
            "type": "disabled"
        }
        name = (
            "unsupported-thinking-tool-choice"
            if rejected
            else next(responses, "overview")
        )
        return httpx.Response(
            400 if rejected else 200,
            json=json.loads((FIXTURES / f"{name}.json").read_text()),
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        handler = HTTPHandler(client=client)
        monkeypatch.setattr(
            "litellm.llms.custom_httpx.llm_http_handler._get_httpx_client",
            lambda **kwargs: handler,
        )
        yield sent


def provider():
    return LiteLLMProvider(
        model="deepseek/deepseek-v4-flash",
        token_limit=1000,
        api_key="test-placeholder",
    )


def test_tool_choice_rejection_retries_without_changing_thinking(transport):
    from src.llms.errors import LLMError

    model = provider()
    for _ in range(2):
        with pytest.raises(LLMError, match="required tool call"):
            model.ask_with_tools(
                [{"role": "user", "content": "Read the repository."}],
                [{"type": "function", "function": {"name": "read_file"}}],
                require=True,
            )
    assert len(transport) == 3
    assert transport[0]["tool_choice"] == "required"
    assert all("tool_choice" not in request for request in transport[1:])
    assert all(
        "thinking" not in request and "reasoning_effort" not in request
        for request in transport
    )


def test_optional_round_preserves_thinking(transport):
    provider().ask_with_tools(
        [{"role": "user", "content": "Read the repository."}],
        [{"type": "function", "function": {"name": "read_file"}}],
    )
    assert len(transport) == 1
    assert transport[0]["tool_choice"] == "auto"
    assert "thinking" not in transport[0]
    assert "reasoning_effort" not in transport[0]


def test_the_old_request_reproduces_the_captured_provider_error(transport):
    with pytest.raises(litellm.BadRequestError, match="Thinking mode does not support"):
        litellm.completion(
            model="deepseek/deepseek-v4-flash",
            api_key="test-placeholder",
            messages=[{"role": "user", "content": "Read the repository."}],
            tools=[{"type": "function", "function": {"name": "read_file"}}],
            tool_choice="required",
        )
    assert len(transport) == 1


@pytest.mark.parametrize(
    "transport,succeeds,attempts",
    [([], False, 2), (["thinking-read", "thinking-answer"], True, 3)],
    indirect=["transport"],
)
def test_discovery_job_through_real_provider_transport(
    monkeypatch, transport, succeeds, attempts
):
    from src.core.jobs.models import BACKGROUND, Job
    from src.core.topology import discovery
    from src.core.topology.discovery import READING, Readings

    monkeypatch.setattr(discovery, "_forge", lambda: object())
    monkeypatch.setattr(discovery, "_token", lambda *args, **kwargs: "test-placeholder")
    monkeypatch.setattr(discovery, "head_revision", lambda *args: "")
    monkeypatch.setattr(discovery, "already_read", lambda *args: False)
    monkeypatch.setattr(discovery, "remember_read", lambda *args: None)
    monkeypatch.setattr(
        "src.core.model.provider_for", lambda *args, **kwargs: provider()
    )
    monkeypatch.setattr(
        "src.core.topology.store.topology_repository", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        "src.core.topology.reading.contents_reader",
        lambda *args, **kwargs: (lambda path: None),
    )
    outcome = Readings().run(
        Job(
            id=1,
            lane=BACKGROUND,
            kind=READING,
            payload={
                "entity_id": "asset:checkout",
                "repository": "acme/checkout",
                "targets": ["asset:billing"],
                "workspace": "workspace-1",
                "user": "1",
                "persist": False,
            },
            state="running",
        )
    )
    assert outcome.succeeded is succeeds, outcome.error
    assert len(transport) == attempts
    assert transport[0]["tool_choice"] == "required"
    assert "tool_choice" not in transport[1]
    assert all("thinking" not in request for request in transport)


@pytest.mark.parametrize(
    "name", ["gemini/gemini-2.5-flash", "openai/gpt-4o", "anthropic/claude-sonnet-4"]
)
def test_other_providers_keep_their_tool_options(monkeypatch, name):
    from unittest.mock import Mock

    completion = Mock(
        return_value=litellm.ModelResponse(
            **json.loads((FIXTURES / "overview.json").read_text())
        )
    )
    monkeypatch.setattr(litellm, "completion", completion)
    LiteLLMProvider(model=name, token_limit=1000).ask_with_tools(
        [{"role": "user", "content": "Read the repository."}],
        [{"type": "function", "function": {"name": "read_file"}}],
        require=False,
    )
    assert "reasoning_effort" not in completion.call_args.kwargs
    assert completion.call_args.kwargs["tool_choice"] == "auto"


def test_unsupported_choice_is_omitted_using_installed_capabilities(monkeypatch):
    from unittest.mock import Mock

    model = "ollama/llama3"
    assert "tool_choice" not in litellm.get_supported_openai_params(model=model)
    completion = Mock(
        return_value=litellm.ModelResponse(
            **json.loads((FIXTURES / "overview.json").read_text())
        )
    )
    monkeypatch.setattr(litellm, "completion", completion)
    LiteLLMProvider(model=model, token_limit=1000).ask_with_tools(
        [{"role": "user", "content": "Read the repository."}],
        [{"type": "function", "function": {"name": "read_file"}}],
    )
    assert "tool_choice" not in completion.call_args.kwargs
    assert "reasoning_effort" not in completion.call_args.kwargs


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError(),
        litellm.BadRequestError(
            message=(FIXTURES / "unsupported-format.json").read_text(),
            model="deepseek-v4-flash",
            llm_provider="deepseek",
        ),
    ],
)
def test_unrelated_failures_are_not_retried(monkeypatch, failure):
    from unittest.mock import Mock

    completion = Mock(side_effect=failure)
    monkeypatch.setattr(litellm, "completion", completion)
    with pytest.raises(type(failure)):
        provider().ask_with_tools(
            [{"role": "user", "content": "Read the repository."}],
            [{"type": "function", "function": {"name": "read_file"}}],
            require=True,
        )
    completion.assert_called_once()


def test_full_assistant_message_survives_serialization(transport):
    from src.llms.messages import assistant_message

    answer = provider().ask_with_tools(
        [{"role": "user", "content": "Read the repository."}],
        [{"type": "function", "function": {"name": "read_file"}}],
    )
    expected = (
        litellm.ModelResponse(**json.loads((FIXTURES / "overview.json").read_text()))
        .choices[0]
        .message.model_dump(exclude_none=True)
    )
    assert assistant_message(answer) == expected


@pytest.mark.parametrize(
    "transport", [["thinking-read", "thinking-answer"]], indirect=True
)
def test_live_captured_thinking_survives_the_tool_followup(transport):
    from src.llms.messages import assistant_message

    model = provider()
    messages = [{"role": "user", "content": "Read README.md."}]
    tools = [{"type": "function", "function": {"name": "read_file"}}]
    first = model.ask_with_tools(messages, tools, require=True)
    messages.append(assistant_message(first))
    messages.append(
        {
            "role": "tool",
            "tool_call_id": first["tool_calls"][0]["id"],
            "content": "# Orchard\nA small test project.",
        }
    )
    second = model.ask_with_tools(messages, tools)
    assert len(transport) == 3
    assert first["assistant_message"]["reasoning_content"]
    assert (
        transport[2]["messages"][1]["reasoning_content"]
        == first["assistant_message"]["reasoning_content"]
    )
    assert second["assistant_message"]["reasoning_content"]
    assert "Orchard" in second["content"]
    assert not second["tool_calls"]
    assert all(
        "thinking" not in request and "reasoning_effort" not in request
        for request in transport
    )
