from dataclasses import asdict
import uuid

from src.core.jobs import INTERACTIVE, JobOutcome, JobRequest, enqueue
from src.core.review.stopping import abandoned
from src.core.settings.configuration import Configuration
from src.integrations.github.github import GitHub
from src.models.code_review import CodeReview
from src.models.pull_request import PullRequest
from src.models.repository import Repository
from src.utils.diff_parser import parse_diff
from src.utils.line_mapper import LineMapper
from src.utils.review_record_service import save_review_record, get_last_reviewed_sha

KIND = "review.post"


def queue_review(
    repository,
    pull_request,
    review,
    diff,
    configuration,
    services,
    completion_event=None,
):
    repository.full_name = f"{repository.owner}/{repository.name}"
    return enqueue(
        JobRequest(
            lane=INTERACTIVE,
            kind=KIND,
            payload={
                "repository": repository.model_dump(mode="json"),
                "pull_request": pull_request.model_dump(mode="json"),
                "review": review.model_dump(mode="json"),
                "diff": diff,
                "configuration": asdict(configuration),
                "delivery_id": uuid.uuid4().hex,
                "completion_event": completion_event,
            },
            deadline_seconds=300,
            max_attempts=8,
            exclusive_key=f"review.post:{repository.full_name}:{pull_request.number}",
        ).for_(
            configuration.workspace or repository.full_name,
            "workspace" if configuration.workspace else "repository",
        ),
        services=services,
    )


class ReviewPosting:
    kind = KIND

    def run(self, job):
        payload = job.payload
        repository = Repository(**payload["repository"])
        pr = PullRequest(**payload["pull_request"])
        configuration = Configuration(**payload["configuration"])
        revision = pr.head_sha if configuration.value("review.stop_on_new_push") else ""
        if abandoned(repository.full_name, pr.number, revision):
            return JobOutcome.ok()
        review = CodeReview.model_validate(payload["review"])
        result = GitHub().post_review(
            repository,
            pr,
            review,
            LineMapper(parse_diff(payload["diff"])),
            configuration=configuration,
            delivery_id=payload["delivery_id"],
        )
        if result["status"] == "pending":
            return JobOutcome.failed(
                result["message"],
                retry=True,
                retry_in=max(result["retry_after"], 60 * 2 ** (job.attempt - 1)),
            )
        if result["status"] == "error":
            return JobOutcome.failed(result["message"])
        if get_last_reviewed_sha(repository.full_name, pr.number) != pr.head_sha:
            try:
                save_review_record(
                    repository.full_name,
                    pr.number,
                    pr.head_sha,
                    pr.base_sha,
                    strict=True,
                )
            except Exception as error:
                return JobOutcome.failed(str(error), retry=True, retry_in=60)
        if payload.get("completion_event"):
            import asyncio
            from src.core.plugins import event_hooks

            asyncio.run(
                event_hooks.broadcast_event(
                    event_type="sourceant.review_completed",
                    event_data={
                        **payload["completion_event"],
                        "review_result": {
                            "status": "success",
                            "review_posted": result,
                            "review": payload["review"],
                            "suggestions_count": len(review.code_suggestions or ()),
                            "verdict": review.verdict.value,
                        },
                    },
                    source_plugin="code_reviewer",
                )
            )
        return JobOutcome.ok()
