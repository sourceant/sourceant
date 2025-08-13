"""
Base plugin interface and metadata for the SourceAnt plugin system.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum


class PluginType(Enum):
    """Plugin type enumeration."""

    INTEGRATION = "integration"
    AUTHENTICATION = "authentication"
    LLM = "llm"
    WEBHOOK = "webhook"
    NOTIFICATION = "notification"
    UTILITY = "utility"


@dataclass
class PluginMetadata:
    """Plugin metadata and configuration."""

    name: str
    version: str
    description: str
    author: str
    plugin_type: PluginType
    dependencies: List[str] = field(default_factory=list)
    config_schema: Optional[Dict[str, Any]] = None
    enabled: bool = True
    priority: int = 100  # Lower number = higher priority


class BasePlugin(ABC):
    """
    Base class for all SourceAnt plugins.

    Plugins must inherit from this class and implement the required methods.
    The plugin system will manage the lifecycle of plugin instances.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the plugin with configuration.

        Args:
            config: Plugin configuration dictionary
        """
        self.config = config or {}
        self._initialized = False
        self._started = False

    @property
    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata."""
        pass

    def validate_config(self, config: Dict[str, Any]) -> bool:
        """
        Validate plugin configuration against schema.

        Args:
            config: Configuration to validate

        Returns:
            True if configuration is valid

        Raises:
            ValueError: If configuration is invalid
        """
        # Default implementation - plugins can override for custom validation
        return True

    async def initialize(self) -> None:
        """
        Initialize the plugin.

        Called once during plugin loading. Use this for one-time setup
        like registering routes, connecting to databases, etc.
        """
        if self._initialized:
            return

        await self._initialize()
        self._initialized = True

    async def start(self) -> None:
        """
        Start the plugin.

        Called after all plugins are initialized. Use this for starting
        background tasks, connecting to external services, etc.
        """
        if not self._initialized:
            raise RuntimeError(
                f"Plugin {self.metadata.name} must be initialized before starting"
            )

        if self._started:
            return

        await self._start()
        self._started = True

    async def stop(self) -> None:
        """
        Stop the plugin.

        Called during application shutdown. Use this for cleanup,
        closing connections, stopping background tasks, etc.
        """
        if not self._started:
            return

        await self._stop()
        self._started = False

    async def cleanup(self) -> None:
        """
        Cleanup plugin resources.

        Called after stop() for final cleanup. Plugin will be unloaded after this.
        """
        await self._cleanup()
        self._initialized = False

    # Override these methods in your plugin implementation
    async def _initialize(self) -> None:
        """Plugin-specific initialization logic."""
        pass

    async def _start(self) -> None:
        """Plugin-specific start logic."""
        pass

    async def _stop(self) -> None:
        """Plugin-specific stop logic."""
        pass

    async def _cleanup(self) -> None:
        """Plugin-specific cleanup logic."""
        pass

    def get_config(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value.

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        return self.config.get(key, default)

    @property
    def is_initialized(self) -> bool:
        """Check if plugin is initialized."""
        return self._initialized

    @property
    def is_started(self) -> bool:
        """Check if plugin is started."""
        return self._started
