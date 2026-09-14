"""An instance answers to the model it was configured with, not two of them."""

from src.core.settings.resolver import set_value
from src.integrations.github.github import GitHub
from src.tests.base_test import BaseTestCase
from src.core.settings.configuration import Configuration

REPO = "sourceant/sourceant"


class TestReadingBackWhatWasWritten(BaseTestCase):

    def test_a_comment_is_read_by_the_model_the_repository_names(self):
        set_value("repository", REPO, "model.name", "openai/gpt-4o-mini")

        assert GitHub._llm_for(Configuration(repository=REPO)).model == (
            "openai/gpt-4o-mini"
        )

    def test_a_repository_that_names_none_falls_back_to_the_deployment(self):
        from src.config.settings import LLM_MODEL

        configuration = Configuration(repository="nobody/has-configured-this")

        assert GitHub._llm_for(configuration).model == LLM_MODEL

    def test_a_comment_is_read_by_the_model_the_review_was_answered_for(self):
        set_value("user", "7", "model.name", "openai/gpt-4o")

        unnamed = "nobody/has-configured-this"
        answered_for = Configuration(repository=unnamed, user="7")

        assert GitHub._llm_for(answered_for).model == "openai/gpt-4o"
        assert (
            GitHub._llm_for(Configuration(repository=unnamed)).model != "openai/gpt-4o"
        )
