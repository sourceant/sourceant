"""The model named at the narrowest scope that names one."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.config.settings import DEFAULT_TOKEN_LIMIT, LLM_MODEL, LLM_TOKEN_LIMIT
from src.core.settings.resolver import UNSTATED, value_of
from src.core.workspace import workspace_holding
from src.llms.litellm_provider import LiteLLMProvider
from src.llms.llm_interface import LLMInterface
from src.utils.logger import logger


@dataclass(frozen=True)
class LLMConfig:
    """What to call, where to call it, and on whose account."""

    name: str
    api_key: str = ""
    base_url: str = ""
    token_limit: int = DEFAULT_TOKEN_LIMIT

    def credentials(self) -> dict:
        """What litellm needs beyond the name, left out when nothing is set.

        Absent, litellm reads the environment, which is how a deployment that
        pays for its own calls has always configured this.
        """
        given = {}
        if self.api_key:
            given["api_key"] = self.api_key
        if self.base_url:
            given["api_base"] = self.base_url
        return given


@dataclass
class SettingsLLMSource:
    """Reads the model from settings: user, then repository, then the workspace
    holding that repository, then the organisation, then what the deployment was
    started with.
    """

    fallback_model: str = LLM_MODEL
    fallback_token_limit: int = LLM_TOKEN_LIMIT

    def provider_for(
        self,
        *,
        repository: Optional[str] = None,
        organization: Optional[str] = None,
        user: Optional[str] = None,
        workspace: Optional[str] = None,
    ) -> LLMInterface | None:
        # Worked out once here rather than left to each setting: the lookup goes
        # to the database, and a config is four settings deep.
        holder = workspace or (workspace_holding(repository) if repository else None)
        config = self.config_for(
            repository=repository,
            organization=organization,
            user=user,
            workspace=holder,
        )
        if config is None:
            return None
        logger.info(f"Asking {config.name}")
        return LiteLLMProvider(
            model=config.name,
            token_limit=config.token_limit,
            api_key=config.api_key,
            api_base=config.base_url,
            attribution={
                "repository": repository,
                "organization": organization,
                "user": user,
                "workspace": holder,
            },
        )

    def config_for(
        self,
        *,
        repository: Optional[str] = None,
        organization: Optional[str] = None,
        user: Optional[str] = None,
        workspace: Any = UNSTATED,
    ) -> LLMConfig | None:
        """What was chosen, before a provider is built from it."""

        def named(key: str) -> str:
            return str(
                value_of(
                    key,
                    repository=repository,
                    organization=organization,
                    user=user,
                    workspace=workspace,
                )
                or ""
            )

        name = named("model.name")
        if name:
            return LLMConfig(
                name=name,
                api_key=named("model.api_key"),
                base_url=named("model.base_url"),
                token_limit=self._limit(repository, organization, user, workspace),
            )
        if self.fallback_model:
            return LLMConfig(
                name=self.fallback_model, token_limit=self.fallback_token_limit
            )
        return None

    def _limit(self, repository, organization, user, workspace) -> int:
        stated = value_of(
            "model.token_limit",
            repository=repository,
            organization=organization,
            user=user,
            workspace=workspace,
        )
        try:
            return int(stated) if stated else DEFAULT_TOKEN_LIMIT
        except (TypeError, ValueError):
            return DEFAULT_TOKEN_LIMIT
