"""Reading, choosing and applying the rules a team already wrote down."""

import json

from src.core.skills import (
    ADVISORY,
    BLOCKING,
    Catalogue,
    Change,
    DirectorySkillSource,
    ModelSkillChecker,
    PhraseSkillSelector,
    Skill,
    SkillQuery,
    followed,
    read_front_matter,
)

COMMIT = """---
name: house-commits
description: Use when the user asks to commit work, or to follow the commit rules.
---

# Commits

A commit message says what changed and why, never how.
"""

MIGRATIONS = """---
name: migrations
description: >
  Use when a change adds or edits a database migration, or alters a schema.
---

Never edit a migration that has already run anywhere.
"""


def write(root, folder, text):
    directory = root / folder
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(text, encoding="utf-8")
    return directory


class TestReadingSkillsOffDisk:
    def test_the_block_at_the_top_names_the_skill(self):
        fields, body = read_front_matter(COMMIT)

        assert fields["name"] == "house-commits"
        assert fields["description"].startswith("Use when the user asks to commit")
        assert body.startswith("# Commits")

    def test_an_indented_line_continues_the_value_above_it(self):
        fields, _ = read_front_matter(MIGRATIONS)

        assert "adds or edits a database migration" in fields["description"]

    def test_a_file_with_no_block_is_all_body(self):
        fields, body = read_front_matter("Just some prose.\n")

        assert fields == {}
        assert body == "Just some prose.\n"

    def test_a_folder_of_skills_reads_as_skills(self, tmp_path):
        write(tmp_path, "house-commits", COMMIT)
        write(tmp_path, "migrations", MIGRATIONS)

        found = DirectorySkillSource(tmp_path, "codex").read()

        assert [skill.id for skill in found] == ["house-commits", "migrations"]
        assert {skill.origin for skill in found} == {"codex"}
        assert found[0].name == "house-commits"

    def test_a_missing_directory_is_no_skills_rather_than_an_error(self, tmp_path):
        assert DirectorySkillSource(tmp_path / "nowhere", "claude").read() == ()

    def test_a_skill_linked_in_from_elsewhere_is_still_read(self, tmp_path):
        # People keep these in a repository of their own and link them into the
        # agent's folder, which is most of what is on a real machine.
        elsewhere = tmp_path / "knowledgebase"
        write(elsewhere, "house-commits", COMMIT)
        folder = tmp_path / "skills"
        folder.mkdir()
        (folder / "house-commits").symlink_to(elsewhere / "house-commits")

        found = DirectorySkillSource(folder, "codex").read()

        assert [skill.id for skill in found] == ["house-commits"]

    def test_a_link_back_up_the_tree_does_not_walk_forever(self, tmp_path):
        write(tmp_path, "house-commits", COMMIT)
        (tmp_path / "house-commits" / "itself").symlink_to(tmp_path)

        found = DirectorySkillSource(tmp_path, "codex").read()

        assert [skill.id for skill in found] == ["house-commits"]

    def test_a_skill_with_no_block_still_has_its_folder_name(self, tmp_path):
        write(tmp_path, "unnamed", "Do the thing.\n")

        found = DirectorySkillSource(tmp_path, "claude").read()

        assert found[0].name == "unnamed"
        assert found[0].description == ""

    def test_the_agent_own_built_ins_are_not_the_team_rules(self, tmp_path):
        # Codex ships its own under `.system`. Reading those puts a page about
        # generating images in front of somebody's pull request.
        write(tmp_path, "house-commits", COMMIT)
        write(tmp_path / ".system", "imagegen", MIGRATIONS)

        found = DirectorySkillSource(tmp_path, "codex").read()

        assert [skill.id for skill in found] == ["house-commits"]


class TestFollowingWhatARulePointsAt:
    def test_a_rule_that_points_at_a_document_is_read_with_it(self, tmp_path):
        skills = tmp_path / "skills"
        write(
            skills, "shared", "---\nname: shared\n---\n\nNever edit an applied one.\n"
        )
        folder = write(
            skills,
            "migrations",
            "---\nname: migrations\ndescription: Use when editing a migration.\n---\n\n"
            "Read `../shared/SKILL.md` completely, then follow it.\n",
        )

        pointer = DirectorySkillSource(skills, "codex").read()
        whole = followed(next(s for s in pointer if s.id == "migrations"))

        assert "Never edit an applied one." in whole
        assert str(folder)

    def test_it_does_not_reach_outside_the_skills_folder(self, tmp_path):
        skills = tmp_path / "skills"
        (tmp_path / "private").mkdir()
        (tmp_path / "private" / "secrets.md").write_text(
            "Nobody's business.", encoding="utf-8"
        )
        write(
            skills,
            "nosey",
            "---\nname: nosey\ndescription: Use always.\n---\n\nRead `../../private/secrets.md`.\n",
        )

        whole = followed(DirectorySkillSource(skills, "codex").read()[0])

        assert "Nobody's business." not in whole

    def test_a_rule_that_points_nowhere_is_left_as_it_is(self, tmp_path):
        skills = tmp_path / "skills"
        write(
            skills,
            "dangling",
            "---\nname: dangling\ndescription: Use always.\n---\n\nRead `../gone/SKILL.md`.\n",
        )

        skill = DirectorySkillSource(skills, "codex").read()[0]

        assert followed(skill) == skill.body


class TestSeveralPlacesReadAsOneList:
    def test_a_repository_overrides_the_machine_on_a_shared_name(self, tmp_path):
        machine, repository = tmp_path / "machine", tmp_path / "repository"
        write(machine, "house-commits", COMMIT)
        write(repository, "house-commits", MIGRATIONS)

        catalogue = Catalogue(
            sources=(
                DirectorySkillSource(machine, "codex"),
                DirectorySkillSource(repository, "repository"),
            )
        )

        assert len(catalogue.all()) == 1
        assert catalogue.get("house-commits").origin == "repository"

    def test_searching_narrows_by_words_and_by_where_it_came_from(self, tmp_path):
        machine, repository = tmp_path / "machine", tmp_path / "repository"
        write(machine, "house-commits", COMMIT)
        write(repository, "migrations", MIGRATIONS)
        catalogue = Catalogue(
            sources=(
                DirectorySkillSource(machine, "codex"),
                DirectorySkillSource(repository, "repository"),
            )
        )

        assert [
            s.id for s in catalogue.search(SkillQuery(text="migration")).skills
        ] == ["migrations"]
        assert [
            s.id for s in catalogue.search(SkillQuery(origins=("codex",))).skills
        ] == ["house-commits"]


def skill(identifier, description, body=""):
    return Skill(id=identifier, name=identifier, description=description, body=body)


class TestChoosingWhichSkillsApply:
    def test_a_change_picks_the_skill_written_about_it(self):
        skills = [
            skill("migrations", "Use when a change adds a database migration."),
            skill("frontend", "Use when styling a component or a stylesheet."),
        ]

        chosen = PhraseSkillSelector().select(
            skills,
            Change(
                title="Add a migration for the charges table",
                paths=("src/database/migrations/2026_add_charges.py",),
            ),
        )

        assert [item.id for item in chosen] == ["migrations"]

    def test_a_change_nothing_was_written_about_picks_nothing(self):
        skills = [skill("frontend", "Use when styling a component or a stylesheet.")]

        chosen = PhraseSkillSelector().select(
            skills, Change(title="Bump the queue timeout", paths=("worker/queue.py",))
        )

        assert chosen == ()

    def test_one_of_them_being_plural_does_not_hide_the_match(self):
        skills = [skill("secrets", "Use when scanning changes for a secret.")]

        chosen = PhraseSkillSelector().select(
            skills, Change(title="Scan the example config for secrets")
        )

        assert [item.id for item in chosen] == ["secrets"]

    def test_a_skill_that_says_one_thing_beats_one_that_says_twenty(self):
        skills = [
            skill("migrations", "Use when a change edits a migration."),
            skill(
                "everything",
                "Use when a change edits a migration, a route, a template, a "
                "stylesheet, a queue, a worker, a schedule or a report.",
            ),
        ]

        chosen = PhraseSkillSelector().select(skills, Change(title="Edit a migration"))

        assert [item.id for item in chosen] == ["migrations", "everything"]

    def test_the_body_does_not_decide_which_rule_applies(self):
        # A long document shares words with every change there has ever been.
        skills = [
            skill("migrations", "Use when a change edits a migration."),
            skill("images", "Use when generating a picture.", body="migration " * 200),
        ]

        chosen = PhraseSkillSelector().select(skills, Change(title="Edit a migration"))

        assert [item.id for item in chosen] == ["migrations"]

    def test_a_change_that_says_nothing_picks_nothing(self):
        skills = [skill("migrations", "Use when a change adds a database migration.")]

        assert PhraseSkillSelector().select(skills, Change()) == ()


class TestPuttingAChangeThroughASkill:
    def answer(self, payload):
        return lambda prompt: json.dumps(payload)

    def test_a_breach_of_a_stated_rule_blocks(self):
        checker = ModelSkillChecker(
            ask=self.answer(
                {
                    "passed": False,
                    "note": "The migration that already ran was edited.",
                    "findings": [
                        {
                            "detail": "Add a new migration instead.",
                            "severity": "blocking",
                            "path": "db/2026_charges.py",
                            "line": 12,
                        }
                    ],
                }
            ),
            model="a-model",
        )

        verdict = checker.check(
            skill("migrations", "Never edit an applied one."), Change()
        )

        assert verdict.passed is False
        assert verdict.blocking[0].path == "db/2026_charges.py"
        assert verdict.blocking[0].severity == BLOCKING

    def test_anything_not_stated_as_a_rule_only_advises(self):
        checker = ModelSkillChecker(
            ask=self.answer(
                {
                    "passed": True,
                    "findings": [
                        {"detail": "Consider naming it sooner.", "severity": ""}
                    ],
                }
            )
        )

        verdict = checker.check(skill("commits", "Say what and why."), Change())

        assert verdict.passed is True
        assert verdict.findings[0].severity == ADVISORY
        assert verdict.blocking == ()

    def test_an_answer_wrapped_in_a_fence_is_still_an_answer(self):
        checker = ModelSkillChecker(
            ask=lambda prompt: '```json\n{"passed": false, "note": "No."}\n```'
        )

        assert checker.check(skill("commits", "Say why."), Change()).passed is False

    def test_nothing_usable_back_does_not_stop_the_work(self):
        checker = ModelSkillChecker(ask=lambda prompt: "I could not tell.")

        verdict = checker.check(skill("commits", "Say why."), Change())

        assert verdict.passed is True
        assert verdict.findings == ()
        assert "not applied" in verdict.note
