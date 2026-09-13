import os

# The application reads DATABASE_URL when src.config.settings is imported, which
# happens while the first test module imports it. A fixture runs after that, so
# the choice has to be made here, at collection. It overrides rather than reads
# what is already set, because an inherited value names a database something else
# is using.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "sqlite:///./sourceant.db"
)

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
