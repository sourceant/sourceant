"""The model named at the narrowest scope that names one."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.config.settings import DEFAULT_TOKEN_LIMIT, LLM_MODEL, LLM_TOKEN_LIMIT
from src.core.settings.resolver import value_of
from src.llms.litellm_provider import LiteLLMProvider
from src.llms.llm_interface import LLMInterface
from src.utils.logger import logger


@dataclass(frozen=True)
class Choice:
    """What decides which model is asked, and on whose account."""

    name: str
    api_key: str = ""
    base_url: str = ""
    token_limit: int = DEFAULT_TOKEN_LIMIT


@dataclass
class SettingsModelSource:
    """Reads the model from settings: user, then repository, then organisation,
    then what the deployment was started with.
    """

    fallback_model: str = LLM_MODEL
    fallback_token_limit: int = LLM_TOKEN_LIMIT

    def model_for(
        self,
        *,
        repository: Optional[str] = None,
        organization: Optional[str] = None,
        user: Optional[str] = None,
    ) -> LLMInterface | None:
        choice = self.choice_for(
            repository=repository, organization=organization, user=user
        )
        if choice is None:
            return None
        logger.info(f"Asking {choice.name}")
        return LiteLLMProvider(
            model=choice.name,
            token_limit=choice.token_limit,
            api_key=choice.api_key,
            api_base=choice.base_url,
            attribution={
                "repository": repository,
                "organization": organization,
                "user": user,
            },
        )

    def choice_for(
        self,
        *,
        repository: Optional[str] = None,
        organization: Optional[str] = None,
        user: Optional[str] = None,
    ) -> Choice | None:
        """What was chosen, before a provider is built from it."""

        def named(key: str) -> str:
            return str(
                value_of(
                    key, repository=repository, organization=organization, user=user
                )
                or ""
            )

        name = named("model.name")
        if name:
            return Choice(
                name=name,
                api_key=named("model.api_key"),
                base_url=named("model.base_url"),
                token_limit=self._limit(repository, organization, user),
            )
        if self.fallback_model:
            return Choice(
                name=self.fallback_model, token_limit=self.fallback_token_limit
            )
        return None

    def _limit(self, repository, organization, user) -> int:
        stated = value_of(
            "model.token_limit",
            repository=repository,
            organization=organization,
            user=user,
        )
        try:
            return int(stated) if stated else DEFAULT_TOKEN_LIMIT
        except (TypeError, ValueError):
            return DEFAULT_TOKEN_LIMIT
