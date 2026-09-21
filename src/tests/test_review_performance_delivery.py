import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Lock
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from litellm import ModelResponse
import requests
from sqlalchemy import select, update
from sqlmodel import create_engine

from src.api.routes import pr, settings
from src.auth import get_current_user
from src.core.analysis import Analysis
from src.core.analysis.checkout import written_out
from src.core.jobs import INTERACTIVE, JobHandler, SQLJobStore, Worker
from src.core.jobs.sql import job_table
from src.core.review.exclusions import excluded, review_diff
from src.core.parallel import SharedReader, parallel_map
from src.core.services import ServiceRegistry
from src.events.delivery import Deliveries
from src.events.review_posting import ReviewPosting
from src.integrations.github.review_delivery import comment_for, retry_after
from src.integrations.github.github import GitHub
from src.models.repository import Repository
from src.models.pull_request import PullRequest
from src.models.config import Config
from src.models.repository_event import RepositoryEvent
from src.models.review_record import ReviewRecord
from src.models.code_review import (
    CodeReview,
    CodeSuggestion,
    Side,
    SuggestionCategory,
    Verdict,
)
from src.plugins.builtin.code_reviewer.plugin import CodeReviewerPlugin
from src.utils.diff_parser import parse_diff
from src.utils.line_mapper import LineMapper


FIXTURES = Path(__file__).parent / "fixtures"


class ReviewPerformanceTests(unittest.TestCase):
    def test_patterns_match_nested_lockfiles_and_can_be_cleared(self):
        from src.core.settings.review_defaults import DEFAULT_EXCLUSIONS

        self.assertTrue(excluded("apps/mobile/package-lock.json", DEFAULT_EXCLUSIONS))
        self.assertTrue(excluded("pnpm-lock.yaml", DEFAULT_EXCLUSIONS))
        self.assertFalse(excluded("package.json", DEFAULT_EXCLUSIONS))
        self.assertFalse(excluded("yarn.lock", ""))
        self.assertTrue(excluded("vendor/generated.py", "vendor/*"))
        self.assertTrue(excluded("generated.py", "**/generated.py"))
        self.assertFalse(excluded("src/generated.py", "vendor/*"))

    def test_filter_preserves_the_remaining_captured_diff(self):
        diff = (FIXTURES / "review-overview/full.diff").read_text()
        configuration = Mock()
        configuration.value.return_value = "src/migrations/*"
        filtered, omitted = review_diff(diff, configuration)
        self.assertIn("src/migrations/versions/create_requirement_tables.py", omitted)
        self.assertNotIn("create_requirement_tables.py", filtered)
        self.assertIn("suggest_groups", filtered)
        configuration.value.return_value = ""
        self.assertEqual(
            len(parse_diff(review_diff(diff, configuration)[0])), len(parse_diff(diff))
        )

    def test_file_fetches_overlap_and_repeated_reads_are_shared(self):
        barrier = Barrier(2)
        calls = []

        def read(path):
            calls.append(path)
            barrier.wait(timeout=5)
            return path

        reader = SharedReader(read)
        with TemporaryDirectory() as root:
            with written_out(["first.py", "second.py"], reader) as (directory, paths):
                self.assertEqual(len(paths), 2)
                self.assertEqual((directory / "first.py").read_text(), "first.py")
            self.assertEqual(parallel_map(reader, ["first.py"] * 8), ["first.py"] * 8)
        self.assertEqual(sorted(calls), ["first.py", "second.py"])

    def test_actual_github_failures_are_retryable(self):
        for captured in json.loads(
            (FIXTURES / "github/review-posting-errors.json").read_text()
        ):
            response = requests.Response()
            response.status_code = int(captured["status"])
            response._content = json.dumps(captured).encode()
            self.assertEqual(retry_after(response), 60)

    def test_invalid_range_is_not_moved_to_a_nearby_line(self):
        mapper = LineMapper(
            parse_diff((FIXTURES / "review-overview/full.diff").read_text())
        )
        finding = CodeSuggestion(
            file_name="src/core/topology/suggestions.py",
            start_line=9999,
            end_line=10000,
            side=Side.RIGHT,
            category=SuggestionCategory.BUG,
            comment="A finding",
            suggested_code=None,
        )
        self.assertIsNone(comment_for(finding, mapper))
        self.assertEqual(finding.start_line, 9999)


class WebhookDeliveryTests(unittest.TestCase):
    def test_webhook_exclusions_and_posting_retry_do_not_regenerate(self):
        for experts, count in (("auto", 2), ("contracts", 1), ("", 0)):
            with self.subTest(experts=experts):
                self._webhook_delivery(experts, count)

    def _webhook_delivery(self, experts, expert_count):
        with ExitStack() as stack:
            folder = stack.enter_context(TemporaryDirectory())
            engine = create_engine(
                f"sqlite:///{folder}/test.db", connect_args={"check_same_thread": False}
            )
            stack.callback(engine.dispose)
            for model in (Config, RepositoryEvent, ReviewRecord):
                model.__table__.create(engine)
            store = SQLJobStore(engine, create_schema=True)
            replacements = {
                "src.config.db.engine": engine,
                "src.core.jobs._core": store,
                "src.controllers.repository_event_controller.STATELESS_MODE": False,
                "src.utils.review_record_service.STATELESS_MODE": False,
                "src.events.dispatcher.QUEUE_MODE": "database",
                "src.config.settings.QUEUE_MODE": "database",
                "src.core.plugins.event_hooks._event_subscribers": {},
                "src.api.routes.pr.GITHUB_SECRET": "",
                "src.events.delivery.tenant_for": lambda repo: ("6", "workspace"),
                "src.core.usage.record_completion": lambda *a, **k: None,
                "src.plugins.builtin.code_reviewer.plugin.examine": lambda *a: Analysis(),
            }
            for name, value in replacements.items():
                stack.enter_context(patch(name, value))
            from src.core.settings.resolver import value_of

            stack.enter_context(
                patch(
                    "src.core.settings.value_of",
                    side_effect=lambda key, **scopes: (
                        0
                        if key == "review.reuse_responses_days"
                        else value_of(key, **scopes)
                    ),
                )
            )
            services = ServiceRegistry()
            plugin = CodeReviewerPlugin()
            plugin.services = services
            asyncio.run(plugin._initialize())
            github = Mock()
            github.get_diff.return_value = (
                FIXTURES / "review-overview/full.diff"
            ).read_text()
            github.get_existing_bot_review_comments.return_value = []
            github.get_previous_review_summary.return_value = None
            github.get_file_content.return_value = None
            github.has_existing_bot_approval.return_value = False
            stack.enter_context(
                patch(
                    "src.plugins.builtin.code_reviewer.plugin.GitHub",
                    return_value=github,
                )
            )
            stack.enter_context(
                patch("src.events.review_posting.GitHub", return_value=github)
            )
            captures = {
                name: ModelResponse(
                    **json.loads((FIXTURES / f"deepseek/{name}.json").read_text())
                )
                for name in ("review", "overview")
            }
            from src.core.skills import SkillLibrary
            from src.core.skills.models import Skill

            library = Mock()
            library.all.return_value = tuple(
                Skill(
                    id=name,
                    name=name,
                    description="Review Python",
                    body=f"Check {name}.",
                    paths=("src/core/topology/**",),
                    metadata={"sourceant": {"type": "review-pass", "review": True}},
                )
                for name in ("contracts", "duplication")
            )
            services.register(SkillLibrary, library, "test")
            barrier = Barrier(2 + expert_count)
            prompts = []

            def answer(**kwargs):
                prompts.append(json.dumps(kwargs["messages"]))
                barrier.wait(timeout=10)
                return captures[
                    (
                        "review"
                        if '"CodeReviewFindings"' in kwargs["messages"][-1]["content"]
                        else "overview"
                    )
                ]

            completion = stack.enter_context(
                patch("litellm.completion", side_effect=answer)
            )
            app = FastAPI()
            app.include_router(pr.router, prefix="/api/prs")
            app.include_router(settings.router, prefix="/api/settings")
            app.dependency_overrides[pr.get_gateway_scope] = lambda: {
                "workspace_id": "6",
                "owner_id": "1",
            }
            app.dependency_overrides[get_current_user] = lambda: {
                "user_id": "1",
                "scope": {"workspace_id": "6"},
            }
            client = stack.enter_context(TestClient(app))
            for key, value in [
                ("model.name", "deepseek/deepseek-v4-flash"),
                ("model.api_key", "your-api-key-here"),
                ("review.exclude_patterns", "src/migrations/*"),
                ("review.expert_passes", experts),
            ]:
                response = client.put(
                    f"/api/settings/user/1/{key}", json={"value": value}
                )
                self.assertEqual(response.status_code, 200, response.text)
            payload = json.loads((FIXTURES / "deepseek/webhook.json").read_text())
            payload["pull_request"].update(
                json.loads((FIXTURES / "review-overview/pull.json").read_text())
            )
            response = client.post(
                "/api/prs/github-webhook",
                json=payload,
                headers={"X-GitHub-Event": "pull_request"},
            )
            self.assertEqual(response.status_code, 201, response.text)
            services.contribute(JobHandler, Deliveries(services), "test")
            services.contribute(JobHandler, ReviewPosting(), "test")
            worker = Worker(store, INTERACTIVE, services=services)
            self.assertEqual(worker.work(max_jobs=1, max_time=15), 1)
            self.assertEqual(completion.call_count, 2 + expert_count)
            self.assertTrue(
                all("create_requirement_tables.py" not in prompt for prompt in prompts)
            )
            specialist_prompts = [
                prompt
                for prompt in prompts
                if "Check contracts." in prompt or "Check duplication." in prompt
            ]
            self.assertEqual(len(specialist_prompts), expert_count)
            self.assertTrue(
                all(
                    "src/core/topology/suggestions.py" in prompt
                    for prompt in specialist_prompts
                )
            )
            github.post_review.side_effect = [
                {
                    "status": "pending",
                    "message": "GitHub temporarily blocked posting",
                    "retry_after": 60,
                },
                {"status": "partial_success", "message": "Review findings delivered"},
            ]
            self.assertEqual(worker.work(max_jobs=1, max_time=15), 1)
            with engine.begin() as connection:
                posting = (
                    connection.execute(
                        select(job_table).where(job_table.c.kind == "review.post")
                    )
                    .mappings()
                    .one()
                )
                self.assertEqual(posting["state"], "queued")
                self.assertEqual(connection.execute(select(ReviewRecord)).all(), [])
                connection.execute(
                    update(job_table)
                    .where(job_table.c.id == posting["id"])
                    .values(available_at=posting["created_at"])
                )
            self.assertEqual(worker.work(max_jobs=1, max_time=15), 1)
            self.assertEqual(completion.call_count, 2 + expert_count)
            self.assertTrue(github.post_review.call_args.kwargs["force_fallback"])
            with engine.connect() as connection:
                self.assertEqual(len(connection.execute(select(ReviewRecord)).all()), 1)
                self.assertEqual(
                    connection.execute(
                        select(job_table.c.state).where(job_table.c.id == posting["id"])
                    ).scalar(),
                    "succeeded",
                )


class FindingsDeliveryTests(unittest.TestCase):
    def test_rejected_review_resumes_as_new_findings_comment_once(self):
        captured = json.loads(
            (FIXTURES / "github/review-posting-errors.json").read_text()
        )[0]
        comment_response = json.loads(
            (FIXTURES / "github/review-comment.json").read_text()
        )
        diff = (FIXTURES / "review-overview/full.diff").read_text()
        mapper = LineMapper(parse_diff(diff))
        parsed = mapper.parsed_files[0]
        line, side = sorted(parsed.commentable_lines)[0]
        finding = CodeSuggestion(
            file_name=parsed.file_path,
            start_line=line,
            end_line=line,
            side=Side(side),
            category=SuggestionCategory.BUG,
            comment="The returned collection must remain iterable.",
            suggested_code=None,
        )
        review = CodeReview(verdict=Verdict.COMMENT, code_suggestions=[finding])
        repository = Repository(owner="sourceant", name="sourceant")
        pr_model = PullRequest(number=1, head_sha="head", base_sha="base")
        posted = []

        def response(status, body):
            result = requests.Response()
            result.status_code = status
            result._content = json.dumps(body).encode()
            return result

        def get(url, **kwargs):
            return response(200, posted if "/issues/" in url else [])

        def post(url, **kwargs):
            if url.endswith("/reviews"):
                return response(int(captured["status"]), captured)
            created = {**comment_response, "body": kwargs["json"]["body"]}
            posted.append(created)
            return response(201, created)

        with patch.dict(
            "os.environ",
            {
                "GITHUB_APP_ID": "test-app",
                "GITHUB_APP_PRIVATE_KEY_PATH": "unused-test-key.pem",
                "GITHUB_APP_CLIENT_ID": "test-client",
            },
        ):
            github = GitHub()
        with (
            patch.object(
                github,
                "get_installation_access_token",
                return_value="your-api-key-here",
            ),
            patch("requests.get", side_effect=get),
            patch("requests.post", side_effect=post) as send,
        ):
            first = github.post_review(
                repository, pr_model, review, mapper, delivery_id="delivery-one"
            )
            self.assertEqual(first["status"], "pending")
            self.assertEqual(posted, [])
            second = github.post_review(
                repository,
                pr_model,
                review,
                mapper,
                delivery_id="delivery-one",
                force_fallback=True,
            )
            self.assertEqual(second["status"], "partial_success")
            self.assertEqual(len(posted), 1)
            self.assertIn(finding.file_name, posted[0]["body"])
            self.assertIn(finding.comment, posted[0]["body"])
            self.assertIn(f"#L{line}-L{line}", posted[0]["body"])
            github.post_review(
                repository,
                pr_model,
                review,
                mapper,
                delivery_id="delivery-one",
                force_fallback=True,
            )
            self.assertEqual(len(posted), 1)
            github.post_review(
                repository,
                pr_model,
                review,
                mapper,
                delivery_id="delivery-two",
                force_fallback=True,
            )
            self.assertEqual(len(posted), 2)
            self.assertEqual(
                sum(call.args[0].endswith("/reviews") for call in send.call_args_list),
                1,
            )


if __name__ == "__main__":
    unittest.main()
