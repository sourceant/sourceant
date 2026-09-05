"""Runs SourceAnt from a personal computer rather than a hosted deployment.

Answers what the two deployments answer differently: which folders are covered,
where the skills are, and which model the bill goes to.
"""

from typing import Any, Dict, Optional

from src.config import settings
from src.config.db import get_engine
from src.core.environment import Environment
from src.core.model import LLMSource
from src.core.plugins import BasePlugin, PluginMetadata, PluginType
from src.core.repositories import RepositoryRegistry
from src.core.review import FindingStore, SQLFindingStore
from src.core.skills import SkillLibrary
from src.utils.logger import logger

from .environment import LocalEnvironment
from .folders import RegisteredFolders
from .llm import ChosenLLM
from .skills import SkillsOnDisk


class LocalPlugin(BasePlugin):
    """Registers the personal environment and what it can answer."""

    _plugin_name = "local"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="local",
            version="1.0.0",
            description="Runs SourceAnt from a personal computer rather than a hosted deployment",
            author="SourceAnt Team",
            plugin_type=PluginType.UTILITY,
            dependencies=[],
            config_schema={
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "Enable/disable the local environment",
                        "default": True,
                    },
                },
            },
            # `sourceant serve` turns this on; a deployment runs uvicorn
            # directly and leaves it off. Read through the module rather than
            # imported, because serve sets it after settings is first imported.
            enabled=settings.LOCAL_MODE,
            priority=20,
        )

    async def _initialize(self) -> None:
        logger.info("Initializing local plugin")

    async def _register_services(self) -> None:
        folders = RegisteredFolders()

        self.services.register(Environment, LocalEnvironment(), self.metadata.name)
        self.services.register(RepositoryRegistry, folders, self.metadata.name)
        self.services.register(SkillLibrary, SkillsOnDisk(folders), self.metadata.name)
        self.services.register(LLMSource, ChosenLLM(), self.metadata.name)

        findings = self._findings()
        if findings is not None:
            self.services.register(FindingStore, findings, self.metadata.name)

    @staticmethod
    def _findings():
        """Where findings are kept, or nothing where there is nowhere to keep them.

        Unregistered, a review runs and forgets, which is what it did before
        anything kept them.
        """
        engine = get_engine()
        if engine is None:
            return None
        try:
            return SQLFindingStore(engine, create_schema=True)
        except Exception as error:  # noqa: BLE001 - a store that will not open
            logger.warning(f"Reviews will not remember what they said: {error}")
            return None

    async def _start(self) -> None:
        logger.info("Local plugin started")

    async def _stop(self) -> None:
        logger.info("Local plugin stopped")

    async def _cleanup(self) -> None:
        logger.info("Local plugin cleanup completed")
