import pytest
from sqlalchemy import create_engine

from src.core.knowledge import (
    KnowledgeObject,
    InMemoryKnowledgeRepository,
    KnowledgeQuery,
)
from src.core.requirements import (
    CODE,
    KNOWLEDGE,
    TEST,
    CoverageQuery,
    GitHubIssueRequirements,
    KnowledgeBackedRequirements,
    Requirement,
    RequirementLink,
    RequirementQuery,
    SQLRequirementsRepository,
)
from src.core.scope import Scope

SCOPE = Scope.from_mapping({"repository": "acme/billing"})


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'requirements.db'}")
    return SQLRequirementsRepository(engine, create_schema=True)


def _requirement(identity="r1", status="open", summary="Refunds settle within a day"):
    return Requirement(id=identity, kind="requirement", status=status, summary=summary)


def test_a_requirement_needs_an_identity_a_kind_and_a_status():
    with pytest.raises(ValueError):
        Requirement(id="", kind="requirement", status="open", summary="x")
    with pytest.raises(ValueError):
        Requirement(id="r1", kind="", status="open", summary="x")
    with pytest.raises(ValueError):
        Requirement(id="r1", kind="requirement", status="", summary="x")


def test_a_link_points_at_something_it_knows_how_to_point_at():
    with pytest.raises(ValueError):
        RequirementLink(id="l1", requirement_id="r1", target_kind="wat", target_id="x")


def test_a_requirement_survives_a_restart(store):
    store.put(SCOPE, _requirement())

    found = store.search(RequirementQuery(scope=SCOPE))

    assert [item.id for item in found.items] == ["r1"]
    assert found.items[0].summary == "Refunds settle within a day"


def test_requirements_are_kept_apart_by_scope(store):
    other = Scope.from_mapping({"repository": "acme/shipping"})
    store.put(SCOPE, _requirement())

    assert store.search(RequirementQuery(scope=other)).total == 0


def test_requirements_can_be_narrowed_by_status(store):
    store.put(SCOPE, _requirement("r1", status="open"))
    store.put(SCOPE, _requirement("r2", status="met"))

    found = store.search(RequirementQuery(scope=SCOPE, statuses=frozenset({"met"})))

    assert [item.id for item in found.items] == ["r2"]


def test_a_requirement_can_be_found_by_where_it_came_from(store):
    store.put(
        SCOPE,
        Requirement(
            id="r1",
            kind="requirement",
            status="open",
            summary="x",
            external_ref="https://github.com/acme/billing/issues/7",
        ),
    )

    found = store.search(
        RequirementQuery(
            scope=SCOPE,
            external_refs=frozenset({"https://github.com/acme/billing/issues/7"}),
        )
    )

    assert [item.id for item in found.items] == ["r1"]


def test_a_link_needs_its_requirement(store):
    with pytest.raises(ValueError):
        store.put_link(
            SCOPE,
            RequirementLink(
                id="l1",
                requirement_id="missing",
                target_kind=CODE,
                target_id="src/a.py",
            ),
        )


def test_removing_a_requirement_takes_its_links(store):
    store.put(SCOPE, _requirement())
    store.put_link(
        SCOPE,
        RequirementLink(
            id="l1", requirement_id="r1", target_kind=CODE, target_id="src/a.py"
        ),
    )

    store.remove(SCOPE, "r1")

    assert store.get_links(SCOPE, frozenset()) == ()


def test_coverage_counts_what_a_requirement_is_linked_to(store):
    store.put(SCOPE, _requirement())
    store.put_link(
        SCOPE,
        RequirementLink(
            id="l1", requirement_id="r1", target_kind=CODE, target_id="src/refund.py"
        ),
    )
    store.put_link(
        SCOPE,
        RequirementLink(
            id="l2",
            requirement_id="r1",
            target_kind=TEST,
            target_id="tests/test_refund.py",
        ),
    )

    report = store.coverage(CoverageQuery(scope=SCOPE))

    assert report.items[0].code_links == 1
    assert report.items[0].test_links == 1
    assert report.items[0].covered is True
    assert report.items[0].tested is True


def test_coverage_names_what_has_code_but_no_test(store):
    store.put(SCOPE, _requirement())
    store.put_link(
        SCOPE,
        RequirementLink(
            id="l1", requirement_id="r1", target_kind=CODE, target_id="src/refund.py"
        ),
    )

    report = store.coverage(CoverageQuery(scope=SCOPE))

    assert report.untested == ("r1",)
    assert report.uncovered == ()


def test_coverage_names_what_nothing_implements(store):
    store.put(SCOPE, _requirement())

    report = store.coverage(CoverageQuery(scope=SCOPE))

    assert report.uncovered == ("r1",)


def test_coverage_can_be_asked_about_the_files_a_change_touches(store):
    store.put(SCOPE, _requirement("r1"))
    store.put(SCOPE, _requirement("r2"))
    store.put_link(
        SCOPE,
        RequirementLink(
            id="l1", requirement_id="r1", target_kind=CODE, target_id="src/refund.py"
        ),
    )

    report = store.coverage(
        CoverageQuery(scope=SCOPE, paths=frozenset({"src/refund.py"}))
    )

    assert [item.requirement_id for item in report.items] == ["r1"]


def test_a_change_touching_nothing_tracked_reports_nothing(store):
    store.put(SCOPE, _requirement())

    report = store.coverage(
        CoverageQuery(scope=SCOPE, paths=frozenset({"src/unrelated.py"}))
    )

    assert report.items == ()


def test_a_requirement_is_also_an_ordinary_knowledge_item(store):
    knowledge = InMemoryKnowledgeRepository()
    requirements = KnowledgeBackedRequirements(store, knowledge)

    requirements.put(SCOPE, _requirement())

    found = knowledge.search(
        KnowledgeQuery(scope=SCOPE, kinds=frozenset({"requirement"}))
    )
    assert [item.id for item in found.items] == ["requirement:r1"]
    assert found.items[0].summary == "Refunds settle within a day"


def test_linking_a_requirement_to_knowledge_connects_them(store):
    knowledge = InMemoryKnowledgeRepository()
    requirements = KnowledgeBackedRequirements(store, knowledge)
    requirements.put(SCOPE, _requirement())
    knowledge.put(
        SCOPE,
        KnowledgeObject(
            id="decision:1", kind="decision", status="approved", summary="Use UTC"
        ),
    )

    requirements.put_link(
        SCOPE,
        RequirementLink(
            id="l1",
            requirement_id="r1",
            target_kind=KNOWLEDGE,
            target_id="decision:1",
        ),
    )

    related = knowledge.get_relationships(
        SCOPE, frozenset({"requirement:r1", "decision:1"})
    )
    assert [item.type for item in related] == ["relates_to"]


def test_github_issues_become_requirements():
    issues = [
        {
            "number": 7,
            "title": "Refunds settle within a day",
            "state": "open",
            "html_url": "https://github.com/acme/billing/issues/7",
            "labels": [{"name": "requirement"}],
        },
        {
            "number": 9,
            "title": "Statements are downloadable",
            "state": "closed",
            "html_url": "https://github.com/acme/billing/issues/9",
            "labels": [{"name": "requirement"}],
        },
    ]
    source = GitHubIssueRequirements(lambda repository, labels: issues)

    found = source.sync(SCOPE)

    assert [item.id for item in found] == ["issue-7", "issue-9"]
    assert [item.status for item in found] == ["open", "met"]
    assert found[0].external_ref == "https://github.com/acme/billing/issues/7"


def test_a_source_needs_to_know_which_repository_to_read():
    source = GitHubIssueRequirements(lambda repository, labels: [])

    with pytest.raises(ValueError):
        source.sync(Scope.from_mapping({"project": "shop"}))


def test_an_issue_with_no_title_is_left_alone():
    source = GitHubIssueRequirements(
        lambda repository, labels: [{"number": 1, "title": ""}]
    )

    assert source.sync(SCOPE) == ()
