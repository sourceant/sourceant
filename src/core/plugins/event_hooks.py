"""
Event hook system for the SourceAnt plugin system.

Allows plugins to register hooks that are called at specific points
in the application lifecycle and request processing.
"""

import asyncio
from enum import IntEnum
from typing import Dict, List, Callable, Any, Optional, Union
from dataclasses import dataclass
from src.utils.logger import logger


class HookPriority(IntEnum):
    """Hook execution priority levels."""

    HIGHEST = 0
    HIGH = 25
    NORMAL = 50
    LOW = 75
    LOWEST = 100


@dataclass
class HookRegistration:
    """Hook registration information."""

    plugin_name: str
    callback: Callable
    priority: HookPriority
    async_callback: bool

    def __lt__(self, other):
        """Sort by priority (lower number = higher priority)."""
        return self.priority < other.priority


class EventHooks:
    """
    Manages event hooks for the plugin system.

    Provides a way for plugins to register callbacks that are executed
    at specific points in the application lifecycle and for event broadcasting.
    """

    # Available hook points
    HOOK_POINTS = {
        # Application lifecycle
        "app_startup",
        "app_shutdown",
        # Repository events
        "before_webhook_processing",
        "after_webhook_processing",
        # Code review lifecycle
        "before_review_generation",
        "after_review_generation",
        "before_review_posting",
        "after_review_posting",
        # Authentication events
        "before_user_authentication",
        "after_user_authentication",
        "before_user_logout",
        "after_user_logout",
        # Repository management
        "before_repository_authorization",
        "after_repository_authorization",
        "before_webhook_setup",
        "after_webhook_setup",
        # Error handling
        "on_error",
        "on_plugin_error",
    }

    def __init__(self):
        """Initialize the event hooks system."""
        self._hooks: Dict[str, List[HookRegistration]] = {
            hook: [] for hook in self.HOOK_POINTS
        }
        self._hook_stats: Dict[str, Dict[str, int]] = {}
        # Event subscribers for broadcasting system
        self._event_subscribers: Dict[str, List[HookRegistration]] = {}

    def register_hook(
        self,
        hook_name: str,
        callback: Callable,
        plugin_name: str,
        priority: HookPriority = HookPriority.NORMAL,
    ) -> bool:
        """
        Register a hook callback.

        Args:
            hook_name: Name of the hook point
            callback: Callback function to execute
            plugin_name: Name of the plugin registering the hook
            priority: Execution priority

        Returns:
            True if registration successful

        Raises:
            ValueError: If hook_name is not valid
        """
        if hook_name not in self.HOOK_POINTS:
            raise ValueError(
                f"Invalid hook name: {hook_name}. Valid hooks: {self.HOOK_POINTS}"
            )

        # Determine if callback is async
        is_async = asyncio.iscoroutinefunction(callback)

        registration = HookRegistration(
            plugin_name=plugin_name,
            callback=callback,
            priority=priority,
            async_callback=is_async,
        )

        # Insert in priority order
        hooks_list = self._hooks[hook_name]
        hooks_list.append(registration)
        hooks_list.sort()

        logger.info(
            f"Registered {hook_name} hook for plugin {plugin_name} with priority {priority}"
        )
        return True

    def unregister_hook(self, hook_name: str, plugin_name: str) -> bool:
        """
        Unregister all hooks for a plugin at a specific hook point.

        Args:
            hook_name: Name of the hook point
            plugin_name: Name of the plugin

        Returns:
            True if any hooks were removed
        """
        if hook_name not in self._hooks:
            return False

        hooks_list = self._hooks[hook_name]
        original_count = len(hooks_list)

        self._hooks[hook_name] = [
            hook for hook in hooks_list if hook.plugin_name != plugin_name
        ]

        removed_count = original_count - len(self._hooks[hook_name])
        if removed_count > 0:
            logger.info(
                f"Unregistered {removed_count} {hook_name} hooks for plugin {plugin_name}"
            )

        return removed_count > 0

    def unregister_plugin_hooks(self, plugin_name: str) -> int:
        """
        Unregister all hooks for a plugin.

        Args:
            plugin_name: Name of the plugin

        Returns:
            Total number of hooks removed
        """
        total_removed = 0

        for hook_name in self._hooks:
            hooks_list = self._hooks[hook_name]
            original_count = len(hooks_list)

            self._hooks[hook_name] = [
                hook for hook in hooks_list if hook.plugin_name != plugin_name
            ]

            removed_count = original_count - len(self._hooks[hook_name])
            total_removed += removed_count

        if total_removed > 0:
            logger.info(
                f"Unregistered {total_removed} total hooks for plugin {plugin_name}"
            )

        return total_removed

    async def execute_hooks(
        self, hook_name: str, context: Optional[Dict[str, Any]] = None, **kwargs
    ) -> Dict[str, Any]:
        """
        Execute all registered hooks for a hook point.

        Args:
            hook_name: Name of the hook point
            context: Context data passed to hooks
            **kwargs: Additional arguments passed to hooks

        Returns:
            Dictionary containing results from all hooks

        Raises:
            ValueError: If hook_name is not valid
        """
        if hook_name not in self.HOOK_POINTS:
            raise ValueError(f"Invalid hook name: {hook_name}")

        hooks_list = self._hooks[hook_name]
        if not hooks_list:
            return {}

        logger.debug(f"Executing {len(hooks_list)} hooks for {hook_name}")

        # Initialize stats if not exists
        if hook_name not in self._hook_stats:
            self._hook_stats[hook_name] = {"executed": 0, "errors": 0}

        results = {}
        context = context or {}

        for hook in hooks_list:
            try:
                if hook.async_callback:
                    result = await hook.callback(context, **kwargs)
                else:
                    result = hook.callback(context, **kwargs)

                results[hook.plugin_name] = result
                self._hook_stats[hook_name]["executed"] += 1

            except Exception as e:
                logger.error(
                    f"Error executing {hook_name} hook for plugin {hook.plugin_name}: {e}",
                    exc_info=True,
                )
                self._hook_stats[hook_name]["errors"] += 1
                results[hook.plugin_name] = {"error": str(e)}

                # Execute error hooks if this isn't already an error hook
                if hook_name != "on_plugin_error":
                    await self.execute_hooks(
                        "on_plugin_error",
                        {
                            "plugin_name": hook.plugin_name,
                            "hook_name": hook_name,
                            "error": e,
                            "original_context": context,
                        },
                    )

        return results

    def get_hooks(self, hook_name: str) -> List[HookRegistration]:
        """
        Get all registered hooks for a hook point.

        Args:
            hook_name: Name of the hook point

        Returns:
            List of hook registrations
        """
        return self._hooks.get(hook_name, []).copy()

    def get_plugin_hooks(self, plugin_name: str) -> Dict[str, List[HookRegistration]]:
        """
        Get all hooks registered by a specific plugin.

        Args:
            plugin_name: Name of the plugin

        Returns:
            Dictionary mapping hook names to registrations
        """
        plugin_hooks = {}

        for hook_name, hooks_list in self._hooks.items():
            plugin_hooks_for_point = [
                hook for hook in hooks_list if hook.plugin_name == plugin_name
            ]
            if plugin_hooks_for_point:
                plugin_hooks[hook_name] = plugin_hooks_for_point

        return plugin_hooks

    def get_statistics(self) -> Dict[str, Dict[str, int]]:
        """
        Get hook execution statistics.

        Returns:
            Dictionary containing execution and error counts per hook
        """
        return self._hook_stats.copy()

    def clear_statistics(self):
        """Clear hook execution statistics."""
        self._hook_stats.clear()

    def subscribe_to_events(
        self,
        plugin_name: str,
        callback: Callable,
        event_types: List[str],
        priority: HookPriority = HookPriority.NORMAL,
    ) -> bool:
        """
        Subscribe a plugin to specific event types for broadcasting.

        Args:
            plugin_name: Name of the plugin
            callback: Callback function to execute
            event_types: List of event types to subscribe to
            priority: Execution priority

        Returns:
            True if subscription successful
        """
        # Determine if callback is async
        is_async = asyncio.iscoroutinefunction(callback)

        registration = HookRegistration(
            plugin_name=plugin_name,
            callback=callback,
            priority=priority,
            async_callback=is_async,
        )

        # Subscribe to each event type
        for event_type in event_types:
            if event_type not in self._event_subscribers:
                self._event_subscribers[event_type] = []

            self._event_subscribers[event_type].append(registration)
            self._event_subscribers[event_type].sort()

            logger.info(f"Subscribed plugin {plugin_name} to event {event_type}")

        return True

    async def broadcast_event(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        source_plugin: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Broadcast an event to all subscribed plugins.

        Args:
            event_type: Type of event to broadcast
            event_data: Event data to pass to subscribers
            source_plugin: Plugin that triggered the event

        Returns:
            Dictionary with results from all subscribers
        """
        logger.debug(
            f"Broadcasting event {event_type} from {source_plugin or 'system'}"
        )

        results = {}

        # Get subscribers for this event type
        subscribers = self._event_subscribers.get(event_type, [])

        if not subscribers:
            logger.debug(f"No subscribers for event {event_type}")
            return results

        # Execute all subscribers
        for registration in subscribers:
            try:
                logger.debug(
                    f"Executing {event_type} handler for plugin {registration.plugin_name}"
                )

                if registration.async_callback:
                    result = await registration.callback(event_type, event_data)
                else:
                    result = registration.callback(event_type, event_data)

                results[registration.plugin_name] = result

                # Update statistics
                if event_type not in self._hook_stats:
                    self._hook_stats[event_type] = {}

                plugin_stats = self._hook_stats[event_type]
                plugin_stats[registration.plugin_name] = (
                    plugin_stats.get(registration.plugin_name, 0) + 1
                )

            except Exception as e:
                logger.error(
                    f"Error executing {event_type} handler for plugin {registration.plugin_name}: {e}"
                )
                results[registration.plugin_name] = {"error": str(e)}

        logger.debug(
            f"Event {event_type} broadcast completed with {len(results)} responses"
        )
        return results


# Global event hooks instance
event_hooks = EventHooks()
