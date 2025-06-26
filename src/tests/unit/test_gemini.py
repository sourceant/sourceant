import pytest
import os
from unittest.mock import patch, MagicMock

from src.llms.gemini import Gemini
from src.models.code_review import CodeReview, Verdict, CodeReviewScores


@pytest.fixture
def mocked_gemini_client():
    """
    Provides a Gemini instance and a mock for the client's generate_content method.
    This is the correct way to mock the call chain used in the application.
    """
    # We patch 'genai' where it's used: in the 'gemini' module.
    with patch("src.llms.gemini.genai") as mock_genai, patch.dict(
        os.environ, {"GEMINI_API_KEY": "test_key"}
    ):
        # When Gemini() is initialized, it calls genai.Client().
        # We need to mock the return value of that call.
        mock_client_instance = MagicMock()
        mock_genai.Client.return_value = mock_client_instance

        # The application code calls client.models.generate_content(...).
        # We need to get a reference to that specific mock.
        mock_generate_content = mock_client_instance.models.generate_content

        gemini_instance = Gemini()
        yield gemini_instance, mock_generate_content


def test_init_raises_value_error_if_api_key_not_set():
    """Test that Gemini raises a ValueError if the API key is not set."""
    with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
        with pytest.raises(
            ValueError, match="GEMINI_API_KEY environment variable not set"
        ):
            Gemini()


def test_generate_code_review_success(mocked_gemini_client):
    """Test successful code review generation."""
    gemini_instance, mock_generate_content = mocked_gemini_client

    # Create a mock response object that mimics the real Gemini response
    mock_response = MagicMock()
    mock_response.parsed = CodeReview(
        summary="Great job!",
        verdict=Verdict.APPROVE,
        code_suggestions=[],
        scores=CodeReviewScores(
            correctness=9,
            clarity=8,
            maintainability=7,
            security=9,
            performance=8,
        ),
    )
    mock_generate_content.return_value = mock_response

    diff = "- old code\n+ new code"
    review = gemini_instance.generate_code_review(diff)

    assert isinstance(review, CodeReview)
    assert review.summary == "Great job!"
    # The verdict should be overridden to APPROVE based on the high scores
    assert review.verdict == Verdict.APPROVE
    mock_generate_content.assert_called_once()


def test_generate_code_review_verdict_approve_on_high_score(mocked_gemini_client):
    """Test that a high average score results in an APPROVE verdict."""
    gemini_instance, mock_generate_content = mocked_gemini_client

    mock_response = MagicMock()
    mock_response.parsed = CodeReview(
        summary="Decent code, but could be better.",
        verdict=Verdict.REQUEST_CHANGES,  # Initial verdict from LLM
        code_suggestions=[],
        scores=CodeReviewScores(
            correctness=8,
            clarity=7,
            maintainability=6,
            security=8,
            performance=7,
        ),
    )
    mock_generate_content.return_value = mock_response

    diff = "- old code\n+ new code"
    review = gemini_instance.generate_code_review(diff)

    assert review.verdict == Verdict.APPROVE


def test_generate_code_review_verdict_request_changes_on_low_score(
    mocked_gemini_client,
):
    """Test that a low average score results in a REQUEST_CHANGES verdict."""
    gemini_instance, mock_generate_content = mocked_gemini_client

    mock_response = MagicMock()
    mock_response.parsed = CodeReview(
        summary="This looks good to me!",
        verdict=Verdict.APPROVE,  # Initial verdict from LLM
        code_suggestions=[],
        scores=CodeReviewScores(
            correctness=2,
            clarity=2,
            maintainability=3,
            security=4,
            performance=3,
        ),
    )
    mock_generate_content.return_value = mock_response

    diff = "- old code\n+ new code"
    review = gemini_instance.generate_code_review(diff)

    assert review.verdict == Verdict.REQUEST_CHANGES


def test_generate_code_review_api_error(mocked_gemini_client):
    """Test that None is returned when the API call fails."""
    gemini_instance, mock_generate_content = mocked_gemini_client
    mock_generate_content.side_effect = Exception("API is down")

    diff = "- old code\n+ new code"
    review = gemini_instance.generate_code_review(diff)

    assert review is None


def test_model_is_configurable_via_env_var():
    """Test that the Gemini model can be configured via an environment variable."""
    # We still need to patch genai to prevent real calls during Gemini instantiation.
    with patch("src.llms.gemini.genai"), patch.dict(
        os.environ,
        {"GEMINI_API_KEY": "test_key", "GEMINI_MODEL": "custom-model-name"},
    ):
        gemini = Gemini()
        assert gemini.model_name == "custom-model-name"


def test_token_limit_is_configurable_via_env_var():
    """Test that the Gemini token limit can be configured via an environment variable."""
    # We still need to patch genai to prevent real calls during Gemini instantiation.
    with patch("src.llms.gemini.genai"), patch.dict(
        os.environ,
        {"GEMINI_API_KEY": "test_key", "GEMINI_TOKEN_LIMIT": "12345"},
    ):
        gemini = Gemini()
        assert gemini.token_limit == 12345
