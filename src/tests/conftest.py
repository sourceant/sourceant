import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def mock_gemini_client(monkeypatch):
    """
    This function-scoped fixture runs automatically for every test. It performs
    two critical functions:

    1.  Sets a dummy `GEMINI_API_KEY` environment variable. This is necessary
        to prevent a `ValueError` during test collection and app startup,
        which is triggered by the `llm()` call in the `lifespan` manager.

    2.  Patches the `Gemini` class itself within the `llm_factory` module.
        This ensures that no real `Gemini` client is ever created and no
        actual API calls are made during any test.
    """
    # 1. Set dummy key to pass the import-time check in main.py
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key-for-testing")

    # 2. Patch the class to prevent real instantiation and API calls
    with patch("src.llms.llm_factory.Gemini", autospec=True) as mock_gemini:
        # Configure the mock instance that will be returned when Gemini() is called
        instance = mock_gemini.return_value
        instance.generate_code_review.return_value = None
        instance.count_tokens.return_value = 0

        yield mock_gemini
