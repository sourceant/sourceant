import json
import subprocess

import pytest
from sqlalchemy import create_engine

from src.api.main import app
from src.api.routes.code import get_code_index
from src.api.routes.knowledge import get_knowledge
from src.core.code_index import SQLCodeIndexRepository
from src.core.knowledge import SQLKnowledgeRepository
from src.tests.base_test import BaseTestCase

MIGRATIONS_SKILL = """---
name: migrations
description: Use when a change adds or edits a database migration or a schema.
---

Never edit a migration that has already run.
"""


class FakeModel:
    """A model that answers whatever the test told it to."""

    def __init__(self, answer):
        self.answer = answer
        self.asked = []
        self.model = "a-model"

    def generate_text(self, prompt):
        self.asked.append(prompt)
        return json.dumps(self.answer)


class TestLocalReview(BaseTestCase):
    """Drives /api/local/reviews the way the local UI does."""

    @pytest.fixture(autouse=True)
    def machine(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path / "home"))
        # Otherwise this reads whichever skills the person running the tests
        # keeps in their own agent folders.
        monkeypatch.setattr("src.core.skills.filesystem.MACHINE_SKILLS", ())
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
        app.dependency_overrides[get_code_index] = lambda: SQLCodeIndexRepository(
            engine, create_schema=True
        )
        app.dependency_overrides[get_knowledge] = lambda: SQLKnowledgeRepository(
            engine, create_schema=True
        )
        yield
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

    def review(self, **body):
        return self.client.post(
            "/api/local/reviews", json={"repository": "acme/billing", **body}
        )

    def test_a_checkout_with_nothing_changed_is_ready(self):
        self.register()

        answered = self.review().json()["data"]

        assert answered["ready"] is True
        assert answered["changed"] == []

    def test_what_changed_is_read_without_asking_anything(self):
        self.register()
        self.edit_the_migration()

        answered = self.review(use_model=False).json()["data"]

        assert [item["path"] for item in answered["changed"]] == ["db/0001_charges.py"]
        assert answered["verdicts"] == []

    def test_work_that_is_not_committed_yet_is_still_the_work(self):
        self.register()
        (self.source / "db" / "0002_refunds.py").write_text(
            "def up():\n    pass\n", encoding="utf-8"
        )

        answered = self.review(use_model=False).json()["data"]

        assert [item["path"] for item in answered["changed"]] == ["db/0002_refunds.py"]
        assert answered["changed"][0]["change"] == "added"

    def test_the_repository_own_rule_is_the_one_picked(self):
        self.register()
        self.edit_the_migration()

        answered = self.review(use_model=False, title="Edit the charges migration")

        assert [item["id"] for item in answered.json()["data"]["skills"]] == [
            "migrations"
        ]

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
            "src.api.routes.local_reviews.model_for_this_machine", lambda: model
        )
        self.register()
        self.edit_the_migration()

        answered = self.review(title="Edit the charges migration").json()["data"]

        assert answered["ready"] is False
        assert answered["verdicts"][0]["skill"] == "migrations"
        assert answered["verdicts"][0]["findings"][0]["severity"] == "blocking"
        assert "Never edit a migration" in model.asked[0]

    def test_advice_alone_does_not_stop_the_work(self, monkeypatch):
        model = FakeModel(
            {
                "passed": True,
                "findings": [{"detail": "Name it sooner.", "severity": "advisory"}],
            }
        )
        monkeypatch.setattr(
            "src.api.routes.local_reviews.model_for_this_machine", lambda: model
        )
        self.register()
        self.edit_the_migration()

        answered = self.review(title="Edit the charges migration").json()["data"]

        assert answered["ready"] is True
        assert answered["verdicts"][0]["findings"][0]["severity"] == "advisory"

    def test_judging_without_a_model_is_refused_rather_than_guessed(self, monkeypatch):
        monkeypatch.setattr(
            "src.api.routes.local_reviews.model_for_this_machine", lambda: None
        )
        self.register()
        self.edit_the_migration()

        assert self.review().status_code == 400

    def test_a_repository_nobody_registered_is_not_reviewed(self):
        assert self.review().status_code == 404


class TestSkillsApi(BaseTestCase):
    """Drives /api/skills the way the local UI does."""

    @pytest.fixture(autouse=True)
    def machine(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path / "home"))
        monkeypatch.setattr(
            "src.core.skills.filesystem.MACHINE_SKILLS",
            ((str(tmp_path / "person" / ".codex" / "skills"), "codex"),),
        )
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

    def test_a_machine_and_a_repository_are_read_together(self):
        self.client.post(
            "/api/code/repositories",
            json={"path": str(self.source), "name": "acme/billing"},
        )

        answered = self.client.get("/api/skills?repository=acme/billing").json()["data"]

        assert sorted(item["id"] for item in answered["skills"]) == [
            "commits",
            "migrations",
        ]
        assert {item["origin"] for item in answered["skills"]} == {
            "codex",
            "repository",
        }

    def test_one_skill_comes_back_in_full_so_a_person_can_read_it(self):
        answered = self.client.get("/api/skills/commits").json()["data"]

        assert answered["name"] == "commits"
        assert answered["body"] == "Say why."

    def test_a_skill_nobody_wrote_is_not_invented(self):
        assert self.client.get("/api/skills/nowhere").status_code == 404
