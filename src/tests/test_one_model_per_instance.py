"""An instance answers to the model it was configured with, not two of them."""

from src.core.settings.resolver import set_value
from src.integrations.github.github import GitHub
from src.tests.base_test import BaseTestCase

REPO = "sourceant/sourceant"


class TestReadingBackWhatWasWritten(BaseTestCase):

    def test_a_comment_is_read_by_the_model_the_repository_names(self):
        set_value("repository", REPO, "model.name", "openai/gpt-4o-mini")

        assert GitHub._llm_for(REPO).model == "openai/gpt-4o-mini"

    def test_a_repository_that_names_none_falls_back_to_the_deployment(self):
        from src.config.settings import LLM_MODEL

        assert GitHub._llm_for("nobody/has-configured-this").model == LLM_MODEL
