from contextvars import ContextVar

running_preview = ContextVar("running_review_preview", default=False)
review_run_key = ContextVar("review_run_key", default="")

import asyncio
import json
from uuid import uuid4

from src.core.cache import cache, Owner
from src.core.jobs import INTERACTIVE, JobOutcome, JobRequest, job_store
from src.models.pull_request import PullRequest
from src.models.repository import Repository
from src.utils.review_cache import save_review

KIND = "review.preview"
RESULTS = "review.preview-result"


def key_for(workspace, repo, number, revision, post=False):
    from src.core.cache import keyed

    return "preview:" + keyed(str(workspace), repo, str(number), revision, str(post))


def request_for(
    repo,
    pull_request,
    metadata,
    workspace,
    user,
    post=False,
    *,
    run_key="",
    event_type=None,
    completion_event=None,
    revision=None,
):
    effective_revision = pull_request.head_sha if revision is None else revision
    key = key_for(workspace, repo, pull_request.number, effective_revision, post)
    return JobRequest(
        lane=INTERACTIVE,
        kind=KIND,
        payload={
            "repo": repo,
            "pull_request": pull_request.model_dump(mode="json"),
            "metadata": metadata,
            "workspace": workspace,
            "user": user,
            "post": post,
            "request_id": run_key or uuid4().hex,
            "event_type": event_type,
            "completion_event": completion_event,
            "revision": effective_revision,
        },
        deadline_seconds=1800,
        max_attempts=1000,
        exclusive_key=key,
        dedupe_slot=key,
        coalesce_running=True,
    ).for_(workspace)


class ReviewPreviews:
    kind = KIND

    def __init__(self, plugin=None):
        self.plugin = plugin

    def run(self, job):
        from src.core.plugins.plugin_registry import plugin_registry

        plugin = self.plugin or plugin_registry.get_plugin("code_reviewer")
        if plugin is None:
            return JobOutcome.failed("Reviewer is not available")
        payload = job.payload
        repo = payload["repo"]
        owner, _, name = repo.partition("/")
        repository = Repository(name=name, owner=owner)
        pull_request = PullRequest(**payload["pull_request"])
        waiting_delivery = cache().get("review.preview-delivery", str(job.id))
        if waiting_delivery:
            result = {"status": "pending", "posting_job_id": int(waiting_delivery)}
        else:
            preview_token = running_preview.set(job.id)
            run_token = review_run_key.set(payload["request_id"])
            try:
                result = asyncio.run(
                    plugin.generate_review(
                        repository,
                        pull_request,
                        pr_metadata=payload["metadata"],
                        event_type=payload.get("event_type"),
                        repository_full_name=repo,
                        post=payload["post"],
                        workspace=payload["workspace"],
                        user=payload["user"],
                        revision=payload.get("revision", pull_request.head_sha) or "",
                        completion_event=payload.get("completion_event"),
                    )
                )
            finally:
                running_preview.reset(preview_token)
                review_run_key.reset(run_token)
        if result.get("continuation"):
            current = job_store().read(job.id)
            if current is None or current.state != "running":
                return JobOutcome.failed("Review was stopped")
            return JobOutcome.failed("Review is continuing", retry=True, retry_in=15)
        if result.get("status") == "pending" and result.get("posting_job_id"):
            cache().set(
                "review.preview-delivery",
                str(job.id),
                str(result["posting_job_id"]),
                ttl=86400,
                scope=Owner("workspace", payload["workspace"]),
            )
            posting = job_store().read(result["posting_job_id"])
            if posting is not None and posting.state in ("queued", "running"):
                current = job_store().read(job.id)
                if current is None or current.state != "running":
                    job_store().cancel(posting.id)
                    return JobOutcome.failed("Review was stopped")
                return JobOutcome.failed(
                    "Review delivery is queued", retry=True, retry_in=15
                )
            if posting is None or posting.state != "succeeded":
                return JobOutcome.failed(
                    posting.error if posting else "Review delivery was lost"
                )
            delivered = cache().get("review.post-result", str(posting.id))
            if delivered is None:
                return JobOutcome.failed("The delivered review result is unavailable")
            result = json.loads(delivered)
        if result.get("status") != "success":
            return JobOutcome.failed(result.get("message") or "Review failed")
        current = job_store().read(job.id)
        if current is None or current.state != "running":
            return JobOutcome.failed("Review was stopped")
        cache().set(
            RESULTS,
            str(job.id),
            json.dumps(result),
            ttl=86400,
            scope=Owner("workspace", payload["workspace"]),
        )
        save_review(
            repo,
            pull_request.number,
            pull_request.head_sha,
            result,
            workspace=payload["workspace"],
        )
        return JobOutcome.ok()
