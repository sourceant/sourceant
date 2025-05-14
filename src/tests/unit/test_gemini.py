import pytest
import json
import os
from unittest.mock import patch, MagicMock
from src.llms.gemini import Gemini
from src.models.code_review import CodeReview, Verdict


# Mock the prompts before they are used
@patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"})
@patch(
    "src.prompts.prompts.Prompts.REVIEW_PROMPT", "Test prompt with {diff} and {context}"
)
@pytest.fixture
def gemini_instance():
    """Provides a Gemini instance with a mocked API key."""
    return Gemini()


def test_init_raises_value_error_if_api_key_not_set():
    """Test that Gemini raises a ValueError if the API key is not set."""
    with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
        with pytest.raises(
            ValueError, match="GEMINI_API_KEY environment variable not set"
        ):
            Gemini()


@patch("src.llms.gemini.genai.Client")
def test_generate_code_review_success(mock_genai_client, gemini_instance):
    """Test successful code review generation."""
    mock_model_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = json.dumps(
        {"summary": "Great job!", "verdict": "APPROVE", "code_suggestions": []}
    )
    mock_model_client.generate_content.return_value = mock_response
    mock_genai_client.return_value.models = mock_model_client

    diff = "- old code\n+ new code"
    review = gemini_instance.generate_code_review(diff)

    assert isinstance(review, CodeReview)
    assert review.summary == "Great job!"
    assert review.verdict == Verdict.APPROVE
    mock_model_client.generate_content.assert_called_once()


@patch("src.llms.gemini.genai.Client")
def test_generate_code_review_json_error(mock_genai_client, gemini_instance):
    """Test that None is returned when JSON decoding fails."""
    mock_model_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "this is not json"
    mock_model_client.generate_content.return_value = mock_response
    mock_genai_client.return_value.models = mock_model_client

    diff = "- old code\n+ new code"
    review = gemini_instance.generate_code_review(diff)

    assert review is None


@patch("src.llms.gemini.genai.Client")
def test_generate_code_review_api_error(mock_genai_client, gemini_instance):
    """Test that None is returned when the API call fails."""
    mock_model_client = MagicMock()
    mock_model_client.generate_content.side_effect = Exception("API is down")
    mock_genai_client.return_value.models = mock_model_client

    diff = "- old code\n+ new code"
    review = gemini_instance.generate_code_review(diff)

    assert review is None


@patch("src.llms.gemini.genai.Client")
def test_model_is_configurable_via_env_var(mock_genai_client):
    """Test that the Gemini model can be configured via an environment variable."""
    with patch.dict(os.environ, {"GEMINI_MODEL": "custom-model-name"}):
        gemini = Gemini()
        assert gemini.model == "custom-model-name"
