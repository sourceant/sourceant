import asyncio
import json
import time
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


def request_for(repo, pull_request, metadata, workspace, user, post=False):
    key = key_for(workspace, repo, pull_request.number, pull_request.head_sha, post)
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
            "request_id": uuid4().hex,
        },
        deadline_seconds=1800,
        max_attempts=1,
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
        result = asyncio.run(
            plugin.generate_review(
                repository,
                pull_request,
                pr_metadata=payload["metadata"],
                event_type=None,
                repository_full_name=repo,
                post=payload["post"],
                workspace=payload["workspace"],
                user=payload["user"],
                revision=pull_request.head_sha or "",
            )
        )
        if result.get("status") == "pending" and result.get("posting_job_id"):
            cache().set(
                "review.preview-delivery",
                str(job.id),
                str(result["posting_job_id"]),
                ttl=86400,
                scope=Owner("workspace", payload["workspace"]),
            )
            posting = job_store().read(result["posting_job_id"])
            while posting is not None and posting.state in ("queued", "running"):
                current = job_store().read(job.id)
                if current is None or current.state != "running":
                    job_store().cancel(posting.id)
                    return JobOutcome.failed("Review was stopped")
                time.sleep(0.25)
                posting = job_store().read(posting.id)
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
