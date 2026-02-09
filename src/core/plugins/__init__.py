"""
SourceAnt Plugin System

Core plugin infrastructure for extending SourceAnt functionality.
"""

from .plugin_manager import PluginManager, plugin_manager
from .base_plugin import BasePlugin, PluginMetadata, PluginType
from .plugin_registry import PluginRegistry, PluginStatus, plugin_registry
from .event_hooks import EventHooks, HookPriority, event_hooks

__all__ = [
    "PluginManager",
    "plugin_manager",
    "BasePlugin",
    "PluginMetadata",
    "PluginType",
    "PluginRegistry",
    "PluginStatus",
    "plugin_registry",
    "EventHooks",
    "HookPriority",
    "event_hooks",
]
