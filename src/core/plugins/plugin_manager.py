"""
Plugin manager for discovering, loading, and managing plugin lifecycle.
"""

import os
import sys
import importlib
import importlib.util
from pathlib import Path
from typing import Dict, List, Optional, Type, Any
import asyncio

from .base_plugin import BasePlugin, PluginType
from .plugin_registry import PluginRegistry, PluginStatus, plugin_registry
from .event_hooks import EventHooks, event_hooks
from src.utils.logger import logger


class PluginManager:
    """
    Manages plugin discovery, loading, and lifecycle.

    Handles finding plugins, loading them dynamically, managing their
    lifecycle (initialize, start, stop), and coordinating with the
    registry and event hook system.
    """

    def __init__(
        self,
        registry: Optional[PluginRegistry] = None,
        hooks: Optional[EventHooks] = None,
    ):
        """
        Initialize the plugin manager.

        Args:
            registry: Plugin registry instance (uses global if None)
            hooks: Event hooks instance (uses global if None)
        """
        self.registry = registry or plugin_registry
        self.hooks = hooks or event_hooks
        self._plugin_configs: Dict[str, Dict[str, Any]] = {}
        self._plugin_directories: List[Path] = []

    def add_plugin_directory(self, directory: Path):
        """
        Add a directory to search for plugins.

        Args:
            directory: Path to plugin directory
        """
        if directory.exists() and directory.is_dir():
            self._plugin_directories.append(directory)
            logger.info(f"Added plugin directory: {directory}")
        else:
            logger.warning(f"Plugin directory does not exist: {directory}")

    def set_plugin_config(self, plugin_name: str, config: Dict[str, Any]):
        """
        Set configuration for a plugin.

        Args:
            plugin_name: Name of the plugin
            config: Configuration dictionary
        """
        self._plugin_configs[plugin_name] = config
        logger.debug(f"Set config for plugin {plugin_name}: {list(config.keys())}")

    def discover_plugins(self) -> Dict[str, Any]:
        """
        Discover plugins in registered directories and via entry points.

        Searches for Python modules that contain plugin classes
        inheriting from BasePlugin. Also discovers pip-installed plugins
        that declare a ``sourceant.plugins`` entry point.

        Returns:
            Dictionary mapping plugin names to their source (Path or EntryPoint)
        """
        discovered_plugins = {}

        for plugin_dir in self._plugin_directories:
            logger.info(f"Discovering plugins in: {plugin_dir}")
            self._discover_in_directory(plugin_dir, discovered_plugins)

        self._discover_entrypoint_plugins(discovered_plugins)

        logger.info(f"Discovered {len(discovered_plugins)} plugins")
        return discovered_plugins

    def _discover_entrypoint_plugins(self, discovered_plugins: Dict[str, Any]):
        from importlib.metadata import entry_points

        eps = entry_points(group="sourceant.plugins")
        for ep in eps:
            if ep.name in discovered_plugins:
                continue
            try:
                plugin_class = ep.load()
                if isinstance(plugin_class, type) and issubclass(
                    plugin_class, BasePlugin
                ):
                    discovered_plugins[ep.name] = ep
                    logger.debug(f"Discovered entrypoint plugin: {ep.name}")
            except Exception as e:
                logger.warning(f"Error loading entrypoint plugin {ep.name}: {e}")

    def _discover_in_directory(
        self, directory: Path, discovered_plugins: Dict[str, Path]
    ):
        """
        Discover plugins in a specific directory.

        Args:
            directory: Directory to scan
            discovered_plugins: Dictionary to populate with discoveries
        """
        for item in directory.iterdir():
            if not item.is_dir() or item.name.startswith("_"):
                continue

            # For 'builtin' directory, recurse into it
            if item.name == "builtin":
                self._discover_in_directory(item, discovered_plugins)
                continue

            # Check if this directory is a plugin package
            plugin_file = item / "plugin.py"
            init_file = item / "__init__.py"

            if plugin_file.exists():
                plugin_path = plugin_file
                module_name = item.name
            elif init_file.exists():
                plugin_path = init_file
                module_name = item.name
            else:
                continue

            try:
                plugin_classes = self._find_plugin_classes(plugin_path, module_name)

                for plugin_class in plugin_classes:
                    plugin_name = getattr(
                        plugin_class, "_plugin_name", plugin_class.__name__
                    )
                    discovered_plugins[plugin_name] = plugin_path
                    logger.debug(f"Discovered plugin: {plugin_name} in {plugin_path}")

            except Exception as e:
                logger.warning(f"Error discovering plugin in {plugin_path}: {e}")

    @staticmethod
    def _resolve_dotted_package(package_dir: Path) -> Optional[str]:
        """Walk up from package_dir building a dotted path until no __init__.py is found."""
        parts = []
        current = package_dir
        while (current / "__init__.py").exists() or not parts:
            parts.append(current.name)
            parent = current.parent
            if parent == current:
                break
            current = parent
            if not (current / "__init__.py").exists():
                break

        if not parts:
            return None

        parts.reverse()
        return ".".join(parts)

    def _find_plugin_classes(
        self, plugin_path: Path, module_name: str
    ) -> List[Type[BasePlugin]]:
        """
        Find BasePlugin subclasses in a module.

        Args:
            plugin_path: Path to the plugin module
            module_name: Name of the module

        Returns:
            List of BasePlugin subclasses found
        """
        plugin_classes = []

        try:
            package_dir = plugin_path.parent
            dotted_package = self._resolve_dotted_package(package_dir)

            if dotted_package and dotted_package not in sys.modules:
                pkg_spec = importlib.util.spec_from_file_location(
                    dotted_package,
                    package_dir / "__init__.py",
                    submodule_search_locations=[str(package_dir)],
                )
                if pkg_spec:
                    pkg_module = importlib.util.module_from_spec(pkg_spec)
                    pkg_module.__path__ = [str(package_dir)]
                    sys.modules[dotted_package] = pkg_module
                    if pkg_spec.loader and (package_dir / "__init__.py").exists():
                        pkg_spec.loader.exec_module(pkg_module)

            qualified_name = (
                f"{dotted_package}.{plugin_path.stem}"
                if dotted_package
                else module_name
            )

            spec = importlib.util.spec_from_file_location(
                qualified_name, plugin_path, submodule_search_locations=[]
            )
            if spec is None or spec.loader is None:
                return plugin_classes

            module = importlib.util.module_from_spec(spec)
            module.__package__ = dotted_package or module_name
            sys.modules[qualified_name] = module
            spec.loader.exec_module(module)

            # Find BasePlugin subclasses
            for attr_name in dir(module):
                attr = getattr(module, attr_name)

                if (
                    isinstance(attr, type)
                    and issubclass(attr, BasePlugin)
                    and attr is not BasePlugin
                ):
                    plugin_classes.append(attr)

        except Exception as e:
            logger.error(f"Error loading module {module_name} from {plugin_path}: {e}")

        return plugin_classes

    async def load_plugin(
        self, plugin_path: Path, module_name: str
    ) -> Optional[BasePlugin]:
        """
        Load a single plugin from a file.

        Args:
            plugin_path: Path to the plugin file
            module_name: Name of the module

        Returns:
            Loaded plugin instance or None if failed
        """
        try:
            plugin_classes = self._find_plugin_classes(plugin_path, module_name)

            if not plugin_classes:
                logger.warning(f"No plugin classes found in {plugin_path}")
                return None

            if len(plugin_classes) > 1:
                logger.warning(
                    f"Multiple plugin classes found in {plugin_path}, using first one"
                )

            plugin_class = plugin_classes[0]
            plugin_name = getattr(plugin_class, "_plugin_name", plugin_class.__name__)

            # Get plugin configuration
            plugin_config = self._plugin_configs.get(plugin_name, {})

            # Create plugin instance
            plugin_instance = plugin_class(config=plugin_config)

            # Validate configuration if plugin has schema
            metadata = plugin_instance.metadata
            if metadata.config_schema:
                try:
                    plugin_instance.validate_config(plugin_config)
                except ValueError as e:
                    logger.error(f"Invalid configuration for plugin {plugin_name}: {e}")
                    return None

            # Register with registry
            self.registry.register(plugin_instance)
            self.registry.set_plugin_status(plugin_name, PluginStatus.LOADED)

            logger.info(f"Loaded plugin: {plugin_name} v{metadata.version}")
            return plugin_instance

        except Exception as e:
            logger.error(f"Error loading plugin from {plugin_path}: {e}", exc_info=True)
            return None

    async def load_all_plugins(self) -> Dict[str, BasePlugin]:
        """
        Discover and load all plugins.

        Returns:
            Dictionary of successfully loaded plugins
        """
        discovered = self.discover_plugins()
        loaded_plugins = {}

        logger.info(f"Loading {len(discovered)} discovered plugins")

        for plugin_name, source in discovered.items():
            if isinstance(source, Path):
                module_name = source.stem if source.is_file() else source.parent.name
                plugin = await self.load_plugin(source, module_name)
            else:
                plugin = await self._load_entrypoint_plugin(plugin_name, source)

            if plugin:
                loaded_plugins[plugin_name] = plugin

        logger.info(f"Successfully loaded {len(loaded_plugins)} plugins")
        return loaded_plugins

    async def _load_entrypoint_plugin(self, name: str, ep) -> Optional[BasePlugin]:
        try:
            plugin_class = ep.load()
            plugin_config = self._plugin_configs.get(name, {})
            plugin_instance = plugin_class(config=plugin_config)

            metadata = plugin_instance.metadata
            if metadata.config_schema:
                try:
                    plugin_instance.validate_config(plugin_config)
                except ValueError as e:
                    logger.error(f"Invalid configuration for plugin {name}: {e}")
                    return None

            self.registry.register(plugin_instance)
            self.registry.set_plugin_status(name, PluginStatus.LOADED)

            logger.info(f"Loaded entrypoint plugin: {name} v{metadata.version}")
            return plugin_instance
        except Exception as e:
            logger.error(f"Error loading entrypoint plugin {name}: {e}", exc_info=True)
            return None

    async def initialize_plugins(self) -> Dict[str, bool]:
        """
        Initialize all loaded plugins in dependency order.

        Returns:
            Dictionary mapping plugin names to success status
        """
        results = {}

        # Update dependency status
        self.registry.update_dependencies_status()

        # Get loading order based on dependencies
        try:
            loading_order = self.registry.get_loading_order()
        except ValueError as e:
            logger.error(f"Failed to resolve plugin dependencies: {e}")
            return results

        logger.info(f"Initializing plugins in order: {loading_order}")

        for plugin_name in loading_order:
            plugin_info = self.registry.get_plugin_info(plugin_name)
            if not plugin_info or not plugin_info.metadata.enabled:
                continue

            try:
                self.registry.set_plugin_status(plugin_name, PluginStatus.INITIALIZING)

                # Check dependencies
                if not plugin_info.dependencies_met:
                    missing_deps = [
                        dep
                        for dep in plugin_info.metadata.dependencies
                        if dep not in self.registry._plugins
                        or self.registry._plugins[dep].status
                        not in [PluginStatus.INITIALIZED, PluginStatus.STARTED]
                    ]
                    error_msg = f"Dependencies not met: {missing_deps}"
                    logger.error(
                        f"Plugin {plugin_name} initialization failed: {error_msg}"
                    )
                    self.registry.set_plugin_status(
                        plugin_name, PluginStatus.ERROR, error_msg
                    )
                    results[plugin_name] = False
                    continue

                # Initialize plugin
                await plugin_info.plugin.initialize()

                self.registry.set_plugin_status(plugin_name, PluginStatus.INITIALIZED)
                results[plugin_name] = True

                logger.info(f"Initialized plugin: {plugin_name}")

                # Update dependencies status for other plugins
                self.registry.update_dependencies_status()

            except Exception as e:
                error_msg = f"Initialization error: {str(e)}"
                logger.error(
                    f"Plugin {plugin_name} initialization failed: {error_msg}",
                    exc_info=True,
                )
                self.registry.set_plugin_status(
                    plugin_name, PluginStatus.ERROR, error_msg
                )
                results[plugin_name] = False

        successful = sum(1 for success in results.values() if success)
        logger.info(f"Initialized {successful}/{len(results)} plugins successfully")

        return results

    async def start_plugins(self) -> Dict[str, bool]:
        """
        Start all initialized plugins.

        Returns:
            Dictionary mapping plugin names to success status
        """
        results = {}

        # Execute app startup hooks
        await self.hooks.execute_hooks("app_startup")

        initialized_plugins = self.registry.get_plugins_by_status(
            PluginStatus.INITIALIZED
        )

        logger.info(f"Starting {len(initialized_plugins)} initialized plugins")

        for plugin in initialized_plugins:
            plugin_name = plugin.metadata.name

            try:
                self.registry.set_plugin_status(plugin_name, PluginStatus.STARTING)

                await plugin.start()

                self.registry.set_plugin_status(plugin_name, PluginStatus.STARTED)
                results[plugin_name] = True

                logger.info(f"Started plugin: {plugin_name}")

            except Exception as e:
                error_msg = f"Start error: {str(e)}"
                logger.error(
                    f"Plugin {plugin_name} start failed: {error_msg}", exc_info=True
                )
                self.registry.set_plugin_status(
                    plugin_name, PluginStatus.ERROR, error_msg
                )
                results[plugin_name] = False

        successful = sum(1 for success in results.values() if success)
        logger.info(f"Started {successful}/{len(results)} plugins successfully")

        return results

    async def stop_plugins(self) -> Dict[str, bool]:
        """
        Stop all running plugins.

        Returns:
            Dictionary mapping plugin names to success status
        """
        results = {}

        # Execute app shutdown hooks first
        await self.hooks.execute_hooks("app_shutdown")

        started_plugins = self.registry.get_plugins_by_status(PluginStatus.STARTED)

        # Stop plugins in reverse dependency order
        loading_order = self.registry.get_loading_order()
        stop_order = list(reversed(loading_order))

        logger.info(f"Stopping {len(started_plugins)} running plugins")

        for plugin_name in stop_order:
            plugin_info = self.registry.get_plugin_info(plugin_name)
            if not plugin_info or plugin_info.status != PluginStatus.STARTED:
                continue

            try:
                self.registry.set_plugin_status(plugin_name, PluginStatus.STOPPING)

                await plugin_info.plugin.stop()

                self.registry.set_plugin_status(plugin_name, PluginStatus.STOPPED)
                results[plugin_name] = True

                logger.info(f"Stopped plugin: {plugin_name}")

            except Exception as e:
                error_msg = f"Stop error: {str(e)}"
                logger.error(
                    f"Plugin {plugin_name} stop failed: {error_msg}", exc_info=True
                )
                results[plugin_name] = False

        successful = sum(1 for success in results.values() if success)
        logger.info(f"Stopped {successful}/{len(results)} plugins successfully")

        return results

    async def cleanup_plugins(self) -> Dict[str, bool]:
        """
        Cleanup all plugins and unload them.

        Returns:
            Dictionary mapping plugin names to success status
        """
        results = {}

        all_plugins = self.registry.get_all_plugins()

        logger.info(f"Cleaning up {len(all_plugins)} plugins")

        for plugin in all_plugins:
            plugin_name = plugin.metadata.name

            try:
                await plugin.cleanup()

                # Unregister from hooks
                self.hooks.unregister_plugin_hooks(plugin_name)

                # Unregister from registry
                self.registry.unregister(plugin_name)

                results[plugin_name] = True
                logger.info(f"Cleaned up plugin: {plugin_name}")

            except Exception as e:
                logger.error(f"Plugin {plugin_name} cleanup failed: {e}", exc_info=True)
                results[plugin_name] = False

        successful = sum(1 for success in results.values() if success)
        logger.info(f"Cleaned up {successful}/{len(results)} plugins successfully")

        return results

    async def reload_plugin(self, plugin_name: str) -> bool:
        """
        Reload a specific plugin.

        Args:
            plugin_name: Name of the plugin to reload

        Returns:
            True if reload successful
        """
        plugin_info = self.registry.get_plugin_info(plugin_name)
        if not plugin_info:
            logger.error(f"Plugin {plugin_name} not found for reload")
            return False

        logger.info(f"Reloading plugin: {plugin_name}")

        try:
            # Stop and cleanup if running
            if plugin_info.status == PluginStatus.STARTED:
                await plugin_info.plugin.stop()

            await plugin_info.plugin.cleanup()

            # Unregister hooks
            self.hooks.unregister_plugin_hooks(plugin_name)

            # Find and reload the plugin module
            # Note: This is a simplified reload - in production you might want
            # to track the original module path
            discovered = self.discover_plugins()

            if plugin_name not in discovered:
                logger.error(f"Plugin {plugin_name} not found during reload discovery")
                return False

            source = discovered[plugin_name]

            # Unregister old plugin
            self.registry.unregister(plugin_name)

            # Load new plugin
            if isinstance(source, Path):
                module_name = source.stem if source.is_file() else source.parent.name
                new_plugin = await self.load_plugin(source, module_name)
            else:
                new_plugin = await self._load_entrypoint_plugin(plugin_name, source)
            if not new_plugin:
                logger.error(f"Failed to load plugin {plugin_name} during reload")
                return False

            # Initialize and start if needed
            await new_plugin.initialize()
            self.registry.set_plugin_status(plugin_name, PluginStatus.INITIALIZED)

            await new_plugin.start()
            self.registry.set_plugin_status(plugin_name, PluginStatus.STARTED)

            logger.info(f"Successfully reloaded plugin: {plugin_name}")
            return True

        except Exception as e:
            logger.error(f"Error reloading plugin {plugin_name}: {e}", exc_info=True)
            return False

    def get_plugin_status_summary(self) -> Dict[str, Dict[str, int]]:
        """
        Get a summary of plugin statuses.

        Returns:
            Dictionary with status counts and other metrics
        """
        status_counts = {}
        type_counts = {}

        for plugin_info in self.registry._plugins.values():
            status = plugin_info.status.value
            plugin_type = plugin_info.metadata.plugin_type.value

            status_counts[status] = status_counts.get(status, 0) + 1
            type_counts[plugin_type] = type_counts.get(plugin_type, 0) + 1

        return {
            "status_counts": status_counts,
            "type_counts": type_counts,
            "total_plugins": len(self.registry._plugins),
            "enabled_plugins": len(
                [p for p in self.registry._plugins.values() if p.metadata.enabled]
            ),
        }


# Global plugin manager instance
plugin_manager = PluginManager()
