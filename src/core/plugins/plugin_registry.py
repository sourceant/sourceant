"""
Plugin registry for tracking loaded plugins and their status.
"""

import time
from typing import Dict, List, Optional, Set
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime

from .base_plugin import BasePlugin, PluginMetadata
from src.utils.logger import logger


class PluginStatus(Enum):
    """Plugin status enumeration."""

    UNLOADED = "unloaded"
    LOADING = "loading"
    LOADED = "loaded"
    INITIALIZING = "initializing"
    INITIALIZED = "initialized"
    STARTING = "starting"
    STARTED = "started"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class PluginInfo:
    """Information about a registered plugin."""

    plugin: BasePlugin
    metadata: PluginMetadata
    status: PluginStatus = PluginStatus.LOADED
    load_time: Optional[datetime] = None
    start_time: Optional[datetime] = None
    error_message: Optional[str] = None
    dependencies_met: bool = False
    dependents: Set[str] = field(default_factory=set)


class PluginRegistry:
    """
    Registry for tracking loaded plugins and their status.

    Maintains information about all plugins including their metadata,
    current status, dependencies, and runtime information.
    """

    def __init__(self):
        """Initialize the plugin registry."""
        self._plugins: Dict[str, PluginInfo] = {}
        self._plugin_order: List[str] = []
        self._dependency_graph: Dict[str, Set[str]] = {}

    def register(self, plugin: BasePlugin) -> bool:
        """
        Register a plugin in the registry.

        Args:
            plugin: Plugin instance to register

        Returns:
            True if registration successful

        Raises:
            ValueError: If plugin is already registered or invalid
        """
        metadata = plugin.metadata

        if metadata.name in self._plugins:
            raise ValueError(f"Plugin '{metadata.name}' is already registered")

        # Validate metadata
        if not metadata.name or not metadata.version:
            raise ValueError("Plugin must have valid name and version")

        plugin_info = PluginInfo(
            plugin=plugin,
            metadata=metadata,
            status=PluginStatus.LOADED,
            load_time=datetime.utcnow(),
            dependencies_met=False,
        )

        self._plugins[metadata.name] = plugin_info
        self._plugin_order.append(metadata.name)
        self._dependency_graph[metadata.name] = set(metadata.dependencies)

        # Update dependents
        for dep_name in metadata.dependencies:
            if dep_name in self._plugins:
                self._plugins[dep_name].dependents.add(metadata.name)

        logger.info(f"Registered plugin: {metadata.name} v{metadata.version}")
        return True

    def unregister(self, plugin_name: str) -> bool:
        """
        Unregister a plugin from the registry.

        Args:
            plugin_name: Name of the plugin to unregister

        Returns:
            True if unregistration successful
        """
        if plugin_name not in self._plugins:
            return False

        plugin_info = self._plugins[plugin_name]

        # Check if any plugins depend on this one
        if plugin_info.dependents:
            dependent_names = ", ".join(plugin_info.dependents)
            logger.warning(
                f"Unregistering plugin '{plugin_name}' which has dependents: {dependent_names}"
            )

        # Remove from dependents of dependencies
        for dep_name in plugin_info.metadata.dependencies:
            if dep_name in self._plugins:
                self._plugins[dep_name].dependents.discard(plugin_name)

        # Remove from registry
        del self._plugins[plugin_name]
        self._plugin_order.remove(plugin_name)
        del self._dependency_graph[plugin_name]

        logger.info(f"Unregistered plugin: {plugin_name}")
        return True

    def get_plugin(self, plugin_name: str) -> Optional[BasePlugin]:
        """
        Get a plugin instance by name.

        Args:
            plugin_name: Name of the plugin

        Returns:
            Plugin instance or None if not found
        """
        plugin_info = self._plugins.get(plugin_name)
        return plugin_info.plugin if plugin_info else None

    def get_plugin_info(self, plugin_name: str) -> Optional[PluginInfo]:
        """
        Get plugin information by name.

        Args:
            plugin_name: Name of the plugin

        Returns:
            PluginInfo instance or None if not found
        """
        return self._plugins.get(plugin_name)

    def get_all_plugins(self) -> List[BasePlugin]:
        """
        Get all registered plugin instances.

        Returns:
            List of all plugin instances
        """
        return [info.plugin for info in self._plugins.values()]

    def get_plugins_by_type(self, plugin_type) -> List[BasePlugin]:
        """
        Get all plugins of a specific type.

        Args:
            plugin_type: PluginType to filter by

        Returns:
            List of plugins matching the type
        """
        return [
            info.plugin
            for info in self._plugins.values()
            if info.metadata.plugin_type == plugin_type
        ]

    def get_plugins_by_status(self, status: PluginStatus) -> List[BasePlugin]:
        """
        Get all plugins with a specific status.

        Args:
            status: PluginStatus to filter by

        Returns:
            List of plugins with matching status
        """
        return [info.plugin for info in self._plugins.values() if info.status == status]

    def set_plugin_status(
        self,
        plugin_name: str,
        status: PluginStatus,
        error_message: Optional[str] = None,
    ):
        """
        Update a plugin's status.

        Args:
            plugin_name: Name of the plugin
            status: New status
            error_message: Optional error message if status is ERROR
        """
        if plugin_name not in self._plugins:
            return

        plugin_info = self._plugins[plugin_name]
        old_status = plugin_info.status
        plugin_info.status = status
        plugin_info.error_message = error_message

        if status == PluginStatus.STARTED and old_status != PluginStatus.STARTED:
            plugin_info.start_time = datetime.utcnow()

        logger.debug(f"Plugin {plugin_name} status changed: {old_status} -> {status}")

        if error_message:
            logger.error(f"Plugin {plugin_name} error: {error_message}")

    def update_dependencies_status(self):
        """Update dependency satisfaction status for all plugins."""
        for plugin_name, plugin_info in self._plugins.items():
            dependencies = plugin_info.metadata.dependencies

            dependencies_met = all(
                dep_name in self._plugins
                and self._plugins[dep_name].status
                in [PluginStatus.STARTED, PluginStatus.INITIALIZED]
                for dep_name in dependencies
            )

            plugin_info.dependencies_met = dependencies_met

    def get_loading_order(self) -> List[str]:
        """
        Get the order in which plugins should be loaded based on dependencies.

        Returns:
            List of plugin names in loading order

        Raises:
            ValueError: If circular dependencies detected
        """
        # Topological sort for dependency resolution
        visited = set()
        temp_visited = set()
        result = []

        def visit(plugin_name: str):
            if plugin_name in temp_visited:
                raise ValueError(
                    f"Circular dependency detected involving plugin: {plugin_name}"
                )

            if plugin_name in visited:
                return

            temp_visited.add(plugin_name)

            # Visit dependencies first
            dependencies = self._dependency_graph.get(plugin_name, set())
            for dep_name in dependencies:
                if dep_name in self._plugins:  # Only consider registered dependencies
                    visit(dep_name)
                else:
                    logger.warning(
                        f"Plugin {plugin_name} depends on unregistered plugin: {dep_name}"
                    )

            temp_visited.remove(plugin_name)
            visited.add(plugin_name)
            result.append(plugin_name)

        # Visit all plugins
        for plugin_name in self._plugin_order:
            if plugin_name not in visited:
                visit(plugin_name)

        return result

    def get_enabled_plugins(self) -> List[BasePlugin]:
        """
        Get all enabled plugins.

        Returns:
            List of enabled plugin instances
        """
        return [info.plugin for info in self._plugins.values() if info.metadata.enabled]

    def enable_plugin(self, plugin_name: str) -> bool:
        """
        Enable a plugin.

        Args:
            plugin_name: Name of the plugin to enable

        Returns:
            True if successful
        """
        if plugin_name not in self._plugins:
            return False

        self._plugins[plugin_name].metadata.enabled = True
        logger.info(f"Enabled plugin: {plugin_name}")
        return True

    def disable_plugin(self, plugin_name: str) -> bool:
        """
        Disable a plugin.

        Args:
            plugin_name: Name of the plugin to disable

        Returns:
            True if successful
        """
        if plugin_name not in self._plugins:
            return False

        self._plugins[plugin_name].metadata.enabled = False
        logger.info(f"Disabled plugin: {plugin_name}")
        return True

    def get_plugin_summary(self) -> Dict[str, Dict[str, any]]:
        """
        Get a summary of all registered plugins.

        Returns:
            Dictionary containing plugin summaries
        """
        summary = {}

        for plugin_name, plugin_info in self._plugins.items():
            summary[plugin_name] = {
                "version": plugin_info.metadata.version,
                "type": plugin_info.metadata.plugin_type.value,
                "status": plugin_info.status.value,
                "enabled": plugin_info.metadata.enabled,
                "dependencies": plugin_info.metadata.dependencies,
                "dependents": list(plugin_info.dependents),
                "dependencies_met": plugin_info.dependencies_met,
                "load_time": (
                    plugin_info.load_time.isoformat() if plugin_info.load_time else None
                ),
                "start_time": (
                    plugin_info.start_time.isoformat()
                    if plugin_info.start_time
                    else None
                ),
                "error_message": plugin_info.error_message,
            }

        return summary

    def clear(self):
        """Clear all registered plugins."""
        self._plugins.clear()
        self._plugin_order.clear()
        self._dependency_graph.clear()
        logger.info("Cleared plugin registry")


# Global plugin registry instance
plugin_registry = PluginRegistry()
