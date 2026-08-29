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
    any_match,
    discover,
    attach,
    followed,
    read_front_matter,
    references,
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


class TestARuleEveryOtherRulePointsAt:
    """What several rules share is the team's baseline, not any one rule's content."""

    def house(self, tmp_path):
        skills = tmp_path / "skills"
        write(
            skills,
            "house",
            "---\nname: house\n---\n\nA commit message says what changed and why.\n",
        )
        for name, about in (("commits", "committing"), ("layout", "a page layout")):
            write(
                skills,
                name,
                f"---\nname: {name}\ndescription: Use when {about}.\n---\n\n"
                "Read `../house/SKILL.md`, then follow it.\n",
            )
        return DirectorySkillSource(skills, "codex").read()

    def test_the_shared_document_is_recognised_as_one_document(self, tmp_path):
        skills = {skill.id: references(skill) for skill in self.house(tmp_path)}

        pointed = [
            path for id_, found in skills.items() if id_ != "house" for path in found
        ]

        assert len(pointed) == 2
        assert len(set(pointed)) == 1

    def test_a_rule_carries_only_what_is_its_own(self, tmp_path):
        found = self.house(tmp_path)
        commits = next(skill for skill in found if skill.id == "commits")

        alone = attach(commits.body, {})
        with_it = followed(commits)

        assert "what changed and why" not in alone
        assert "what changed and why" in with_it


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


class TestFindingWhereSkillsAreKept:
    """A list of tool names is always one tool behind. These are found."""

    def test_a_tool_nobody_here_has_heard_of_is_read_the_same(self, tmp_path):
        write(tmp_path / ".whatever" / "skills", "house", COMMIT)

        found = discover(tmp_path)

        assert [source.origin for source in found] == ["whatever"]
        assert [skill.id for skill in found[0].read()] == ["house"]

    def test_the_folder_a_tool_keeps_them_in_names_the_tool(self, tmp_path):
        write(tmp_path / ".claude" / "skills", "one", COMMIT)
        write(tmp_path / ".codex" / "skills", "two", COMMIT)

        found = discover(tmp_path)

        assert sorted(source.origin for source in found) == ["claude", "codex"]

    def test_commands_are_skills_too(self, tmp_path):
        # Custom commands and skills were merged: a file at commands/deploy.md
        # and a folder at skills/deploy/SKILL.md are the same thing.
        folder = tmp_path / ".claude" / "commands"
        folder.mkdir(parents=True)
        (folder / "nfebe-pr.md").write_text(
            "---\ndescription: Prepare a pull request.\n---\n\nDo it carefully.\n",
            encoding="utf-8",
        )

        found = discover(tmp_path)
        skills = found[0].read()

        assert [skill.id for skill in skills] == ["nfebe-pr"]
        assert skills[0].description == "Prepare a pull request."

    def test_a_document_a_skill_was_written_with_is_not_another_skill(self, tmp_path):
        folder = write(tmp_path / ".claude" / "skills", "workflows", COMMIT)
        (folder / "global.md").write_text("Shared preferences.\n", encoding="utf-8")

        skills = discover(tmp_path)[0].read()

        assert [skill.id for skill in skills] == ["workflows"]

    def test_somewhere_that_is_not_a_tool_is_left_alone(self, tmp_path):
        write(tmp_path / ".git" / "skills", "nope", COMMIT)
        write(tmp_path / ".cache" / "skills", "nope", COMMIT)

        assert discover(tmp_path) == []

    def test_a_home_with_nothing_in_it_finds_nothing(self, tmp_path):
        assert discover(tmp_path) == []


class TestWhatTheAuthorSaid:
    """The skill format lets somebody state this, and a statement beats a guess."""

    def declared(self, tmp_path, front):
        write(tmp_path, "declared", f"---\n{front}---\n\nDo the thing.\n")
        return DirectorySkillSource(tmp_path, "codex").read()[0]

    def test_globs_naming_the_files_it_is_about_are_read(self, tmp_path):
        skill = self.declared(
            tmp_path,
            "name: migrations\ndescription: Use when editing one.\n"
            "paths:\n  - db/migrations/**\n  - '**/*.sql'\n",
        )

        assert skill.paths == ("db/migrations/**", "**/*.sql")

    def test_globs_written_on_one_line_mean_the_same(self, tmp_path):
        skill = self.declared(
            tmp_path, "name: x\ndescription: y\npaths: db/**, app/**\n"
        )

        assert skill.paths == ("db/**", "app/**")

    def test_a_client_may_keep_its_own_keys_where_the_format_says(self, tmp_path):
        skill = self.declared(
            tmp_path,
            "name: x\ndescription: y\nmetadata:\n  sourceant:\n    review: false\n",
        )

        assert skill.reviews is False

    def test_a_skill_only_a_person_may_start_says_so(self, tmp_path):
        skill = self.declared(
            tmp_path, "name: x\ndescription: y\ndisable-model-invocation: true\n"
        )

        assert skill.automatic is False

    def test_saying_nothing_is_not_saying_no(self, tmp_path):
        skill = self.declared(tmp_path, "name: x\ndescription: y\n")

        assert skill.reviews is None
        assert skill.automatic is True

    def test_anything_else_the_author_wrote_is_kept_rather_than_read(self, tmp_path):
        skill = self.declared(
            tmp_path, "name: x\ndescription: y\nlicense: MIT\nallowed-tools: Read\n"
        )

        assert skill.properties["license"] == "MIT"

    def test_a_block_yaml_will_not_take_still_gives_up_its_name(self, tmp_path):
        # Real frontmatter is often not valid YAML. A hint written as
        # `[working | <path>] [--only]` is two flow sequences on one line,
        # which the agents that read these files tolerate. Losing the
        # description over a field nothing here reads would be our fault.
        fields, body = read_front_matter(
            "---\n"
            "description: Prepare a pull request.\n"
            "argument-hint: [working | <file path>] [--critique-only]\n"
            "---\n\nDo it carefully.\n"
        )

        assert fields["description"] == "Prepare a pull request."
        assert "Do it carefully." in body


class TestWhichFilesASkillIsAbout:
    def test_one_star_stops_at_a_separator(self):
        assert any_match(["src/thing.py"], ["src/*.py"])
        assert not any_match(["src/deep/thing.py"], ["src/*.py"])

    def test_two_stars_cross_them(self):
        assert any_match(["src/deep/down/thing.py"], ["src/**"])

    def test_anywhere_matches_at_the_top_too(self):
        assert any_match(["thing.sql"], ["**/*.sql"])
        assert any_match(["db/migrations/thing.sql"], ["**/*.sql"])

    def test_a_directory_means_everything_under_it(self):
        assert any_match(["db/migrations/0001.py"], ["db/migrations/"])

    def test_naming_nothing_matches_nothing(self):
        assert not any_match(["anything.py"], [])


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

    def test_a_skill_that_named_the_files_is_picked_on_that_alone(self):
        # Nothing in the title says migration; the author already said which
        # files this is about, which is better than anything read out of words.
        skills = [
            Skill(
                id="migrations",
                name="migrations",
                description="Use when editing one.",
                body="",
                paths=("db/migrations/**",),
            )
        ]

        chosen = PhraseSkillSelector().select(
            skills, Change(title="Bump the retry count", paths=("db/migrations/7.py",))
        )

        assert [item.id for item in chosen] == ["migrations"]

    def test_a_skill_that_named_other_files_is_not_picked_on_its_wording(self):
        skills = [
            Skill(
                id="migrations",
                name="migrations",
                description="Use when a change adds a database migration.",
                body="",
                paths=("db/migrations/**",),
            )
        ]

        chosen = PhraseSkillSelector().select(
            skills,
            Change(title="Add a database migration helper", paths=("app/helpers.py",)),
        )

        assert chosen == ()

    def test_a_skill_said_not_to_be_for_reviews_is_never_picked(self):
        skills = [
            Skill(
                id="migrations",
                name="migrations",
                description="Use when a change adds a database migration.",
                body="",
                metadata={"sourceant": {"review": False}},
            )
        ]

        assert (
            PhraseSkillSelector().select(
                skills, Change(title="Add a database migration")
            )
            == ()
        )

    def test_a_skill_said_to_be_for_reviews_is_always_picked(self):
        skills = [
            Skill(
                id="house",
                name="house",
                description="Nothing to do with anything.",
                body="",
                metadata={"sourceant": {"review": True}},
            )
        ]

        chosen = PhraseSkillSelector().select(skills, Change(title="Bump a timeout"))

        assert [item.id for item in chosen] == ["house"]

    def test_a_skill_only_a_person_may_start_is_not_started_here(self):
        skills = [
            Skill(
                id="migrations",
                name="migrations",
                description="Use when a change adds a database migration.",
                body="",
                automatic=False,
            )
        ]

        assert (
            PhraseSkillSelector().select(
                skills, Change(title="Add a database migration")
            )
            == ()
        )

    def test_what_was_stated_does_not_compete_with_what_was_guessed(self):
        skills = [
            Skill(
                id="house",
                name="house",
                description="Nothing to do with anything.",
                body="",
                metadata={"sourceant": {"review": True}},
            ),
            skill("migrations", "Use when a change adds a database migration."),
        ]

        chosen = PhraseSkillSelector().select(
            skills, Change(title="Add a database migration"), limit=2
        )

        assert [item.id for item in chosen] == ["house", "migrations"]


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
