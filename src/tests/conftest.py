import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def mock_llm_provider(monkeypatch):
    """
    This function-scoped fixture runs automatically for every test. It patches
    the LiteLLMProvider class in the llm_factory module to prevent real API
    calls during tests.
    """
    with patch("src.llms.llm_factory.LiteLLMProvider", autospec=True) as mock_provider:
        instance = mock_provider.return_value
        instance.generate_code_review.return_value = None
        instance.count_tokens.return_value = 0
        instance.uploads_enabled = False

        yield mock_provider
