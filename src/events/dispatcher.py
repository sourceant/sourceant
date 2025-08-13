import tempfile
from contextvars import ContextVar
from pathlib import Path
from typing import List, Optional, Dict, Any

import redis
from redislite import Redis as RedisLite

from fastapi import BackgroundTasks
from rq import Queue

from src.config.settings import (
    QUEUE_MODE,
    REDIS_HOST,
    REDIS_PORT,
    REVIEW_DRAFT_PRS,
    APP_ENV,
)
from src.events.event import Event
from src.events.repository_event import RepositoryEvent
from src.integrations.github.github import GitHub
from src.integrations.github.github_oauth_client import GitHubOAuth
from src.plugins.event_hooks import event_hooks
from src.llms.llm_factory import llm
from src.models.code_review import CodeReview, Verdict
from src.models.pull_request import PullRequest
from src.models.repository import Repository
from src.models.repository_event import RepositoryEvent as RepositoryEventModel

from src.utils.diff_parser import parse_diff, ParsedDiff
from src.utils.line_mapper import LineMapper

from src.utils.logger import logger

# Context variable to hold the BackgroundTasks object for the current request
bg_tasks_cv: ContextVar[Optional[BackgroundTasks]] = ContextVar(
    "bg_tasks", default=None
)

q = None
if QUEUE_MODE == "redis":
    logger.info("Using Redis for event queue.")
    redis_conn = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
    q = Queue(connection=redis_conn)
elif QUEUE_MODE == "redislite":
    logger.info("Using RedisLite for event queue.")
    # RedisLite uses a file-based Redis instance, no host/port needed.
    redis_conn = RedisLite()
    q = Queue(connection=redis_conn)
elif QUEUE_MODE == "request":
    logger.info("Using request-scoped background tasks for event processing.")
else:
    # This case should ideally not be reached if QUEUE_MODE is validated on startup,
    # but as a safeguard:
    logger.info("No queue mode configured. Events will not be dispatched.")


class EventDispatcher:
    """Dispatches events to the queue."""

    def dispatch(self, event: Event):
        """Dispatches an event to the configured queue or background task runner."""
        if not isinstance(event, RepositoryEvent):
            logger.info(f"Skipping non-repository event: {event}")
            return

        logger.info(f"Dispatching event: {event} (mode: {QUEUE_MODE})")
        if QUEUE_MODE in ["redis", "redislite"]:
            if not q:
                raise RuntimeError(f"{QUEUE_MODE} queue not initialized.")
            q.enqueue(self._process_event_sync, event)

        elif QUEUE_MODE == "request":
            background_tasks = bg_tasks_cv.get()
            if not background_tasks:
                raise RuntimeError(
                    "FastAPI BackgroundTasks not found in context. Is the endpoint setting it?"
                )
            background_tasks.add_task(self._process_event_sync, event)
        else:
            raise ValueError(
                f"Unknown QUEUE_MODE: '{QUEUE_MODE}'. Must be 'redis', 'redislite', or 'request'."
            )

    async def _process_event(self, event: Event):
        """
        Process and broadcast events to all subscribers.
        
        This is now a pure event broadcaster - no review logic here.
        """
        if not isinstance(event, RepositoryEvent):
            logger.error(f"Unhandled event type: {event}")
            return

        repository_event: RepositoryEventModel = event.data
        logger.info(
            f"Broadcasting repository event: {repository_event.type} on {repository_event.repository_full_name}"
        )

        # Broadcast the event to all subscribers (plugins, code reviewer, etc.)
        await self._broadcast_event_to_subscribers(repository_event)
    
    async def _broadcast_event_to_subscribers(self, repository_event: RepositoryEventModel):
        """
        Broadcast repository event to all subscribers.
        
        Args:
            repository_event: Repository event model instance
        """
        try:
            # Determine event type for broadcasting
            if repository_event.action:
                event_type = f"{repository_event.type}.{repository_event.action}"
            else:
                event_type = repository_event.type
            
            # Get auth type and create appropriate client for context extraction
            auth_type = repository_event.payload.get("sourceant_auth_type", "github_app")
            
            if auth_type == "oauth":
                oauth_client = GitHubOAuth()
                user_context = oauth_client.get_user_info_from_webhook(repository_event.payload)
                repository_context = oauth_client.get_repository_info_from_webhook(repository_event.payload)
                activity_data = oauth_client.extract_activity_data(repository_event.payload, repository_event.type)
            else:
                # For GitHub App events, extract basic context
                user_context = self._extract_user_context_github_app(repository_event.payload)
                repository_context = self._extract_repository_context_github_app(repository_event.payload)
                activity_data = None
            
            # Prepare event data for broadcasting
            event_data = {
                'event_type': event_type,
                'auth_type': auth_type,
                'user_context': user_context,
                'repository_context': repository_context,
                'repository_event': {
                    'type': repository_event.type,
                    'action': repository_event.action,
                    'number': repository_event.number,
                    'title': repository_event.title,
                    'url': repository_event.url,
                    'repository_full_name': repository_event.repository_full_name,
                    'provider': repository_event.provider
                },
                'payload': repository_event.payload,
                'activity_data': activity_data,  # OAuth-specific activity data
                'timestamp': repository_event.created_at.isoformat() if repository_event.created_at else None
            }
            
            # Broadcast to all subscribers
            logger.info(f"Broadcasting {event_type} event from {auth_type} to all subscribers")
            
            broadcast_results = await event_hooks.broadcast_event(
                event_type=event_type,
                event_data=event_data,
                source_plugin='sourceant_core'
            )
            
            logger.debug(f"Event broadcast results: {list(broadcast_results.keys())}")
            
        except Exception as e:
            logger.error(f"Error broadcasting event to subscribers: {e}", exc_info=True)
    
    def _extract_user_context_github_app(self, payload: Dict) -> Optional[Dict[str, Any]]:
        """Extract user context from GitHub App webhook payload."""
        try:
            user_info = None
            
            if 'sender' in payload:
                user_info = payload['sender']
            elif 'pull_request' in payload and 'user' in payload['pull_request']:
                user_info = payload['pull_request']['user']
            
            if user_info:
                return {
                    'github_id': user_info.get('id'),
                    'username': user_info.get('login'),
                    'avatar_url': user_info.get('avatar_url'),
                    'type': user_info.get('type')
                }
            
            return None
        except Exception as e:
            logger.error(f"Error extracting user context from GitHub App payload: {e}")
            return None
    
    def _extract_repository_context_github_app(self, payload: Dict) -> Optional[Dict[str, Any]]:
        """Extract repository context from GitHub App webhook payload."""
        try:
            repo_info = payload.get('repository')
            if not repo_info:
                return None
            
            return {
                'github_repo_id': repo_info.get('id'),
                'full_name': repo_info.get('full_name'),
                'name': repo_info.get('name'),
                'owner': repo_info.get('owner', {}).get('login'),
                'private': repo_info.get('private', False)
            }
        except Exception as e:
            logger.error(f"Error extracting repository context from GitHub App payload: {e}")
            return None
    
    def _process_event_sync(self, event: Event):
        """Synchronous wrapper for async event processing."""
        import asyncio
        try:
            # Create new event loop if none exists
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        if loop.is_running():
            # If loop is already running, schedule the coroutine
            asyncio.create_task(self._process_event(event))
        else:
            # Run the coroutine in the loop
            loop.run_until_complete(self._process_event(event))
