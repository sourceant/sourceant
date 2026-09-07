from contextvars import ContextVar
from typing import Optional, Dict, Any

import redis
from redislite import Redis as RedisLite

from fastapi import BackgroundTasks
from rq import Queue

from src.config.settings import (
    QUEUE_MODE,
    VALID_QUEUE_MODES,
    REDIS_HOST,
    REDIS_PORT,
)

from src.core.jobs import enqueue
from src.events.delivery import DELIVERY_TIMEOUT, delivery_of
from src.events.event import Event
from src.events.repository_event import RepositoryEvent
from src.integrations.github.github_webhook_parser import GitHubWebhookParser
from src.core.plugins import event_hooks
from src.models.repository_event import RepositoryEvent as RepositoryEventModel

from src.utils.logger import logger

bg_tasks_cv: ContextVar[Optional[BackgroundTasks]] = ContextVar(
    "bg_tasks", default=None
)

# Redis is built wherever it is configured, not only where deliveries use it.
# Work that has not moved to the jobs table is still asked for through this
# queue.
q = None
if QUEUE_MODE in ("redis", "database"):
    logger.info("Using Redis for event queue.")
    redis_conn = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
    q = Queue(connection=redis_conn)
elif QUEUE_MODE == "redislite":
    logger.info("Using RedisLite for event queue.")
    redis_conn = RedisLite()
    q = Queue(connection=redis_conn)
elif QUEUE_MODE == "request":
    logger.info("Using request-scoped background tasks for event processing.")
else:
    logger.info("No queue mode configured. Events will not be dispatched.")


class EventDispatcher:

    def dispatch(self, event: Event):
        if not isinstance(event, RepositoryEvent):
            logger.info(f"Skipping non-repository event: {event}")
            return

        logger.info(f"Dispatching event: {event} (mode: {QUEUE_MODE})")
        if QUEUE_MODE == "database":
            if event.data.id is None:
                # A job names a delivery by its row, and nothing wrote one, so
                # there is nothing for a worker to be given.
                logger.info("This delivery was not written down, so it is done here")
                self._without_waiting(event)
                return
            job_id = enqueue(delivery_of(event.data))
            logger.info(f"Delivery queued as job {job_id}")

        elif QUEUE_MODE in ["redis", "redislite"]:
            if not q:
                raise RuntimeError(f"{QUEUE_MODE} queue not initialized.")
            # Unstated, this takes the queue's default of 180 seconds and the
            # job is stopped partway through with nothing recorded.
            q.enqueue(self._process_event_sync, event, job_timeout=DELIVERY_TIMEOUT)

        elif QUEUE_MODE == "request":
            background_tasks = bg_tasks_cv.get()
            if not background_tasks:
                raise RuntimeError(
                    "FastAPI BackgroundTasks not found in context. Is the endpoint setting it?"
                )
            background_tasks.add_task(self._process_event_sync, event)
        else:
            raise ValueError(
                f"Unknown QUEUE_MODE: '{QUEUE_MODE}'. Must be one of "
                f"{', '.join(VALID_QUEUE_MODES)}."
            )

    def deliver(self, event: Event) -> Dict[str, Any]:
        """Do what a delivery asks for, here, in the caller's process.

        Answers with what each subscriber said, so that a delivery nobody could
        act on is not mistaken for one that was acted on.
        """
        return self._process_event_sync(event) or {}

    def _without_waiting(self, event: Event) -> None:
        """Do the delivery after this request, or in it where that is all there is."""
        background_tasks = bg_tasks_cv.get()
        if background_tasks:
            background_tasks.add_task(self._process_event_sync, event)
            return
        self.deliver(event)

    async def _process_event(self, event: Event) -> Dict[str, Any]:
        if not isinstance(event, RepositoryEvent):
            logger.error(f"Unhandled event type: {event}")
            return {}

        repository_event: RepositoryEventModel = event.data
        logger.info(
            f"Broadcasting repository event: {repository_event.type} on {repository_event.repository_full_name}"
        )

        return await self._broadcast_event_to_subscribers(repository_event)

    async def _broadcast_event_to_subscribers(
        self, repository_event: RepositoryEventModel
    ) -> Dict[str, Any]:
        try:
            if repository_event.action:
                event_type = f"{repository_event.type}.{repository_event.action}"
            else:
                event_type = repository_event.type

            auth_type = repository_event.payload.get(
                "sourceant_auth_type", "github_app"
            )

            if auth_type == "oauth":
                webhook_parser = GitHubWebhookParser()
                user_context = webhook_parser.get_user_info_from_webhook(
                    repository_event.payload
                )
                repository_context = webhook_parser.get_repository_info_from_webhook(
                    repository_event.payload
                )
                activity_data = webhook_parser.extract_activity_data(
                    repository_event.payload, repository_event.type
                )
            else:
                user_context = self._extract_user_context_github_app(
                    repository_event.payload
                )
                repository_context = self._extract_repository_context_github_app(
                    repository_event.payload
                )
                activity_data = None

            event_data = {
                "event_type": event_type,
                "auth_type": auth_type,
                "user_context": user_context,
                "repository_context": repository_context,
                "repository_event": {
                    "type": repository_event.type,
                    "action": repository_event.action,
                    "number": repository_event.number,
                    "title": repository_event.title,
                    "url": repository_event.url,
                    "repository_full_name": repository_event.repository_full_name,
                    "provider": repository_event.provider,
                },
                "payload": repository_event.payload,
                "activity_data": activity_data,
                "timestamp": (
                    repository_event.created_at.isoformat()
                    if repository_event.created_at
                    else None
                ),
            }

            logger.info(
                f"Broadcasting {event_type} event from {auth_type} to all subscribers"
            )

            broadcast_results = await event_hooks.broadcast_event(
                event_type=event_type,
                event_data=event_data,
                source_plugin="sourceant_core",
            )

            logger.debug(f"Event broadcast results: {list(broadcast_results.keys())}")
            return broadcast_results

        except Exception as e:
            logger.error(f"Error broadcasting event to subscribers: {e}", exc_info=True)
            return {"sourceant_core": {"error": str(e)}}

    def _extract_user_context_github_app(
        self, payload: Dict
    ) -> Optional[Dict[str, Any]]:
        try:
            user_info = None

            if "sender" in payload:
                user_info = payload["sender"]
            elif "pull_request" in payload and "user" in payload["pull_request"]:
                user_info = payload["pull_request"]["user"]

            if user_info:
                return {
                    "github_id": user_info.get("id"),
                    "username": user_info.get("login"),
                    "avatar_url": user_info.get("avatar_url"),
                    "type": user_info.get("type"),
                }

            return None
        except Exception as e:
            logger.error(f"Error extracting user context from GitHub App payload: {e}")
            return None

    def _extract_repository_context_github_app(
        self, payload: Dict
    ) -> Optional[Dict[str, Any]]:
        try:
            repo_info = payload.get("repository")
            if not repo_info:
                return None

            return {
                "github_repo_id": repo_info.get("id"),
                "full_name": repo_info.get("full_name"),
                "name": repo_info.get("name"),
                "owner": repo_info.get("owner", {}).get("login"),
                "private": repo_info.get("private", False),
            }
        except Exception as e:
            logger.error(
                f"Error extracting repository context from GitHub App payload: {e}"
            )
            return None

    async def _ensure_plugins_loaded(self):
        """Bootstrap the plugin system when running outside FastAPI (e.g. RQ worker)."""
        if event_hooks._event_subscribers:
            return

        from pathlib import Path
        from src.core.plugins import plugin_manager
        from src.llms.llm_factory import llm
        from src.utils.logger import setup_logger

        setup_logger()
        llm()

        plugins_dir = Path(__file__).parent.parent / "plugins"
        plugin_manager.add_plugin_directory(plugins_dir)
        await plugin_manager.load_all_plugins()
        await plugin_manager.initialize_plugins()
        await plugin_manager.start_plugins()

    def _process_event_sync(self, event: Event) -> Optional[Dict[str, Any]]:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            asyncio.create_task(self._process_event(event))
            return None
        loop.run_until_complete(self._ensure_plugins_loaded())
        return loop.run_until_complete(self._process_event(event))
