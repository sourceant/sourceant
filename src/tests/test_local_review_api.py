import json
import subprocess

import pytest
from sqlalchemy import create_engine

from src.api.main import app
from src.api.routes.code import get_code_index
from src.api.routes.knowledge import get_knowledge
from src.api.routes.local_reviews import get_reviews, get_working_tree_reviewer
from src.core.review import Reviewer
from src.core.services import ServiceRegistry
from src.plugins.builtin.code_reviewer.reviewing import CodeReviewer
from src.plugins.builtin.code_reviewer import working_tree
from src.plugins.builtin.code_reviewer.working_tree import WorkingTreeReviews
from src.plugins.builtin.local.folders import RegisteredFolders
from src.plugins.builtin.local.skills import SkillsOnDisk
from src.core.code_index import SQLCodeIndexRepository
from src.core.knowledge import SQLKnowledgeRepository
from src.core.review import SQLReviewStore
from src.llms.llm_interface import LLMInterface
from src.models.code_review import (
    CodeReview,
    CodeReviewSummary,
    CodeSuggestion,
    Side,
    SuggestionCategory,
    Verdict,
)
from src.tests.base_test import BaseTestCase

MIGRATIONS_SKILL = """---
name: migrations
description: Use when a change adds or edits a database migration or a schema.
---

Never edit a migration that has already run.
"""


class FakeModel(LLMInterface):
    """A model that answers whatever the test told it to.

    Two questions get asked of it: the review proper, which is the same
    reviewer a pull request gets, and one question per skill.
    """

    def __init__(self, answer, review=None):
        self.answer = answer
        self.review = review if review is not None else _review()
        self.asked = []
        self.asked_purposes = []
        self.told = []
        self.model = "a-model"

    @property
    def token_limit(self):
        return 1_000_000

    def count_tokens(self, text):
        return len(text)

    def generate_text(self, prompt, *, purpose=None):
        self.asked.append(prompt)
        self.asked_purposes.append(purpose)
        if purpose == "review-evaluation":
            return json.dumps(
                {
                    "judgments": [
                        {
                            "candidate": 0,
                            "status": "supported",
                            "reason": "The migration changes an existing applied operation.",
                            "evidence": ["db/0001_charges.py: return 1"],
                        }
                    ]
                }
            )
        return json.dumps(self.answer)

    def generate_code_review(self, **called):
        self.told.append(called)
        return self.review

    def generate_summary(self, suggestions, **kwargs):
        return self.review.summary

    def is_summary_different(self, summary_a, summary_b):
        return summary_a != summary_b


def _review(verdict=Verdict.COMMENT, suggestions=()):
    return CodeReview(
        verdict=verdict,
        summary=CodeReviewSummary(
            overview="It edits a migration that has already run.",
            key_improvements=["Add a new migration"],
            minor_suggestions=[],
            critical_issues=["A migration that already ran was edited"],
        ),
        code_suggestions=list(suggestions),
    )


class TestLocalReview(BaseTestCase):
    """Drives /api/local/reviews the way the local UI does."""

    @pytest.fixture(autouse=True)
    def machine(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path / "home"))
        # An empty home, or this reads whichever skills the person running the
        # tests keeps in their own agent folders.
        (tmp_path / "nobody").mkdir()
        monkeypatch.setenv("SOURCEANT_MACHINE_HOME", str(tmp_path / "nobody"))
        monkeypatch.setattr("src.api.routes.code.LOCAL_MODE", True)

        self.source = tmp_path / "billing"
        (self.source / "db").mkdir(parents=True)
        (self.source / "db" / "0001_charges.py").write_text(
            "def up():\n    pass\n", encoding="utf-8"
        )
        skills = self.source / ".sourceant" / "skills" / "migrations"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text(MIGRATIONS_SKILL, encoding="utf-8")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "nobody@example.com")
        self.git("config", "user.name", "Nobody")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "First")

        engine = create_engine(f"sqlite:///{tmp_path / 'store.db'}")
        services = ServiceRegistry()
        self.services = services
        app.dependency_overrides[get_code_index] = lambda: SQLCodeIndexRepository(
            engine, create_schema=True
        )
        app.dependency_overrides[get_knowledge] = lambda: SQLKnowledgeRepository(
            engine, create_schema=True
        )
        app.dependency_overrides[get_reviews] = lambda: SQLReviewStore(
            engine, create_schema=True
        )
        # The plugin that reads a checkout, pointed at this test's store. It
        # resolves the reviewer itself, so registering one is what decides
        # whether these reviews are judged at all.
        folders = RegisteredFolders()
        app.dependency_overrides[get_working_tree_reviewer] = (
            lambda: WorkingTreeReviews(
                repositories=folders,
                skills=SkillsOnDisk(folders),
                knowledge=SQLKnowledgeRepository(engine, create_schema=True),
                services=services,
            )
        )
        services.register(Reviewer, CodeReviewer(services=services), "test")
        yield
        for key in (
            "review.plan",
            "review.discovery_passes",
            "review.evaluation_passes",
            "review.finding_scope",
        ):
            self.client.delete(f"/api/local/settings/{key}")
        app.dependency_overrides.clear()

    def git(self, *arguments):
        subprocess.run(
            ["git", *arguments], cwd=self.source, check=True, capture_output=True
        )

    def register(self, name="acme/billing"):
        return self.client.post(
            "/api/code/repositories", json={"path": str(self.source), "name": name}
        )

    def edit_the_migration(self):
        (self.source / "db" / "0001_charges.py").write_text(
            "def up():\n    return 1\n", encoding="utf-8"
        )

    def start(self, **body):
        return self.client.post(
            "/api/local/reviews", json={"repository": "acme/billing", **body}
        )

    def review(self, **body):
        """Ask for one, then read what came of it.

        Asking answers with a name and the reading happens behind it, so a
        test that wants the answer has to come back for it, exactly as a
        screen or an agent does.
        """
        started = self.start(**body)
        if started.status_code != 200:
            return started
        return self.client.get(f"/api/local/reviews/{started.json()['data']['id']}")

    @pytest.mark.parametrize("status", ["supported", "rejected", "unresolved"])
    def test_premium_static_findings_require_evaluation_over_http(
        self, monkeypatch, status
    ):
        from src.core.analysis import Analyzer, AnalyzerFinding, ERROR

        class Linter:
            name = "lint"

            def available(self):
                return True

            def examine(self, root, paths):
                return (
                    AnalyzerFinding(
                        "db/0001_charges.py",
                        2,
                        2,
                        "test-rule",
                        "Potential failure",
                        ERROR,
                        "lint",
                    ),
                )

        class Model(FakeModel):
            def generate_text(self, prompt, *, purpose=None):
                if purpose != "review-evaluation":
                    return super().generate_text(prompt, purpose=purpose)
                payload = json.loads(prompt.split("\nReview input:\n", 1)[1])
                assert (
                    payload["source"]["db/0001_charges.py"]
                    == "def up():\n    return 1\n"
                )
                assert len(payload["candidates"]) == 1
                return json.dumps(
                    {
                        "judgments": [
                            {
                                "candidate": 0,
                                "status": status,
                                "reason": "Inspect the migration return value",
                                "evidence": (
                                    ["return 1"] if status != "unresolved" else []
                                ),
                            }
                        ]
                    }
                )

        self.services.contribute(Analyzer, Linter(), "lint")
        model = Model({"findings": []}, review=_review(Verdict.APPROVE, []))
        monkeypatch.setattr(working_tree, "provider_for", lambda *_, **__: model)
        self.register()
        for key, value in {
            "review.plan": "premium",
            "review.discovery_passes": 1,
            "review.evaluation_passes": 1,
        }.items():
            response = self.client.put(
                f"/api/local/settings/{key}", json={"value": value}
            )
            assert response.status_code == 200
        self.edit_the_migration()
        response = self.review()
        assert response.status_code == 200
        review = response.json()["data"]["review"]
        execution = review["review"]["execution"]
        assert len(execution["candidates"]) == 1
        assert execution["producers"] == [["analysis:lint"]]
        assert execution["evaluations"][1]["judgments"][0]["status"] == status
        assert len(execution["analysis_findings"]) == (
            1 if status == "supported" else 0
        )
        assert bool(review["review"]["summary"]["critical_issues"]) == (
            status == "supported"
        )
        assert (review["review"]["verdict"] == "REQUEST_CHANGES") == (
            status == "supported"
        )

    @pytest.mark.parametrize("status", ["supported", "rejected", "unresolved"])
    def test_doubtful_findings_are_published_only_after_evaluation_over_http(
        self, monkeypatch, status
    ):
        sure = CodeSuggestion(
            file_name="db/0001_charges.py",
            start_line=2,
            end_line=2,
            side=Side.RIGHT,
            category=SuggestionCategory.BUG,
            comment="Add a new migration instead of editing one that already ran.",
            existing_code="    return 1\n",
            suggested_code="    pass\n",
        )
        doubted = sure.model_copy(
            update={
                "comment": "Return None here; a runner can treat 1 as a failure code.",
                "suggested_code": "    return None\n",
                "doubt": "The runner may ignore the return value of up().",
            }
        )
        evaluated = []

        class Model(FakeModel):
            def generate_text(self, prompt, *, purpose=None):
                if purpose != "review-evaluation":
                    return super().generate_text(prompt, purpose=purpose)
                evaluated.append(prompt)
                payload = json.loads(prompt.split("\nReview input:\n", 1)[1])
                return json.dumps(
                    {
                        "judgments": [
                            {
                                "candidate": one["candidate"],
                                "status": (
                                    status if "doubt" in one["finding"] else "supported"
                                ),
                                "reason": "Inspect the migration return value",
                                "evidence": (
                                    ["return 1"]
                                    if "doubt" not in one["finding"]
                                    or status != "unresolved"
                                    else []
                                ),
                            }
                            for one in payload["candidates"]
                        ]
                    }
                )

        model = Model(
            {"findings": []}, review=_review(Verdict.COMMENT, [sure, doubted])
        )
        monkeypatch.setattr(working_tree, "provider_for", lambda *_, **__: model)
        self.register()
        for key, value in {
            "review.plan": "premium",
            "review.discovery_passes": 1,
            "review.evaluation_passes": 1,
            "review.evaluate_uncertain_findings": True,
        }.items():
            response = self.client.put(
                f"/api/local/settings/{key}", json={"value": value}
            )
            assert response.status_code == 200, response.text
        try:
            self.edit_the_migration()
            response = self.review()
        finally:
            self.client.delete("/api/local/settings/review.evaluate_uncertain_findings")
        assert response.status_code == 200, response.text
        published = [
            one["comment"]
            for one in response.json()["data"]["review"]["review"]["suggestions"]
        ]
        assert "Doubtful concerns" in (model.told[0]["knowledge"] or "")
        payload = json.loads(evaluated[0].split("\nReview input:\n", 1)[1])
        findings = {
            one["finding"]["comment"]: one["finding"] for one in payload["candidates"]
        }
        assert findings[doubted.comment]["doubt"] == doubted.doubt
        assert "doubt" not in findings[sure.comment]
        assert "neither support nor rejection, and it is never evidence" in evaluated[0]
        assert sorted(published) == sorted(
            [sure.comment, doubted.comment] if status == "supported" else [sure.comment]
        )

    @pytest.mark.parametrize(
        "premises,published,verdict",
        [
            (("anyone", "everyone", "crash", "always"), True, "REQUEST_CHANGES"),
            (("operator", "one", "crash", "conditional"), True, "COMMENT"),
            (("anyone", "everyone", "crash", "possible"), False, "COMMENT"),
            (None, False, "COMMENT"),
        ],
    )
    def test_supported_findings_are_ranked_by_verified_premises_over_http(
        self, monkeypatch, premises, published, verdict
    ):
        stated = CodeSuggestion(
            file_name="db/0001_charges.py",
            start_line=2,
            end_line=2,
            side=Side.RIGHT,
            category=SuggestionCategory.BUG,
            comment="Add a new migration instead of editing one that already ran.",
            existing_code="    return 1\n",
            suggested_code="    pass\n",
            trigger="anyone",
            blast="everyone",
            impact="crash",
            certainty="always",
        )
        evaluated = []

        class Model(FakeModel):
            def generate_text(self, prompt, *, purpose=None):
                if purpose != "review-evaluation":
                    return super().generate_text(prompt, purpose=purpose)
                evaluated.append(prompt)
                payload = json.loads(prompt.split("\nReview input:\n", 1)[1])
                return json.dumps(
                    {
                        "judgments": [
                            {
                                "candidate": one["candidate"],
                                "status": "supported",
                                "reason": "The edited migration already ran",
                                "evidence": ["return 1"],
                                **(
                                    {
                                        "premises": dict(
                                            zip(
                                                (
                                                    "trigger",
                                                    "blast",
                                                    "impact",
                                                    "certainty",
                                                ),
                                                premises,
                                            )
                                        )
                                    }
                                    if premises
                                    else {}
                                ),
                            }
                            for one in payload["candidates"]
                        ]
                    }
                )

        model = Model({"findings": []}, review=_review(Verdict.COMMENT, [stated]))
        monkeypatch.setattr(working_tree, "provider_for", lambda *_, **__: model)
        self.register()
        settings = {
            "review.plan": "premium",
            "review.discovery_passes": 1,
            "review.evaluation_passes": 1,
            "review.evaluation_policy": "verify-claims",
            "review.include_nitpicks": False,
        }
        for key, value in settings.items():
            response = self.client.put(
                f"/api/local/settings/{key}", json={"value": value}
            )
            assert response.status_code == 200, response.text
        try:
            self.edit_the_migration()
            response = self.review()
        finally:
            for key in settings:
                self.client.delete(f"/api/local/settings/{key}")
        assert response.status_code == 200, response.text
        review = response.json()["data"]["review"]["review"]
        assert [one["comment"] for one in review["suggestions"]] == (
            [stated.comment] if published else []
        )
        assert review["verdict"] == verdict
        judgment = review["execution"]["evaluations"][1]["judgments"][0]
        assert judgment["premises"] == (
            dict(zip(("trigger", "blast", "impact", "certainty"), premises))
            if premises
            else None
        )
        assert judgment["status"] == ("supported" if premises else "unresolved")
        assert "Judge each finding by what it claims" in evaluated[0]
        assert "Security claims need a concrete exploitable flow" not in evaluated[0]
        assert '"premises"' in evaluated[0].split("\nReview input:\n", 1)[0]

    @pytest.mark.parametrize("remedy", [False, True])
    def test_fix_assumptions_do_not_block_an_established_defect_over_http(
        self, monkeypatch, remedy
    ):
        from pathlib import Path

        captured = json.loads(
            (
                Path(__file__).parent
                / "fixtures/review-contracts/node-remedy-assumption-judgments.json"
            ).read_text()
        )
        stated = CodeSuggestion(
            file_name="db/0001_charges.py",
            start_line=2,
            end_line=2,
            side=Side.RIGHT,
            category=SuggestionCategory.BUG,
            comment="Add a new migration instead of editing one that already ran.",
            existing_code="    return 1\n",
            suggested_code="    pass\n",
        )
        evaluated = []

        class Model(FakeModel):
            def generate_text(self, prompt, *, purpose=None):
                if purpose != "review-evaluation":
                    return super().generate_text(prompt, purpose=purpose)
                judgment = dict(captured["judgments"][len(evaluated)])
                evaluated.append(prompt)
                if remedy:
                    judgment["remedy_assumptions"] = judgment.pop(
                        "unverified_assumptions"
                    )
                payload = json.loads(prompt.split("\nReview input:\n", 1)[1])
                return json.dumps(
                    {
                        "judgments": [
                            {**judgment, "candidate": one["candidate"]}
                            for one in payload["candidates"]
                        ]
                    }
                )

        model = Model({"findings": []}, review=_review(Verdict.COMMENT, [stated]))
        monkeypatch.setattr(working_tree, "provider_for", lambda *_, **__: model)
        self.register()
        settings = {
            "review.plan": "premium",
            "review.discovery_passes": 1,
            "review.evaluation_passes": 2,
            "review.evaluation_policy": "verify-claims",
            "review.include_nitpicks": True,
        }
        for key, value in settings.items():
            response = self.client.put(
                f"/api/local/settings/{key}", json={"value": value}
            )
            assert response.status_code == 200, response.text
        try:
            self.edit_the_migration()
            response = self.review()
        finally:
            for key in settings:
                self.client.delete(f"/api/local/settings/{key}")
        assert response.status_code == 200, response.text
        review = response.json()["data"]["review"]["review"]
        assert [one["comment"] for one in review["suggestions"]] == (
            [stated.comment] if remedy else []
        )
        judgments = [
            evaluation["judgments"][0]
            for evaluation in review["execution"]["evaluations"][1:]
        ]
        assert [one["status"] for one in judgments] == (
            ["supported"] * 2 if remedy else ["unresolved"] * 2
        )
        assert all(
            ("Fix assumptions: " in one["reason"]) is remedy for one in judgments
        )
        schema = evaluated[0].split("\nReview input:\n", 1)[0]
        assert '"remedy_assumptions"' in schema
        assert "an uncertain fix does not make an established defect uncertain" in (
            schema
        )

    @pytest.mark.parametrize(
        "strategy,budget,limit,digest",
        [
            ("independent", 15000, None, False),
            ("investigations", 15000, None, False),
            ("investigations", 2000, None, False),
            ("investigations", 2000, None, True),
            ("investigations", 2000, 2, False),
        ],
    )
    def test_snapshot_http_supplies_captured_finding_dependency(
        self, monkeypatch, strategy, budget, limit, digest
    ):
        from pathlib import Path
        import time
        import jwt
        from src.core.settings.configuration import Configuration
        from src.core.settings.definitions import get
        from src.core.review import snapshot

        captured = json.loads(
            (
                Path(__file__).parent
                / "fixtures/review-contracts/node-cited-evidence.json"
            ).read_text()
        )
        candidate = captured["candidates"][3]
        expected = captured["snapshot"]["files"]["packages/pg/lib/query.js"]
        checked = []

        class CapturedModel(FakeModel):
            def count_tokens(self, text):
                return len(text) // 4

            def generate_text(self, prompt, *, purpose=None):
                if purpose == "review-evaluation" and budget == 2000:
                    return captured["evaluation_response"]
                if purpose == "review-evaluation":
                    supplied = json.loads(prompt.split("Review input:\n", 1)[1])
                    assert (
                        supplied["source"].get("packages/pg/lib/query.js") == expected
                    ), list(supplied["source"])
                    assert not set(supplied["source"]) & set(
                        supplied["omitted_context"]
                    )
                    checked.append(supplied)
                    return captured["evaluation_response"]
                return super().generate_text(prompt, purpose=purpose)

        model = CapturedModel(
            {"findings": []},
            review=CodeReview(
                verdict=Verdict.COMMENT, code_suggestions=captured["candidates"]
            ),
        )
        monkeypatch.setenv("JWT_SECRET", "test-signing-secret-for-review")
        monkeypatch.setattr(Configuration, "value", lambda self, key: get(key).default)
        monkeypatch.setattr(snapshot, "provider_for", lambda *_, **__: model)
        token = jwt.encode(
            {
                "sub": "reviewer",
                "exp": int(time.time()) + 60,
                "scope": {"workspace_id": "benchmark"},
            },
            "test-signing-secret-for-review",
            algorithm="HS256",
        )
        captured["snapshot"]["configuration"]["split-large-changes"] = (
            "true" if strategy == "investigations" else "false"
        )
        captured["snapshot"]["configuration"]["reading-budget"] = str(budget)
        if budget == 2000:
            captured["snapshot"]["configuration"]["discovery-passes"] = "1"
        if limit:
            captured["snapshot"]["configuration"]["maximum-parts"] = str(limit)
        if digest:
            captured["snapshot"]["configuration"]["include-change-digest"] = "true"
        response = self.client.post(
            "/api/reviews/snapshots",
            json=captured["snapshot"],
            headers={"Authorization": "Bearer " + token},
        )
        if limit:
            assert response.status_code == 422, response.text
            assert "investigations to fit the reading budget" in response.text
            assert not model.told
            return
        if budget == 2000:
            assert response.status_code == 200, response.text
            tasks = response.json()["data"]["review"]["review"]["execution"][
                "investigations"
            ]
            assert len(model.told) == len(tasks) > 1
            for call in model.told:
                files = call["parsed_files"]
                assert (
                    len(files) == 1
                    or sum(len(one.diff_text) // 4 for one in files) <= budget
                )
                own = {one.file_path for one in files}
                assert ("Review change digest" in call["requirements"]) is digest
                assert ("Review change inventory" in call["requirements"]) is not digest
                if digest:
                    directory, current, foreign = "", None, []
                    for row in call["requirements"].splitlines():
                        if row.startswith("packages/") and row.endswith("/"):
                            directory = row
                        elif row.startswith("  ") and not row.startswith("    "):
                            current = directory + row.strip()
                        elif row.startswith("    ") and current not in own:
                            foreign.append(row)
                    assert foreign
            from src.utils.diff_parser import parse_diff
            from src.core.review.exclusions import review_diff
            from src.core.review.snapshot import SnapshotConfiguration

            diff, _ = review_diff(captured["snapshot"]["diff"], SnapshotConfiguration())
            assert sorted(
                target["path"] for task in tasks for target in task["targets"]
            ) == sorted(
                one.file_path for one in parse_diff(diff) if not one.is_binary_file
            )
            return
        assert response.status_code == 200, response.text
        assert len(checked) == 2
        assert any(
            one["finding"]["comment"] == candidate["comment"]
            for one in checked[0]["candidates"]
        )
        if strategy == "investigations":
            execution = response.json()["data"]["review"]["review"]["execution"]
            assert len(model.told) == len(execution["investigations"]) == 3
            assigned = {
                target["path"]
                for task in execution["investigations"]
                for target in task["targets"]
            }
            from src.utils.diff_parser import parse_diff
            from src.core.review.exclusions import review_diff
            from src.core.review.snapshot import SnapshotConfiguration

            diff, _ = review_diff(captured["snapshot"]["diff"], SnapshotConfiguration())
            assert assigned == {
                one.file_path for one in parse_diff(diff) if not one.is_binary_file
            }
            for call in model.told:
                assert "follow the value or operation" in call["knowledge"]
                assert "Investigation head source:" in call["related_code"]
                assert len(call["parsed_files"]) < len(assigned)

    @pytest.mark.parametrize("included", [False, True])
    def test_snapshot_http_supplies_callers_and_callees(self, monkeypatch, included):
        from pathlib import Path
        import time
        import jwt
        from src.core.review import snapshot

        captured = json.loads(
            (
                Path(__file__).parent
                / "fixtures/review-contracts/node-cited-evidence.json"
            ).read_text()
        )
        model = FakeModel(
            {"findings": []},
            review=CodeReview(verdict=Verdict.COMMENT, code_suggestions=[]),
        )
        monkeypatch.setenv("JWT_SECRET", "test-signing-secret-for-review")
        monkeypatch.setattr(snapshot, "provider_for", lambda *_, **__: model)
        token = jwt.encode(
            {
                "sub": "reviewer",
                "exp": int(time.time()) + 60,
                "scope": {"workspace_id": "benchmark"},
            },
            "test-signing-secret-for-review",
            algorithm="HS256",
        )
        captured["snapshot"]["configuration"]["include-callers-and-callees"] = (
            "true" if included else "false"
        )
        response = self.client.post(
            "/api/reviews/snapshots",
            json=captured["snapshot"],
            headers={"Authorization": "Bearer " + token},
        )
        assert response.status_code == 200, response.text
        related = "\n".join(call["related_code"] or "" for call in model.told)
        caller = (
            "packages/pg/lib/diagnostics.js:27 shouldTrace called by connect at "
            "packages/pg/lib/client.js:211 (matched by name; 2 definitions share "
            "the name)"
        )
        callee = (
            "packages/pg-pool/index.js:173 _remove calls shouldTrace at "
            "packages/pg-pool/diagnostics.js:31 (matched by name)"
        )
        assert (caller in related) is included
        assert (callee in related) is included

    @pytest.mark.parametrize("included", [False, True])
    def test_snapshot_http_discovers_documentation_by_format(
        self, monkeypatch, included
    ):
        from pathlib import Path
        import time
        import jwt
        from src.core.review import snapshot

        captured = json.loads(
            (
                Path(__file__).parent
                / "fixtures/review-contracts/node-cited-evidence.json"
            ).read_text()
        )
        document = "docs/pages/announcements.mdx"
        assert document in captured["snapshot"]["files"]
        assert document not in captured["snapshot"]["diff"]
        model = FakeModel(
            {"findings": []},
            review=CodeReview(verdict=Verdict.COMMENT, code_suggestions=[]),
        )
        monkeypatch.setenv("JWT_SECRET", "test-signing-secret-for-review")
        monkeypatch.setattr(snapshot, "provider_for", lambda *_, **__: model)
        token = jwt.encode(
            {
                "sub": "reviewer",
                "exp": int(time.time()) + 60,
                "scope": {"workspace_id": "benchmark"},
            },
            "test-signing-secret-for-review",
            algorithm="HS256",
        )
        captured["snapshot"]["configuration"]["include-documentation-files"] = (
            "true" if included else "false"
        )
        response = self.client.post(
            "/api/reviews/snapshots",
            json=captured["snapshot"],
            headers={"Authorization": "Bearer " + token},
        )
        assert response.status_code == 200, response.text
        assert model.told
        assert all(
            (f"Repository contract source: {document}" in (call["related_code"] or ""))
            is included
            for call in model.told
        )

    @pytest.mark.parametrize(
        "path,source,line",
        [
            (
                "context.py",
                "def element(values, index):\n    if index < 0 or index > len(values):\n        return None\n    return values[index]\n",
                4,
            ),
            (
                "context.ts",
                "export function element(values: number[], index: number): number {\n    if (index < 0 || index > values.length) return 0;\n    return values[index];\n}\n",
                3,
            ),
            (
                "context.go",
                "package context\nfunc element(values []int, index int) int {\n    if index < 0 || index > len(values) { return 0 }\n    return values[index]\n}\n",
                4,
            ),
            (
                "Context.java",
                "class Context {\n    static int element(int[] values, int index) {\n        if (index < 0 || index > values.length) return 0;\n        return values[index];\n    }\n}\n",
                4,
            ),
            (
                "context.rs",
                "fn element(values: &[i32], index: usize) -> Option<i32> {\n    if index > values.len() { return None; }\n    Some(values[index])\n}\n",
                3,
            ),
        ],
    )
    @pytest.mark.parametrize(
        "scope,expected", [("changed-lines", 0), ("repository", 1)]
    )
    def test_off_diff_comment_survives_repository_scope_over_http(
        self, monkeypatch, scope, expected, path, source, line
    ):
        (self.source / path).write_text(source)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "feat: Add context")
        candidate = CodeSuggestion(
            file_name=path,
            start_line=line,
            end_line=line,
            side=Side.RIGHT,
            category=SuggestionCategory.BUG,
            comment="Reject an index equal to the collection length before reading its element.",
            existing_code=source.splitlines()[line - 1],
            suggested_code=None,
            comment_only=True,
        )

        class Model(FakeModel):
            def generate_text(self, prompt, *, purpose=None):
                if purpose != "review-evaluation":
                    return super().generate_text(prompt, purpose=purpose)
                payload = json.loads(prompt.split("\nReview input:\n", 1)[1])
                assert payload["source"][path] == source
                assert payload["candidates"][0]["finding"]["start_line"] == line
                return json.dumps(
                    {
                        "judgments": [
                            {
                                "candidate": 0,
                                "status": "supported",
                                "reason": "The guard allows an index equal to the collection length.",
                                "evidence": [source.splitlines()[line - 1]],
                            }
                        ]
                    }
                )

        model = Model({"findings": []}, review=_review(Verdict.COMMENT, [candidate]))
        monkeypatch.setattr(working_tree, "provider_for", lambda *_, **__: model)
        self.register()
        for key, value in {
            "review.plan": "premium",
            "review.discovery_passes": 1,
            "review.finding_scope": scope,
        }.items():
            assert (
                self.client.put(
                    f"/api/local/settings/{key}", json={"value": value}
                ).status_code
                == 200
            )
        self.edit_the_migration()
        response = self.review()
        assert response.status_code == 200
        review = response.json()["data"]["review"]
        assert len(review["review"]["suggestions"]) == expected
        if expected:
            assert review["review"]["suggestions"][0]["path"] == path
            assert review["review"]["execution"]["candidates"][0]["position"] is None
            assert not review["review"]["execution"]["evaluations"][1]["error"]

    @pytest.mark.parametrize("committed", [False, True])
    def test_premium_http_review_runs_redundancy_and_returns_coverage(
        self, monkeypatch, committed
    ):
        from src.core.analysis import SemgrepAnalyzer
        from src.core.settings.configuration import Configuration

        monkeypatch.setattr(SemgrepAnalyzer, "available", lambda self: False)
        candidate = CodeSuggestion(
            file_name="db/0001_charges.py",
            start_line=2,
            end_line=2,
            side=Side.RIGHT,
            category=SuggestionCategory.BUG,
            comment="Add a new migration instead.",
            existing_code="    return 1\n",
            suggested_code="    pass\n",
        )
        model = FakeModel(
            {"findings": []}, review=_review(Verdict.COMMENT, [candidate])
        )
        monkeypatch.setattr(working_tree, "provider_for", lambda *_, **__: model)
        original = Configuration.value
        configured = {
            "review.plan": "premium",
            "review.discovery_passes": 3,
            "review.evaluation_passes": 2,
            "review.minimum_support": 1,
        }
        monkeypatch.setattr(
            Configuration,
            "value",
            lambda self, key: (
                configured[key] if key in configured else original(self, key)
            ),
        )
        self.register()
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.source, text=True
        ).strip()
        self.edit_the_migration()
        options = {}
        if committed:
            self.git("add", "-A")
            self.git("commit", "-q", "-m", "feat: Change the migration")
            head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=self.source, text=True
            ).strip()
            options = {"against": base, "head": head}
        response = self.review(**options)
        assert response.status_code == 200
        answered = response.json()["data"]["review"]
        execution = answered["review"].get("execution")
        assert execution is not None, response.text
        assert len(execution["reviews"]) == 3
        assert all(not one["error"] for one in execution["reviews"]), execution[
            "reviews"
        ]
        assert len(execution["evaluations"]) == 3
        assert len(model.told) == 3
        evaluations = [
            one
            for one in execution["evaluations"]
            if one["participant"] != "deterministic"
        ]
        assert all(not one["error"] for one in evaluations), evaluations
        assert all(one["judgments"][0]["status"] == "supported" for one in evaluations)
        assert len(answered["review"]["suggestions"]) == 1
        assert "db/0001_charges.py" in execution["unchecked"]
        assert answered["ready"] is False

    def test_requested_head_rejects_a_different_checkout(self):
        self.register()
        response = self.review(against="main", head="a" * 40, use_model=False)
        assert response.json()["data"]["status"] == "failed"
        assert "does not match" in response.json()["data"]["error"]

    @pytest.mark.parametrize("trace_failure", [False, True])
    @pytest.mark.parametrize("discovery_failure", [False, True])
    @pytest.mark.parametrize(
        "strategy,expert_readings,expert_execution",
        [
            ("independent", 0, "passes"),
            ("independent", 1, "passes"),
            ("independent", 0, "guidance"),
            ("investigations", 0, "passes"),
        ],
    )
    def test_premium_snapshot_http_runs_the_same_plan(
        self,
        monkeypatch,
        trace_failure,
        discovery_failure,
        strategy,
        expert_readings,
        expert_execution,
    ):
        import time
        import jwt
        from src.core.settings.configuration import Configuration
        from src.core.settings.definitions import get
        from src.core.review import snapshot
        from functools import partial
        from src.api.routes import review_snapshots
        from src.core.review_trace import ReviewTraceSink, TraceKind
        from src.core.skills.interfaces import SkillSource
        from src.core.skills.models import Skill

        events = []

        class Trace:
            def record(self, event):
                events.append(event)
                if trace_failure:
                    raise RuntimeError("Trace destination unavailable")

        services = snapshot.service_registry.with_service(
            ReviewTraceSink, Trace(), "test-trace"
        )

        class Experts:
            def read(self):
                return tuple(
                    Skill(
                        id=name,
                        name=name,
                        description="Inspect changed behavior.",
                        body="Check the changed migration against existing callers.",
                        metadata={"sourceant": {"type": "review-pass"}},
                        applications={"review": True},
                    )
                    for name in ("contracts", "failures")
                )

        services.contribute(SkillSource, Experts(), "test-experts")
        if discovery_failure:
            original = CodeReviewer._review_once

            def failing_reading(self, changes, **options):
                if any(
                    (
                        "Reading review-0:1."
                        if strategy == "investigations"
                        else "Reading review-0:2."
                    )
                    in one.body
                    for one in options.get("told", ())
                ):
                    raise RuntimeError("Discovery unavailable")
                return original(self, changes, **options)

            monkeypatch.setattr(CodeReviewer, "_review_once", failing_reading)
        monkeypatch.setattr(
            review_snapshots,
            "review_snapshot",
            partial(snapshot.review_snapshot, services=services),
        )

        monkeypatch.setenv("JWT_SECRET", "test-signing-secret-for-review")
        monkeypatch.setattr(Configuration, "value", lambda self, key: get(key).default)
        model = FakeModel(
            {"findings": []},
            review=_review(
                Verdict.COMMENT,
                [
                    CodeSuggestion(
                        file_name="db/0001_charges.py",
                        start_line=2,
                        end_line=2,
                        side=Side.RIGHT,
                        category=SuggestionCategory.BUG,
                        comment="Add a new migration instead.",
                        existing_code="    return 1\n",
                        suggested_code="    pass\n",
                    )
                ],
            ),
        )
        monkeypatch.setattr(snapshot, "provider_for", lambda *_, **__: model)
        token = jwt.encode(
            {
                "sub": "reviewer",
                "exp": int(time.time()) + 60,
                "scope": {"workspace_id": "benchmark"},
            },
            "test-signing-secret-for-review",
            algorithm="HS256",
        )
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.source, text=True
        ).strip()
        self.edit_the_migration()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "feat: Change the migration")
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.source, text=True
        ).strip()
        body = {
            "repository": "acme/billing",
            "base": base,
            "head": head,
            "diff": subprocess.check_output(
                ["git", "diff", base + "..." + head], cwd=self.source, text=True
            ),
            "files": {
                "db/0001_charges.py": (self.source / "db/0001_charges.py").read_text()
            },
            "configuration": {
                "discovery-passes": "3",
                "split-large-changes": (
                    "true" if strategy == "investigations" else "false"
                ),
                "expert-readings": str(expert_readings),
                "expert-execution": expert_execution,
                "evaluation-passes": "2",
                "finding-scope": "repository",
                "reuse-responses-days": "0",
                "max-output-tokens": "16384",
                "reasoning-effort": "low",
            },
        }
        unauthenticated = self.client.post("/api/reviews/snapshots", json=body)
        assert unauthenticated.status_code == 422
        response = self.client.post(
            "/api/reviews/snapshots",
            json=body,
            headers={"Authorization": "Bearer " + token},
        )
        if discovery_failure:
            assert response.status_code == 502, response.text
            assert "review-evaluation" not in model.asked_purposes
            return
        assert response.status_code == 200, response.text
        answer = response.json()["data"]
        assert answer["snapshot"]["head"] == body["head"]
        assert answer["configuration"]["finding-scope"] == "repository"
        assert answer["configuration"]["reuse-responses-days"] == "0"
        assert answer["configuration"]["max-output-tokens"] == "16384"
        assert answer["configuration"]["reasoning-effort"] == "low"
        assert answer["configuration"]["expert-readings"] == str(expert_readings)
        execution = answer["review"]["review"]["execution"]
        assert len(execution["reviews"]) == (1 if strategy == "investigations" else 3)
        assert all(not one["error"] for one in execution["reviews"])
        assert (
            len(
                [
                    one
                    for one in execution["evaluations"]
                    if one["participant"] != "deterministic"
                ]
            )
            == 2
        )
        assert len(answer["review"]["review"]["suggestions"]) == 1
        raw = [
            event
            for event in events
            if event.kind == TraceKind.FINDING and event.action == "raw"
        ]
        expected_calls = (
            1
            if strategy == "investigations"
            else 3 if expert_execution == "guidance" else 5 if expert_readings else 9
        )
        assert len(model.told) == expected_calls
        assert len(raw) == expected_calls
        assert len({event.participant for event in raw}) == (
            1 if strategy == "investigations" else 3
        )
        if strategy == "investigations" or expert_execution == "guidance":
            assert (
                "Check the changed migration against existing callers."
                in model.told[0]["knowledge"]
            )
        assert len({event.session for event in events}) == 1
        assert all(event.finding_id not in response.text for event in raw)
        decided = [event for event in events if event.stage == "acceptance"]
        assert len(decided) == len(execution["candidates"])
        assert len([event for event in decided if event.action == "accepted"]) >= len(
            answer["review"]["review"]["suggestions"]
        )

        body["files"] = {"../escape.py": "pass\n"}
        invalid = self.client.post(
            "/api/reviews/snapshots",
            json=body,
            headers={"Authorization": "Bearer " + token},
        )
        assert invalid.status_code == 422

    @pytest.mark.parametrize(
        "path,guard",
        [
            ("bounds.py", "if index > len(values): return None"),
            ("bounds.ts", "if (index > values.length) return null;"),
            ("bounds.go", "if index > len(values) { return 0, false }"),
        ],
    )
    def test_http_review_keeps_one_character_bounds_fixes(
        self, monkeypatch, path, guard
    ):
        safe = guard.replace(" > ", " >= ")
        prefix, suffix = {
            "bounds.py": ("def item(values, index):\n", "    return values[index]\n"),
            "bounds.ts": (
                "export function item(values: number[], index: number): number | null {\n",
                "    return values[index];\n}\n",
            ),
            "bounds.go": (
                "package fixture\n\nfunc Item(values []int, index int) (int, bool) {\n",
                "    return values[index], true\n}\n",
            ),
        }[path]
        (self.source / path).write_text(prefix + "    " + safe + "\n" + suffix)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "test: Establish bounds guard")
        candidate = CodeSuggestion(
            file_name=path,
            start_line=prefix.count("\n") + 1,
            end_line=prefix.count("\n") + 1,
            side=Side.RIGHT,
            category=SuggestionCategory.BUG,
            comment="The guard permits index equal to the collection length, causing an invalid access. Use >= to reject that index.",
            existing_code=guard,
            suggested_code=safe,
        )

        class BoundsModel(FakeModel):
            def generate_text(self, prompt, *, purpose=None):
                if purpose == "review-evaluation":
                    candidates = json.loads(prompt.split("Review input:\n", 1)[1])[
                        "candidates"
                    ]
                    return json.dumps(
                        {
                            "judgments": [
                                {
                                    "candidate": index,
                                    "status": "supported",
                                    "reason": "Both comments identify the guard allowing an index equal to length.",
                                    "evidence": [guard],
                                    "duplicate_of": 0 if index else None,
                                }
                                for index in range(len(candidates))
                            ]
                        }
                    )
                return super().generate_text(prompt, purpose=purpose)

        repeated = candidate.model_copy(
            update={
                "comment": "The guard fails to reject index equal to length, allowing invalid access. Reject that boundary.",
                "suggested_code": {
                    "bounds.py": "if index > len(values) or index == len(values): return None",
                    "bounds.ts": "if (index > values.length || index === values.length) return null;",
                    "bounds.go": "if index > len(values) || index == len(values) { return 0, false }",
                }[path],
            }
        )
        model = BoundsModel(
            {"findings": []}, review=_review(Verdict.COMMENT, [candidate, repeated])
        )
        monkeypatch.setattr(working_tree, "provider_for", lambda *_, **__: model)
        self.register()
        for key, value in {
            "review.plan": "premium",
            "review.discovery_passes": 1,
        }.items():
            assert (
                self.client.put(
                    f"/api/local/settings/{key}", json={"value": value}
                ).status_code
                == 200
            )
        (self.source / path).write_text(prefix + "    " + guard + "\n" + suffix)
        response = self.review()
        assert response.status_code == 200
        suggestions = response.json()["data"]["review"]["review"]["suggestions"]
        assert len(suggestions) == 1
        assert suggestions[0]["suggested_code"] == safe
        assert response.json()["data"]["review"]["review"]["execution"][
            "finding_groups"
        ] == [[0, 1]]

    def test_http_evaluation_receives_the_source_read_by_discovery(self, monkeypatch):
        caller = "export const locale = request.query.language;\n"
        (self.source / "caller.ts").write_text(caller)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "test: Establish related caller")
        candidate = CodeSuggestion(
            file_name="db/0001_charges.py",
            start_line=2,
            end_line=2,
            side=Side.RIGHT,
            category=SuggestionCategory.BUG,
            comment="Add a new migration instead.",
            existing_code="    return 1\n",
            suggested_code="    pass\n",
        )

        class ContextModel(FakeModel):
            def generate_text(self, prompt, *, purpose=None):
                if purpose == "review-evaluation":
                    supplied = json.loads(prompt.split("Review input:\n", 1)[1])
                    assert supplied["source"]["caller.ts"] == caller
                return super().generate_text(prompt, purpose=purpose)

        model = ContextModel(
            {"findings": []}, review=_review(Verdict.COMMENT, [candidate])
        )
        monkeypatch.setattr(working_tree, "provider_for", lambda *_, **__: model)

        def read_related(self, changes, *, read_content, **options):
            from src.core.search import Searcher, SearchQuery

            found = self.services.resolve(Searcher).search(
                SearchQuery(changes.code_scope, ("locale",))
            )
            assert any(
                one.path == "caller.ts" and caller.strip() in one.text
                for one in found.matches
            )
            assert read_content("caller.ts") == caller
            return model.review

        monkeypatch.setattr(CodeReviewer, "_review_once", read_related)
        self.register()
        for key, value in {
            "review.plan": "premium",
            "review.discovery_passes": 3,
        }.items():
            assert (
                self.client.put(
                    f"/api/local/settings/{key}", json={"value": value}
                ).status_code
                == 200
            )
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.source, text=True
        ).strip()
        self.edit_the_migration()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "test: Change the migration")
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.source, text=True
        ).strip()
        response = self.review(against=base, head=head)
        assert response.status_code == 200
        execution = response.json()["data"]["review"]["review"]["execution"]
        assert len(execution["reviews"]) == 3
        assert not execution["evaluations"][1]["error"]
        assert execution["evaluations"][1]["judgments"][0]["status"] == "supported"

    def test_a_checkout_with_nothing_changed_is_ready(self):
        self.register()

        answered = self.review().json()["data"]["review"]

        assert answered["ready"] is True
        assert answered["changed"] == []

    def test_what_changed_is_read_without_asking_anything(self):
        self.register()
        self.edit_the_migration()

        answered = self.review(use_model=False).json()["data"]["review"]

        assert [item["path"] for item in answered["changed"]] == ["db/0001_charges.py"]
        assert answered["verdicts"] == []

    def test_work_that_is_not_committed_yet_is_still_the_work(self):
        self.register()
        (self.source / "db" / "0002_refunds.py").write_text(
            "def up():\n    pass\n", encoding="utf-8"
        )

        answered = self.review(use_model=False).json()["data"]["review"]

        assert [item["path"] for item in answered["changed"]] == ["db/0002_refunds.py"]
        assert answered["changed"][0]["change"] == "added"

    def test_another_checkout_nested_in_this_one_is_not_this_one_work(self, tmp_path):
        # A worktree, or a repository somebody cloned in here. Git reports it as
        # a directory it will not look inside, and everything in it belongs to
        # that checkout rather than to the change being reviewed.
        self.register()
        nested = self.source / "elsewhere"
        nested.mkdir()
        subprocess.run(
            ["git", "init", "-q"], cwd=nested, check=True, capture_output=True
        )
        (nested / "theirs.py").write_text("x = 1\n", encoding="utf-8")
        self.edit_the_migration()

        answered = self.review(use_model=False).json()["data"]["review"]

        assert [item["path"] for item in answered["changed"]] == ["db/0001_charges.py"]

    def test_each_file_carries_what_changed_in_it(self):
        # A list of names is not a review. Somebody has to see the work.
        self.register()
        self.edit_the_migration()

        answered = self.review(use_model=False).json()["data"]["review"]

        patch = answered["changed"][0]["patch"]
        assert "db/0001_charges.py" in patch
        assert "+    return 1" in patch

    def test_a_file_nobody_had_staged_carries_its_own_too(self):
        self.register()
        (self.source / "db" / "0002_refunds.py").write_text(
            "def up():\n    return 2\n", encoding="utf-8"
        )

        answered = self.review(use_model=False).json()["data"]["review"]

        assert "+    return 2" in answered["changed"][0]["patch"]

    def test_the_repository_own_rule_is_the_one_picked(self):
        self.register()
        self.edit_the_migration()

        answered = self.review(use_model=False, title="Edit the charges migration")

        assert [item["id"] for item in answered.json()["data"]["review"]["skills"]] == [
            "migrations"
        ]

    def test_a_skill_this_machine_keeps_out_is_not_read_against_a_change(self):
        self.register()
        self.edit_the_migration()
        self.client.put(
            "/api/local/settings/skills.uses",
            json={"value": {"migrations": {"review": False}}},
        )
        try:
            answered = self.review(use_model=False, title="Edit the charges migration")
        finally:
            self.client.delete("/api/local/settings/skills.uses")

        assert answered.json()["data"]["review"]["skills"] == []

    def test_breaking_a_stated_rule_says_the_work_is_not_ready(self, monkeypatch):
        model = FakeModel(
            {
                "passed": False,
                "note": "A migration that already ran was edited.",
                "findings": [
                    {
                        "detail": "Add a new migration instead.",
                        "severity": "blocking",
                        "path": "db/0001_charges.py",
                        "line": 2,
                    }
                ],
            }
        )
        monkeypatch.setattr(
            working_tree,
            "provider_for",
            lambda *_, **__: model,
        )
        self.register()
        self.edit_the_migration()

        answered = self.review(title="Edit the charges migration").json()["data"][
            "review"
        ]

        assert answered["ready"] is False
        assert answered["verdicts"][0]["skill"] == "migrations"
        assert answered["verdicts"][0]["findings"][0]["severity"] == "blocking"
        assert any(
            "Never edit a migration" in prompt
            for purpose, prompt in zip(model.asked_purposes, model.asked)
            if purpose is None
        )

    def test_advice_alone_does_not_stop_the_work(self, monkeypatch):
        model = FakeModel(
            {
                "passed": True,
                "findings": [{"detail": "Name it sooner.", "severity": "advisory"}],
            }
        )
        monkeypatch.setattr(
            working_tree,
            "provider_for",
            lambda *_, **__: model,
        )
        self.register()
        self.edit_the_migration()

        answered = self.review(title="Edit the charges migration").json()["data"][
            "review"
        ]

        assert answered["ready"] is True
        assert answered["verdicts"][0]["findings"][0]["severity"] == "advisory"

    def test_judging_without_a_model_is_refused_rather_than_guessed(self, monkeypatch):
        monkeypatch.setattr(
            working_tree,
            "provider_for",
            lambda *_, **__: None,
        )
        self.register()
        self.edit_the_migration()

        answered = self.review().json()["data"]

        assert answered["status"] == "failed"
        assert "No model is configured" in answered["error"]

    def test_a_review_is_a_review_rather_than_a_list_of_rule_breaches(
        self, monkeypatch
    ):
        model = FakeModel(
            {"passed": True},
            review=_review(
                verdict=Verdict.REQUEST_CHANGES,
                suggestions=[
                    CodeSuggestion(
                        file_name="db/0001_charges.py",
                        start_line=2,
                        end_line=2,
                        side=Side.RIGHT,
                        comment="Add a new migration instead.",
                        category=SuggestionCategory.BUG,
                        existing_code="    return 1\n",
                        suggested_code="    pass\n",
                    )
                ],
            ),
        )
        monkeypatch.setattr(
            working_tree,
            "provider_for",
            lambda *_, **__: model,
        )
        self.register()
        self.edit_the_migration()

        answered = self.review(title="Edit the charges migration").json()["data"][
            "review"
        ]

        assert answered["review"]["verdict"] == "REQUEST_CHANGES"
        assert answered["review"]["summary"]["overview"]
        assert answered["review"]["suggestions"][0]["suggested_code"]
        # A verdict of change-this is not ready, whatever the skills said.
        assert answered["ready"] is False

    @pytest.mark.parametrize("overview_fails", [False, True])
    def test_conditional_defects_survive_nitpick_filter_and_overview_failure(
        self, monkeypatch, overview_fails
    ):
        from src.models.code_review import Blast, Certainty, Impact, Trigger

        candidate = CodeSuggestion(
            file_name="db/0001_charges.py",
            start_line=2,
            end_line=2,
            side=Side.RIGHT,
            comment="Add a new migration instead.",
            category=SuggestionCategory.BUG,
            existing_code="    return 1\n",
            suggested_code="    pass\n",
            trigger=Trigger.ANYONE,
            blast=Blast.ONE,
            impact=Impact.CRASH,
            certainty=Certainty.CONDITIONAL,
        )

        class Model(FakeModel):
            def generate_summary(self, suggestions, **kwargs):
                if overview_fails:
                    raise ValueError("Invalid structured summary output")
                return super().generate_summary(suggestions, **kwargs)

        model = Model({"passed": True}, review=_review(suggestions=[candidate]))
        monkeypatch.setattr(working_tree, "provider_for", lambda *_, **__: model)
        self.register()
        assert (
            self.client.put(
                "/api/local/settings/review.plan", json={"value": "premium"}
            ).status_code
            == 200
        )
        self.edit_the_migration()
        response = self.review()
        assert response.status_code == 200
        answer = response.json()["data"]
        assert answer["status"] == "done"
        review = answer["review"]
        assert len(review["review"]["suggestions"]) == 1
        assert review["review"]["suggestions"][0]["comment"] == candidate.comment
        assert review["review"]["execution"]["candidates"]
        assert review["review"]["summary"]["overview"]
        if overview_fails:
            assert review["note"] == (
                "Overview generation failed; evaluated review retained."
            )

    def test_the_reviewer_is_told_what_the_team_wrote_down(self, monkeypatch):
        model = FakeModel({"passed": True})
        monkeypatch.setattr(
            working_tree,
            "provider_for",
            lambda *_, **__: model,
        )
        self.register()
        self.edit_the_migration()

        self.review(title="Edit the charges migration")

        told = model.told[0]["knowledge"]
        assert "Never edit a migration" in told
        assert "What this team expects of work here" in told

    def test_a_checkout_can_be_named_by_its_path(self):
        self.register()

        answered = self.review(repository=str(self.source), use_model=False)

        assert answered.status_code == 200
        assert answered.json()["data"]["repository"] == "acme/billing"

    def test_a_path_inside_a_checkout_names_that_checkout(self):
        self.register()

        answered = self.review(repository=str(self.source / "db"), use_model=False)

        assert answered.json()["data"]["repository"] == "acme/billing"

    def test_a_checkout_nobody_registered_is_covered_by_being_reviewed(self):
        self.edit_the_migration()

        answered = self.review(repository=str(self.source), use_model=False)

        assert answered.status_code == 200
        assert answered.json()["data"]["repository"] == "billing"
        listed = self.client.get("/api/code/repositories").json()["data"]
        assert [item["path"] for item in listed] == [str(self.source)]

    def test_a_path_that_is_nothing_is_still_refused(self):
        assert self.start(repository=str(self.source / "nowhere")).status_code == 404

    def test_a_folder_that_is_not_a_checkout_is_not_covered(self, tmp_path):
        # Registering it would leave a folder nothing can ever review.
        elsewhere = tmp_path / "notes"
        elsewhere.mkdir()

        assert self.start(repository=str(elsewhere)).status_code == 404
        assert self.client.get("/api/code/repositories").json()["data"] == []

    def test_a_subdirectory_covers_the_checkout_it_is_in(self):
        answered = self.review(repository=str(self.source / "db"), use_model=False)

        assert answered.status_code == 200
        listed = self.client.get("/api/code/repositories").json()["data"]
        assert [item["path"] for item in listed] == [str(self.source)]

    def test_a_repository_nobody_registered_is_not_reviewed(self):
        # Refused when it is asked for, rather than written down as a review
        # that failed: nobody wants a record of a typo.
        assert self.start().status_code == 404

    def test_a_review_is_kept_so_a_link_to_it_still_opens(self):
        self.register()
        self.edit_the_migration()

        identifier = self.start(use_model=False).json()["data"]["id"]

        found = self.client.get(f"/api/local/reviews/{identifier}")
        assert found.status_code == 200
        assert found.json()["data"]["status"] == "done"
        assert found.json()["data"]["path"] == f"/reviews/{identifier}"

    def test_a_review_whose_reader_went_away_says_so(self, tmp_path):
        # A stdio agent lives as long as its client. Whatever it started dies
        # with it, and a spinner that never stops is a worse answer than none.
        from datetime import timedelta

        from src.core.review import ReviewRecord, now

        store = app.dependency_overrides[get_reviews]()
        store.put(
            ReviewRecord(
                id="abandoned",
                repository="acme/billing",
                started=now() - timedelta(hours=2),
            )
        )

        found = self.client.get("/api/local/reviews/abandoned").json()["data"]

        assert found["status"] == "failed"
        assert "stopped before it finished" in found["error"]

    def test_a_review_that_only_just_started_is_still_running(self):
        from src.core.review import ReviewRecord

        store = app.dependency_overrides[get_reviews]()
        store.put(ReviewRecord(id="fresh", repository="acme/billing"))

        found = self.client.get("/api/local/reviews/fresh").json()["data"]

        assert found["status"] == "running"

    def test_a_review_nobody_asked_for_is_not_invented(self):
        assert self.client.get("/api/local/reviews/nothing").status_code == 404

    def test_the_last_few_come_back_without_their_findings(self):
        self.register()
        self.edit_the_migration()
        self.start(use_model=False)

        listed = self.client.get("/api/local/reviews?repository=acme/billing")

        assert listed.status_code == 200
        assert len(listed.json()["data"]) == 1
        # A list is a list. Whoever wants the findings opens the one they mean.
        assert listed.json()["data"][0]["review"] == {}


class TestSkillsApi(BaseTestCase):
    """Drives /api/skills the way the local UI does."""

    @pytest.fixture(autouse=True)
    def machine(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("SOURCEANT_MACHINE_HOME", str(tmp_path / "person"))
        monkeypatch.setattr("src.api.routes.code.LOCAL_MODE", True)
        self.source = tmp_path / "billing"
        own = self.source / ".sourceant" / "skills" / "migrations"
        own.mkdir(parents=True)
        (own / "SKILL.md").write_text(MIGRATIONS_SKILL, encoding="utf-8")
        theirs = tmp_path / "person" / ".codex" / "skills" / "commits"
        theirs.mkdir(parents=True)
        (theirs / "SKILL.md").write_text(
            "---\nname: commits\ndescription: Use when committing.\n---\n\nSay why.\n",
            encoding="utf-8",
        )
        yield

    def test_what_a_person_and_their_agents_keep_are_read_together(self):
        self.client.post(
            "/api/code/repositories",
            json={"path": str(self.source), "name": "acme/billing"},
        )

        answered = self.client.get("/api/skills?repository=acme/billing").json()["data"]

        assert sorted(item["id"] for item in answered["skills"]) == [
            "commits",
            "migrations",
        ]
        # The repository's own is read where the team committed it, and the
        # person's from wherever their agent keeps it.
        assert {item["origin"] for item in answered["skills"]} == {"codex", "sourceant"}

    def test_one_skill_comes_back_in_full_so_a_person_can_read_it(self):
        answered = self.client.get("/api/skills/commits").json()["data"]

        assert answered["name"] == "commits"
        assert answered["body"] == "Say why."

    def test_a_skill_nobody_wrote_is_not_invented(self):
        assert self.client.get("/api/skills/nowhere").status_code == 404

    def register(self):
        return self.client.post(
            "/api/code/repositories",
            json={"path": str(self.source), "name": "acme/billing"},
        )

    def state(self, **body):
        return self.client.put(
            "/api/skills", json={"repository": "acme/billing", **body}
        )

    def test_a_rule_can_be_stated_and_read_back(self):
        self.register()

        written = self.state(
            id="retry-limit",
            name="retry-limit",
            description="Use when a change touches how a charge is retried.",
            body="Charges retry three times, then stop.",
        )

        assert written.status_code == 200
        read = self.client.get("/api/skills/retry-limit?repository=acme/billing")
        assert read.json()["data"]["body"] == "Charges retry three times, then stop."
        assert read.json()["data"]["origin"] == "repository"

    def test_nothing_is_written_into_somebody_repository(self, tmp_path):
        # A folder appearing in a checkout because a tool was opened turns up
        # in their `git status` and in a review nobody asked for.
        self.register()

        self.state(id="retry-limit", description="Use when retrying a charge.")

        assert not (self.source / ".sourceant" / "skills" / "retry-limit").exists()
        assert list((tmp_path / "home" / "skills" / "repositories").iterdir())

    def test_a_skill_written_for_a_repository_is_read_back_for_it(self):
        self.register()

        self.state(id="retry-limit", description="Use when retrying a charge.")

        read = self.client.get("/api/skills?repository=acme/billing").json()["data"]
        assert "retry-limit" in [item["id"] for item in read["skills"]]

    def test_a_skill_written_for_one_repository_is_not_another_one(self, tmp_path):
        self.register()
        other = tmp_path / "other"
        (other / ".sourceant" / "skills").mkdir(parents=True)
        self.client.post(
            "/api/code/repositories", json={"path": str(other), "name": "acme/other"}
        )

        self.state(id="retry-limit", description="Use when retrying a charge.")

        read = self.client.get("/api/skills?repository=acme/other").json()["data"]
        assert "retry-limit" not in [item["id"] for item in read["skills"]]

    def test_what_a_skill_is_for_is_kept_apart_from_how_it_is_read(self):
        self.register()

        self.state(
            id="retry-limit",
            description="Use when retrying a charge.",
            applications={"review": True, "onboarding": False},
            type="review-pass",
        )

        read = self.client.get(
            "/api/skills/retry-limit?repository=acme/billing"
        ).json()["data"]
        assert read["applications"] == {"review": True, "onboarding": False}
        assert read["type"] == "review-pass"
        # The one purpose this product reads, answered from the same map.
        assert read["reviews"] is True

    def test_what_a_skill_can_be_used_for_is_served_rather_than_guessed(self):
        answered = self.client.get("/api/skills/uses")

        assert answered.status_code == 200
        offered = answered.json()["data"]
        assert [use["id"] for use in offered][0] == "review"
        assert {"id", "label"} == set(offered[0])

    def test_a_skill_only_a_person_may_invoke_stays_that_way_when_saved(self):
        self.register()

        self.state(id="retry-limit", description="Use when retrying.", automatic=False)

        read = self.client.get(
            "/api/skills/retry-limit?repository=acme/billing"
        ).json()["data"]
        assert read["automatic"] is False

    def test_a_kind_of_skill_nobody_defined_is_refused(self):
        self.register()

        answered = self.state(
            id="retry-limit", description="Use when retrying.", type="whatever"
        )

        assert answered.status_code == 400

    def test_the_old_way_of_saying_it_is_for_reviews_still_works(self):
        self.register()

        self.state(id="retry-limit", description="Use when retrying.", reviews=False)

        read = self.client.get(
            "/api/skills/retry-limit?repository=acme/billing"
        ).json()["data"]
        assert read["applications"] == {"review": False}
        assert read["reviews"] is False

    def test_a_rule_with_no_line_saying_when_it_applies_is_refused(self):
        self.register()

        answered = self.state(id="retry-limit", description="  ")

        assert answered.status_code == 400
        assert "when it applies" in answered.json()["detail"]

    def test_a_name_that_is_a_path_is_refused_rather_than_tidied(self):
        self.register()

        answered = self.state(id="../../etc/passwd", description="Use when anything.")

        assert answered.status_code == 400
        assert not (self.source.parent / "etc").exists()

    def test_stating_it_again_replaces_it(self):
        self.register()
        self.state(id="retry-limit", description="Use when retrying.", body="Three.")

        self.state(id="retry-limit", description="Use when retrying.", body="Four.")

        read = self.client.get("/api/skills/retry-limit?repository=acme/billing")
        assert read.json()["data"]["body"] == "Four."

    def test_a_rule_this_repository_stated_can_be_forgotten(self):
        self.register()
        self.state(id="retry-limit", description="Use when retrying a charge.")

        forgotten = self.client.delete(
            "/api/skills?repository=acme/billing&id=retry-limit"
        )

        assert forgotten.status_code == 200
        assert self.client.get("/api/skills/retry-limit").status_code == 404

    def test_a_skill_can_apply_everywhere_rather_than_to_one_repository(self, tmp_path):
        # What somebody works by everywhere, rather than what one project needs.
        written = self.client.put(
            "/api/skills",
            json={
                "scope": "global",
                "id": "write-simply",
                "description": "Use when drafting prose of any kind.",
                "body": "Say the thing, then stop.",
            },
        )

        assert written.status_code == 200
        assert written.json()["data"]["origin"] == "global"
        assert (
            tmp_path / "home" / "skills" / "global" / "write-simply" / "SKILL.md"
        ).is_file()

    def test_a_global_skill_is_read_back_without_naming_a_repository(self, tmp_path):
        self.client.put(
            "/api/skills",
            json={
                "scope": "global",
                "id": "write-simply",
                "description": "Use when drafting prose of any kind.",
                "body": "Say the thing, then stop.",
            },
        )

        listed = self.client.get("/api/skills").json()["data"]

        assert "write-simply" in [item["id"] for item in listed["skills"]]
        assert self.client.get("/api/skills/write-simply").json()["data"]["body"] == (
            "Say the thing, then stop."
        )

    def test_a_global_skill_is_forgotten_without_naming_a_repository(self, tmp_path):
        self.client.put(
            "/api/skills",
            json={
                "scope": "global",
                "id": "write-simply",
                "description": "Use when drafting prose.",
            },
        )

        forgotten = self.client.delete("/api/skills?scope=global&id=write-simply")

        assert forgotten.status_code == 200
        assert self.client.get("/api/skills/write-simply").status_code == 404

    def test_a_scope_nobody_offers_is_refused(self):
        answered = self.client.put(
            "/api/skills",
            json={"scope": "everywhere", "id": "x", "description": "Use always."},
        )

        assert answered.status_code == 400

    def test_a_rule_somebody_keeps_in_their_own_folder_is_not_deleted_here(self):
        self.register()

        answered = self.client.delete("/api/skills?repository=acme/billing&id=commits")

        assert answered.status_code == 400
        assert "read here and never written" in answered.json()["detail"]
        assert (
            tmp_path_of(self) / "person" / ".codex" / "skills" / "commits" / "SKILL.md"
        ).is_file()


def tmp_path_of(case):
    return case.source.parent
