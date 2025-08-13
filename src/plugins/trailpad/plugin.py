"""
Trailpad Integration Plugin.

Forwards GitHub events to trailpad.ai for user activity tracking and analytics.
"""

import os
from typing import Dict, Any, Optional

from src.plugins.base_plugin import BasePlugin, PluginMetadata, PluginType
from src.plugins.event_hooks import event_hooks, HookPriority
from .trailpad_client import TrailpadClient
from src.utils.logger import logger


class TrailpadPlugin(BasePlugin):
    """
    Trailpad Integration Plugin for forwarding events to trailpad.ai.
    
    This external plugin subscribes to all GitHub events and forwards them
    to trailpad.ai for user activity tracking and analytics.
    """
    
    _plugin_name = "trailpad"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the Trailpad plugin."""
        super().__init__(config)
        self.trailpad_client: Optional[TrailpadClient] = None
    
    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata."""
        return PluginMetadata(
            name="trailpad",
            version="1.0.0",
            description="Forward GitHub events to trailpad.ai for activity tracking",
            author="Trailpad Team",
            plugin_type=PluginType.NOTIFICATION,
            dependencies=[],
            config_schema={
                "type": "object",
                "properties": {
                    "webhook_url": {
                        "type": "string",
                        "description": "Trailpad webhook endpoint URL",
                        "format": "uri"
                    },
                    "webhook_secret": {
                        "type": "string",
                        "description": "Secret for webhook signature verification"
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "Enable/disable trailpad integration",
                        "default": True
                    },
                    "track_oauth_only": {
                        "type": "boolean",
                        "description": "Only track OAuth events (user activity), not GitHub App events",
                        "default": False
                    }
                },
                "required": ["webhook_url"]
            },
            enabled=True,
            priority=75  # Lower priority so it runs after core processing
        )
    
    def validate_config(self, config: Dict[str, Any]) -> bool:
        """
        Validate plugin configuration.
        
        Args:
            config: Configuration to validate
            
        Returns:
            True if configuration is valid
            
        Raises:
            ValueError: If configuration is invalid
        """
        # Check for required webhook_url
        webhook_url = config.get("webhook_url") or os.getenv("TRAILPAD_WEBHOOK_URL")
        if not webhook_url:
            raise ValueError("webhook_url is required for trailpad integration")
        
        # Validate URL format
        if not webhook_url.startswith(('http://', 'https://')):
            raise ValueError("webhook_url must be a valid HTTP/HTTPS URL")
        
        return True
    
    async def _initialize(self) -> None:
        """Initialize the plugin."""
        logger.info("Initializing Trailpad plugin")
        
        # Get configuration from config or environment variables
        webhook_url = self.get_config("webhook_url") or os.getenv("TRAILPAD_WEBHOOK_URL")
        webhook_secret = self.get_config("webhook_secret") or os.getenv("TRAILPAD_WEBHOOK_SECRET")
        
        if not webhook_url:
            raise ValueError("Trailpad webhook_url is required")
        
        # Initialize Trailpad client
        self.trailpad_client = TrailpadClient(
            webhook_url=webhook_url,
            webhook_secret=webhook_secret
        )
        
        # Subscribe to all GitHub events
        github_events = [
            # Pull request events
            "pull_request.opened",
            "pull_request.closed", 
            "pull_request.synchronize",
            "pull_request.reopened",
            "pull_request.ready_for_review",
            "pull_request.converted_to_draft",
            
            # Push events
            "push",
            
            # Issue events
            "issues.opened",
            "issues.closed",
            "issues.reopened",
            
            # Release events
            "release.published",
            "release.created",
            
            # Repository events
            "repository.created",
            "repository.deleted",
            "repository.archived",
            
            # Star/watch events
            "star",
            "watch",
            "fork",
            
            # SourceAnt events
            "sourceant.review_completed",
            "sourceant.review_failed"
        ]
        
        event_hooks.subscribe_to_events(
            plugin_name=self.metadata.name,
            callback=self._handle_event,
            event_types=github_events
        )
        
        logger.info(f"Trailpad plugin subscribed to {len(github_events)} event types")
    
    async def _start(self) -> None:
        """Start the plugin."""
        logger.info("Starting Trailpad plugin")
        
        # Test webhook connectivity if enabled
        if self.get_config("enabled", True) and self.trailpad_client:
            try:
                success = await self.trailpad_client.send_health_check()
                if success:
                    logger.info("Trailpad webhook connection verified")
                else:
                    logger.warning("Trailpad webhook connection test failed")
            except Exception as e:
                logger.warning(f"Could not verify trailpad connection: {e}")
        
        logger.info("Trailpad plugin started successfully")
    
    async def _stop(self) -> None:
        """Stop the plugin."""
        logger.info("Stopping Trailpad plugin")
        # No background tasks to stop
        logger.info("Trailpad plugin stopped")
    
    async def _cleanup(self) -> None:
        """Cleanup plugin resources."""
        logger.info("Cleaning up Trailpad plugin")
        
        # Clear references
        self.trailpad_client = None
        
        logger.info("Trailpad plugin cleanup completed")
    
    async def _handle_event(self, event_type: str, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle GitHub events and forward to trailpad.ai.
        
        Args:
            event_type: Type of event (e.g., "pull_request.opened")
            event_data: Event data from broadcaster
            
        Returns:
            Processing result dictionary
        """
        try:
            if not self.get_config("enabled", True) or not self.trailpad_client:
                return {'forwarded': False, 'reason': 'Plugin disabled or not configured'}
            
            # Check if we should only track OAuth events
            track_oauth_only = self.get_config("track_oauth_only", False)
            auth_type = event_data.get('auth_type', 'github_app')
            
            if track_oauth_only and auth_type != 'oauth':
                logger.debug(f"Skipping {event_type} from {auth_type} - only tracking OAuth events")
                return {'forwarded': False, 'reason': 'Only tracking OAuth events'}
            
            # Extract context data
            user_context = event_data.get('user_context', {})
            repository_context = event_data.get('repository_context', {})
            repository_event = event_data.get('repository_event', {})
            activity_data = event_data.get('activity_data')
            original_payload = event_data.get('payload', {})
            
            # Prepare payload for trailpad
            trailpad_payload = {
                'event_type': event_type,
                'auth_type': auth_type,
                'timestamp': event_data.get('timestamp'),
                'user': {
                    'github_id': user_context.get('github_id'),
                    'username': user_context.get('username'),
                    'avatar_url': user_context.get('avatar_url'),
                    'type': user_context.get('type')
                },
                'repository': {
                    'github_repo_id': repository_context.get('github_repo_id'),
                    'full_name': repository_context.get('full_name'),
                    'owner': repository_context.get('owner'),
                    'name': repository_context.get('name'),
                    'private': repository_context.get('private')
                },
                'event_details': repository_event,
                'source': 'sourceant'
            }
            
            # Add OAuth-specific activity data if available
            if activity_data:
                trailpad_payload['activity'] = activity_data
            
            # Add event-specific data
            if event_type.startswith('pull_request'):
                pr_data = original_payload.get('pull_request', {})
                trailpad_payload['pull_request'] = {
                    'number': pr_data.get('number'),
                    'title': pr_data.get('title'),
                    'state': pr_data.get('state'),
                    'draft': pr_data.get('draft'),
                    'merged': pr_data.get('merged'),
                    'base_ref': pr_data.get('base', {}).get('ref'),
                    'head_ref': pr_data.get('head', {}).get('ref'),
                    'additions': pr_data.get('additions'),
                    'deletions': pr_data.get('deletions'),
                    'changed_files': pr_data.get('changed_files')
                }
            elif event_type.startswith('sourceant.review'):
                # Add review-specific data
                review_result = event_data.get('review_result', {})
                trailpad_payload['review'] = {
                    'status': review_result.get('status'),
                    'suggestions_count': review_result.get('suggestions_count', 0),
                    'verdict': review_result.get('verdict'),
                    'total_tokens': review_result.get('total_tokens')
                }
            
            # Forward to trailpad
            success = await self.trailpad_client.send_event(trailpad_payload)
            
            if success:
                logger.debug(f"Successfully forwarded {event_type} to trailpad.ai")
                return {'forwarded': True, 'status': 'success', 'event_type': event_type}
            else:
                logger.warning(f"Failed to forward {event_type} to trailpad.ai")
                return {'forwarded': False, 'reason': 'HTTP request failed'}
            
        except Exception as e:
            logger.error(f"Error forwarding {event_type} to trailpad.ai: {e}")
            return {'forwarded': False, 'reason': f'Error: {str(e)}'}
    
    def get_trailpad_client(self) -> Optional[TrailpadClient]:
        """Get the Trailpad client instance."""
        return self.trailpad_client