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

# Every variable a provider reads a key from. A machine that holds one made
# the suite pass where CI, holding none, did not.
PROVIDER_KEYS = (
    "ANTHROPIC_API_KEY",
    "AZURE_API_KEY",
    "COHERE_API_KEY",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "OPENAI_API_KEY",
    "XAI_API_KEY",
)


@pytest.fixture(autouse=True)
def no_borrowed_credentials(monkeypatch):
    """A test asks for the key it needs rather than finding one lying about."""
    for name in PROVIDER_KEYS:
        monkeypatch.delenv(name, raising=False)


def _asks_every_time(key, **scopes):
    """Zero reuse, and whatever the setting says for everything else."""
    from src.core.settings.resolver import value_of as settled

    if key == "review.reuse_responses_days":
        return 0
    return settled(key, **scopes)


@pytest.fixture(autouse=True)
def ask_every_time(monkeypatch):
    """No test reuses an answer another test was given.

    Answers are kept against the exact question asked, which never collides in
    a deployment because two changes are never the same text. Here it collides
    constantly: a suite asks a handful of one-line prompts of the same model,
    and a test written to observe a call would silently observe none.

    A test about the cache turns it back on for itself.
    """
    monkeypatch.setattr("src.core.settings.value_of", _asks_every_time)


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
