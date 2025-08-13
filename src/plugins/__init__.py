"""
SourceAnt Plugin System

This package provides a flexible plugin architecture for extending SourceAnt functionality.
Plugins can add new integrations, authentication methods, LLM providers, and more.
"""

from .plugin_manager import PluginManager
from .base_plugin import BasePlugin, PluginMetadata
from .plugin_registry import PluginRegistry
from .event_hooks import EventHooks, HookPriority

__all__ = [
    "PluginManager",
    "BasePlugin", 
    "PluginMetadata",
    "PluginRegistry",
    "EventHooks",
    "HookPriority",
]