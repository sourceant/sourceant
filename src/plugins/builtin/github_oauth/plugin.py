"""
GitHub OAuth Plugin for SourceAnt.

Provides user authentication via GitHub OAuth and repository access management.
"""

import os
from typing import Dict, Any, Optional

from fastapi import FastAPI

from src.core.plugins import BasePlugin, PluginMetadata, PluginType
from src.core.plugins import event_hooks, HookPriority
from .oauth_handler import GitHubOAuthHandler
from .routes import GitHubOAuthRoutes, router
from src.utils.logger import logger


class GitHubOAuthPlugin(BasePlugin):
    """
    GitHub OAuth Plugin for user authentication and repository management.

    This plugin extends SourceAnt to support user-based authentication via
    GitHub OAuth, allowing users to authorize SourceAnt to access their
    repositories without needing to create a GitHub App.
    """

    _plugin_name = "github_oauth"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the GitHub OAuth plugin."""
        super().__init__(config)
        self.oauth_handler: Optional[GitHubOAuthHandler] = None
        self.routes_handler: Optional[GitHubOAuthRoutes] = None
        self._app: Optional[FastAPI] = None

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata."""
        return PluginMetadata(
            name="github_oauth",
            version="1.0.0",
            description="GitHub OAuth authentication and repository management",
            author="SourceAnt Team",
            plugin_type=PluginType.AUTHENTICATION,
            dependencies=[],
            config_schema={
                "type": "object",
                "properties": {
                    "client_id": {
                        "type": "string",
                        "description": "GitHub OAuth App Client ID",
                    },
                    "client_secret": {
                        "type": "string",
                        "description": "GitHub OAuth App Client Secret",
                    },
                    "redirect_uri": {
                        "type": "string",
                        "description": "OAuth callback URI",
                        "default": "http://localhost:8000/auth/github/callback",
                    },
                },
                "required": ["client_id", "client_secret"],
            },
            enabled=True,
            priority=50,
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
        required_fields = ["client_id", "client_secret"]

        for field in required_fields:
            if field not in config:
                # Try to get from environment variables
                env_var = f"GITHUB_OAUTH_{field.upper()}"
                if env_var not in os.environ:
                    raise ValueError(f"Missing required configuration: {field}")

        return True

    async def _initialize(self) -> None:
        """Initialize the plugin."""
        logger.info("Initializing GitHub OAuth plugin")

        # Get configuration from config or environment variables
        client_id = self.get_config("client_id") or os.getenv("GITHUB_OAUTH_CLIENT_ID")
        client_secret = self.get_config("client_secret") or os.getenv(
            "GITHUB_OAUTH_CLIENT_SECRET"
        )
        redirect_uri = self.get_config(
            "redirect_uri", "http://localhost:8000/auth/github/callback"
        )

        if not client_id or not client_secret:
            raise ValueError("GitHub OAuth client_id and client_secret are required")

        # Initialize OAuth handler
        self.oauth_handler = GitHubOAuthHandler(
            client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri
        )

        # Initialize routes handler
        self.routes_handler = GitHubOAuthRoutes(self.oauth_handler)

        # Register event hooks
        await self._register_hooks()

        logger.info("GitHub OAuth plugin initialized successfully")

    async def _start(self) -> None:
        """Start the plugin."""
        logger.info("Starting GitHub OAuth plugin")

        # Register routes with FastAPI app
        await self._register_routes()

        logger.info("GitHub OAuth plugin started successfully")

    async def _stop(self) -> None:
        """Stop the plugin."""
        logger.info("Stopping GitHub OAuth plugin")

        # Unregister routes if needed
        # Note: FastAPI doesn't have a built-in way to unregister routes at runtime

        logger.info("GitHub OAuth plugin stopped")

    async def _cleanup(self) -> None:
        """Cleanup plugin resources."""
        logger.info("Cleaning up GitHub OAuth plugin")

        # Clear references
        self.oauth_handler = None
        self.routes_handler = None
        self._app = None

        logger.info("GitHub OAuth plugin cleanup completed")

    async def _register_hooks(self) -> None:
        """Register event hooks."""
        # Register hook for before webhook processing to check user authorization
        event_hooks.register_hook(
            hook_name="before_webhook_processing",
            callback=self._before_webhook_processing,
            plugin_name=self.metadata.name,
            priority=HookPriority.HIGH,
        )

        # Register hook for user authentication events
        event_hooks.register_hook(
            hook_name="after_user_authentication",
            callback=self._after_user_authentication,
            plugin_name=self.metadata.name,
            priority=HookPriority.NORMAL,
        )

        logger.info("Registered GitHub OAuth plugin hooks")

    async def _register_routes(self) -> None:
        """Register routes with the FastAPI application."""
        try:
            # Get the FastAPI app instance
            # In a real implementation, this would be injected or obtained from
            # the plugin manager or application context
            from src.api.main import app

            self._app = app

            # Include the OAuth router
            app.include_router(router)

            logger.info("Registered GitHub OAuth routes")

        except Exception as e:
            logger.error(f"Failed to register routes: {e}")
            raise

    async def _before_webhook_processing(
        self, context: Dict[str, Any], **kwargs
    ) -> Dict[str, Any]:
        """
        Hook called before webhook processing.

        Validates that the repository is authorized by a user.
        """
        try:
            repository_event = context.get("repository_event")
            if not repository_event:
                return {"authorized": False, "reason": "No repository event"}

            repository_full_name = repository_event.repository_full_name

            # Check if any user has authorized this repository
            from src.config.db import get_db
            from .models import UserRepository

            db = next(get_db())
            try:
                authorized_repo = (
                    db.query(UserRepository)
                    .filter(
                        UserRepository.full_name == repository_full_name,
                        UserRepository.enabled == True,
                        UserRepository.webhook_configured == True,
                    )
                    .first()
                )

                if authorized_repo:
                    logger.info(
                        f"Repository {repository_full_name} is authorized by user"
                    )
                    return {
                        "authorized": True,
                        "user_id": authorized_repo.user_id,
                        "repository_id": authorized_repo.id,
                    }
                else:
                    logger.info(f"Repository {repository_full_name} is not authorized")
                    return {
                        "authorized": False,
                        "reason": "Repository not authorized by any user",
                    }

            finally:
                db.close()

        except Exception as e:
            logger.error(f"Error in before_webhook_processing hook: {e}")
            return {"authorized": False, "reason": f"Hook error: {str(e)}"}

    async def _after_user_authentication(
        self, context: Dict[str, Any], **kwargs
    ) -> Dict[str, Any]:
        """
        Hook called after user authentication.

        Performs post-authentication tasks like syncing repositories.
        """
        try:
            user = context.get("user")
            if not user:
                return {"success": False, "reason": "No user in context"}

            # Get fresh access token and sync repositories
            access_token = await self.oauth_handler.get_valid_token(user)
            if access_token:
                repositories = await self.oauth_handler.get_user_repositories(
                    access_token
                )
                if repositories:
                    count = await self.oauth_handler.update_user_repositories(
                        user, repositories
                    )
                    logger.info(f"Synced {count} repositories for user {user.username}")
                    return {"success": True, "synced_repos": count}

            return {"success": True, "synced_repos": 0}

        except Exception as e:
            logger.error(f"Error in after_user_authentication hook: {e}")
            return {"success": False, "reason": f"Hook error: {str(e)}"}

    def get_oauth_handler(self) -> Optional[GitHubOAuthHandler]:
        """Get the OAuth handler instance."""
        return self.oauth_handler

    def get_routes_handler(self) -> Optional[GitHubOAuthRoutes]:
        """Get the routes handler instance."""
        return self.routes_handler
