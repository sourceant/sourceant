import json
from pathlib import Path

import httpx
import litellm
import pytest
from litellm.llms.custom_httpx.http_handler import HTTPHandler

from src.core.model.settings import SettingsLLMSource
from src.core.review.checkout import CheckoutConfiguration


@pytest.mark.parametrize("model", ["openai/gpt-6-astra", "openai/gpt-6-luna"])
@pytest.mark.parametrize("effort", ["", "high"])
def test_reasoning_tools_use_responses_through_the_installed_bridge(
    monkeypatch, model, effort
):
    sent = []

    def capture(request):
        sent.append((request.url, json.loads(request.content)))
        captured = Path(__file__).parents[1] / "fixtures/openai/quota-exhausted.json"
        return httpx.Response(429, json=json.loads(captured.read_text()))

    configuration = CheckoutConfiguration(
        overrides={
            "model.name": model,
            "model.api_key": "test-placeholder",
            "model.base_url": "https://api.openai.com/v1",
            "model.max_output_tokens": 16384,
            "model.reasoning_effort": effort,
        }
    )
    provider = SettingsLLMSource().provider_for(configuration)
    with httpx.Client(transport=httpx.MockTransport(capture)) as client:
        handler = HTTPHandler(client=client)
        monkeypatch.setattr(
            "litellm.llms.custom_httpx.llm_http_handler._get_httpx_client",
            lambda **kwargs: handler,
        )
        with pytest.raises(litellm.RateLimitError, match="no credits remaining"):
            provider._completion(
                **provider._credentials(),
                model=provider.model,
                messages=[{"role": "user", "content": "Read the changed file."}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "parameters": {
                                "type": "object",
                                "properties": {"path": {"type": "string"}},
                                "required": ["path"],
                            },
                        },
                    }
                ],
                tool_choice="required",
                max_retries=0,
            )

    assert sent
    for url, body in sent:
        assert str(url) == "https://api.openai.com/v1/responses"
        assert body["model"] == model.removeprefix("openai/")
        if effort:
            assert body["reasoning"]["effort"] == effort
        assert body["max_output_tokens"] == 16384
        assert body["tool_choice"] == "required"
        assert body["tools"][0]["name"] == "read_file"
