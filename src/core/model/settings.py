"""The model named at the narrowest scope that names one."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

from src.config.settings import DEFAULT_TOKEN_LIMIT, LLM_MODEL, LLM_TOKEN_LIMIT
from src.core.settings.resolver import value_of
from src.llms.litellm_provider import LiteLLMProvider
from src.llms.llm_interface import LLMInterface
from src.utils.logger import logger

# Enough that a busy deployment rebuilds one rarely.
MOST_KEPT = 64


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

    Providers are cached on the whole Choice, so editing a key takes effect on
    the next call rather than after a restart.
    """

    fallback_model: str = LLM_MODEL
    fallback_token_limit: int = LLM_TOKEN_LIMIT
    _built: "OrderedDict" = field(default_factory=OrderedDict, repr=False)

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
        # Keyed on who is asking as well as what is asked, because what a call
        # consumed is recorded against the scope the model was chosen for, and a
        # provider kept for one scope would report every later call as that one.
        asking = (choice, repository, organization, user)
        if asking not in self._built:
            logger.info(f"Asking {choice.name}")
            self._built[asking] = LiteLLMProvider(
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
            # The scope is part of the key, so a deployment reviewing many
            # repositories would otherwise keep one of these per repository for
            # the life of the process.
            while len(self._built) > MOST_KEPT:
                self._built.popitem(last=False)
        self._built.move_to_end(asking)
        return self._built[asking]

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
