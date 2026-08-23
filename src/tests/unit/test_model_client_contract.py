"""The model client is mocked everywhere else, so nothing else would notice it break."""

import pytest


def test_a_response_can_be_constructed():
    from litellm.types.utils import ModelResponse

    # Raised PydanticUserError on litellm 1.97.0, which reached production and
    # stopped every review before a request was ever sent.
    ModelResponse()


def test_a_completion_can_be_made_without_a_provider():
    import litellm

    answer = litellm.completion(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hi"}],
        mock_response="hello",
    )

    assert answer.choices[0].message.content == "hello"
