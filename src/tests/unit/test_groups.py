import pytest
from sqlalchemy import create_engine

from src.core.groups import (
    BREADTH,
    MAX_DEPTH,
    CheckedGroups,
    Group,
    GroupMember,
    GroupQuery,
    SQLGroupsRepository,
)
from src.core.requirements import (
    CODE,
    Requirement,
    RequirementLink,
    SQLRequirementsRepository,
    TEST,
)
from src.core.requirements.grouping import GroupableRequirements
from src.core.scope import Scope

WORKSPACE = Scope.from_mapping({"workspace": "acme"})
BILLING = Scope.from_mapping({"workspace": "acme", "repository": "acme/billing"})
PAYMENTS = Scope.from_mapping({"workspace": "acme", "repository": "acme/payments"})
ELSEWHERE = Scope.from_mapping({"workspace": "other"})


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'groups.db'}")
    return SQLGroupsRepository(engine, create_schema=True)


@pytest.fixture
def requirements(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'requirements.db'}")
    return SQLRequirementsRepository(engine, create_schema=True)


@pytest.fixture
def filing(store, requirements):
    return CheckedGroups(store, (GroupableRequirements(requirements),))


def _group(identity="refunds", name="Refunds", type="feature", parent_id=""):
    return Group(
        id=identity,
        type=type,
        status="open",
        name=name,
        parent_id=parent_id,
    )


def _requirement(identity, summary="Refunds settle within a business day"):
    return Requirement(id=identity, kind="requirement", status="open", summary=summary)


def test_a_group_holds_requirements_from_two_repositories(store, requirements):
    store.put(WORKSPACE, _group())
    requirements.put(BILLING, _requirement("r1"))
    requirements.put(PAYMENTS, _requirement("r2"))

    store.place(WORKSPACE, GroupMember("refunds", "requirement", "r1", BILLING))
    store.place(WORKSPACE, GroupMember("refunds", "requirement", "r2", PAYMENTS))

    held = store.members(WORKSPACE, frozenset({"refunds"}))
    assert [(one.member_id, one.member_scope) for one in held] == [
        ("r1", BILLING),
        ("r2", PAYMENTS),
    ]


def test_one_thing_can_be_in_several_groups(store, requirements):
    """A requirement every service answers to belongs to each of them.

    "Respond within a second" is not part of one project any more than of the
    next, and a rule that made it pick one would make every other project's
    numbers wrong.
    """
    store.put(WORKSPACE, _group("billing-v2", "Billing v2", type="project"))
    store.put(WORKSPACE, _group("payments", "Payments", type="project"))
    store.put(WORKSPACE, _group("srs-2-3", "2.3 Refunds", type="section"))
    requirements.put(
        BILLING, _requirement("speed", "Every service answers in a second")
    )

    for group in ("billing-v2", "payments", "srs-2-3"):
        store.place(WORKSPACE, GroupMember(group, "requirement", "speed", BILLING))

    assert store.filed_under(
        WORKSPACE, "requirement", frozenset({"speed"}), BILLING
    ) == {"speed": ("billing-v2", "payments", "srs-2-3")}
    for group in ("billing-v2", "payments", "srs-2-3"):
        assert [
            one.member_id for one in store.members(WORKSPACE, frozenset({group}))
        ] == ["speed"]


def test_filing_the_same_thing_in_the_same_group_twice_writes_it_once(
    store, requirements
):
    store.put(WORKSPACE, _group())
    requirements.put(BILLING, _requirement("r1"))

    store.place(WORKSPACE, GroupMember("refunds", "requirement", "r1", BILLING))
    store.place(WORKSPACE, GroupMember("refunds", "requirement", "r1", BILLING))

    assert len(store.members(WORKSPACE, frozenset({"refunds"}))) == 1


def test_taking_something_out_of_one_group_leaves_the_others_holding_it(
    store, requirements
):
    store.put(WORKSPACE, _group("billing-v2", "Billing v2", type="project"))
    store.put(WORKSPACE, _group("payments", "Payments", type="project"))
    requirements.put(BILLING, _requirement("speed"))
    for group in ("billing-v2", "payments"):
        store.place(WORKSPACE, GroupMember(group, "requirement", "speed", BILLING))

    assert (
        store.unfile(WORKSPACE, "billing-v2", "requirement", "speed", BILLING) is True
    )

    assert store.filed_under(
        WORKSPACE, "requirement", frozenset({"speed"}), BILLING
    ) == {"speed": ("payments",)}


def test_a_member_needs_its_group_in_the_same_scope(store, requirements):
    store.put(WORKSPACE, _group())
    requirements.put(BILLING, _requirement("r1"))

    with pytest.raises(ValueError, match="in the same scope"):
        store.place(ELSEWHERE, GroupMember("refunds", "requirement", "r1", BILLING))


def test_a_kind_nobody_owns_is_refused(filing, store):
    store.put(WORKSPACE, _group())

    with pytest.raises(ValueError, match="cannot be grouped"):
        filing.place(WORKSPACE, GroupMember("refunds", "invoice", "i1", BILLING))


def test_a_requirement_that_is_not_there_is_refused(filing, store):
    store.put(WORKSPACE, _group())

    with pytest.raises(ValueError, match="is not in that scope"):
        filing.place(WORKSPACE, GroupMember("refunds", "requirement", "r9", BILLING))


def test_ancestry_reads_outermost_first(store):
    store.put(WORKSPACE, _group("billing-v2", "Billing v2", type="project"))
    store.put(WORKSPACE, _group(parent_id="billing-v2"))
    store.put(WORKSPACE, _group("refund-api", "Refund API", "task", "refunds"))

    assert [one.id for one in store.ancestry(WORKSPACE, "refund-api")] == [
        "billing-v2",
        "refunds",
        "refund-api",
    ]


def test_a_group_refuses_what_its_columns_cannot_hold(store):
    """The same bounds on both surfaces.

    The HTTP layer checked them and the tools did not, so a tool could write a
    value that reached the database and failed there instead.
    """
    with pytest.raises(ValueError, match="type must contain at most 255"):
        _group(type="f" * 256)
    with pytest.raises(ValueError, match="external ref must contain at most 500"):
        Group("refunds", "feature", "open", "Refunds", "", "h" * 501)
    with pytest.raises(ValueError, match="type must contain at most 64"):
        GroupMember("refunds", "r" * 65, "r1", BILLING)
    with pytest.raises(ValueError, match="identity must contain at most 500"):
        GroupMember("refunds", "requirement", "r" * 501, BILLING)


def test_a_group_cannot_hold_itself():
    with pytest.raises(ValueError, match="cannot hold itself"):
        _group(parent_id="refunds")


def test_nesting_cannot_close_a_loop(store):
    store.put(WORKSPACE, _group("billing-v2", "Billing v2", type="project"))
    store.put(WORKSPACE, _group(parent_id="billing-v2"))

    with pytest.raises(ValueError, match="cycle"):
        store.put(WORKSPACE, _group("billing-v2", "Billing v2", "project", "refunds"))


def test_a_parent_outside_the_scope_is_refused(store):
    with pytest.raises(ValueError, match="parent has to be in the same scope"):
        store.put(WORKSPACE, _group(parent_id="nothing-like-it"))


def test_a_walk_deeper_than_the_cap_says_it_stopped(store, requirements):
    parent = ""
    for depth in range(MAX_DEPTH + 2):
        identity = f"level-{depth}"
        store.put(WORKSPACE, _group(identity, f"Level {depth}", parent_id=parent))
        parent = identity

    shallow = store.contents(WORKSPACE, "level-0", depth=2)
    assert shallow.truncated is True
    assert shallow.groups == ("level-1", "level-2")

    whole = store.contents(WORKSPACE, "level-0", depth=MAX_DEPTH + 5)
    assert whole.truncated is False
    assert len(whole.groups) == MAX_DEPTH + 1


def test_a_walk_says_when_it_answered_with_only_part_of_what_is_there(
    store, requirements
):
    """The cap is on the answer, not on the nesting.

    A rollup read off a cut answer reports less than is there, so the flag is
    the difference between a number and a wrong number.
    """
    store.put(WORKSPACE, _group())
    for index in range(3):
        identity = f"r{index}"
        requirements.put(BILLING, _requirement(identity))
        store.place(WORKSPACE, GroupMember("refunds", "requirement", identity, BILLING))
    for index in range(3):
        store.put(
            WORKSPACE, _group(f"part-{index}", f"Part {index}", "task", "refunds")
        )

    whole = store.contents(WORKSPACE, "refunds")
    assert (len(whole.members), len(whole.groups), whole.truncated) == (3, 3, False)

    members_cut = store.contents(WORKSPACE, "refunds", breadth=2)
    assert len(members_cut.members) == 2
    assert members_cut.truncated is True

    assert BREADTH >= 500


def test_a_shared_requirement_counts_in_every_group_that_holds_it(
    filing, store, requirements
):
    store.put(WORKSPACE, _group("billing-v2", "Billing v2", type="project"))
    store.put(WORKSPACE, _group("payments", "Payments", type="project"))
    requirements.put(BILLING, _requirement("speed"))
    requirements.put_link(BILLING, RequirementLink("l9", "speed", CODE, "src/app.py"))
    for group in ("billing-v2", "payments"):
        filing.place(WORKSPACE, GroupMember(group, "requirement", "speed", BILLING))

    rolled = {
        one.group_id: one
        for one in filing.rollup(WORKSPACE, frozenset({"billing-v2", "payments"}))
    }

    for group in ("billing-v2", "payments"):
        assert rolled[group].counts["requirement"] == {
            "total": 1,
            "covered": 1,
            "tested": 0,
        }


def test_a_rollup_sums_a_group_and_what_is_nested_in_it(filing, store, requirements):
    store.put(WORKSPACE, _group("billing-v2", "Billing v2", type="project"))
    store.put(WORKSPACE, _group(parent_id="billing-v2"))

    for identity in ("r1", "r2", "r3"):
        requirements.put(BILLING, _requirement(identity))
    requirements.put(PAYMENTS, _requirement("r4"))

    requirements.put_link(BILLING, RequirementLink("l1", "r1", CODE, "src/refund.py"))
    requirements.put_link(
        BILLING, RequirementLink("l2", "r1", TEST, "tests/test_refund.py")
    )
    requirements.put_link(BILLING, RequirementLink("l3", "r2", CODE, "src/ledger.py"))

    filing.place(WORKSPACE, GroupMember("billing-v2", "requirement", "r3", BILLING))
    filing.place(WORKSPACE, GroupMember("refunds", "requirement", "r1", BILLING))
    filing.place(WORKSPACE, GroupMember("refunds", "requirement", "r2", BILLING))
    filing.place(WORKSPACE, GroupMember("refunds", "requirement", "r4", PAYMENTS))

    rolled = filing.rollup(WORKSPACE, frozenset({"billing-v2", "refunds"}))
    summed = {one.group_id: one for one in rolled}

    assert summed["refunds"].counts["requirement"] == {
        "total": 3,
        "covered": 2,
        "tested": 1,
    }
    assert summed["refunds"].descendants == 0
    assert summed["billing-v2"].counts["requirement"] == {
        "total": 4,
        "covered": 2,
        "tested": 1,
    }
    assert summed["billing-v2"].descendants == 1


def test_another_workspace_sees_none_of_it(store, requirements):
    store.put(WORKSPACE, _group())
    requirements.put(BILLING, _requirement("r1"))
    store.place(WORKSPACE, GroupMember("refunds", "requirement", "r1", BILLING))

    assert store.search(GroupQuery(scope=ELSEWHERE)).items == ()
    assert store.members(ELSEWHERE, frozenset({"refunds"})) == ()
    assert store.filed_under(ELSEWHERE, "requirement", frozenset({"r1"}), BILLING) == {}


def test_the_outermost_groups_can_be_asked_for_on_their_own(store):
    store.put(WORKSPACE, _group("billing-v2", "Billing v2", type="project"))
    store.put(WORKSPACE, _group(parent_id="billing-v2"))

    found = store.search(GroupQuery(scope=WORKSPACE, parent_ids=frozenset({""})))
    assert [one.id for one in found.items] == ["billing-v2"]
    assert found.total == 1


def test_a_group_that_still_holds_things_cannot_be_removed(store, requirements):
    store.put(WORKSPACE, _group())
    requirements.put(BILLING, _requirement("r1"))
    store.place(WORKSPACE, GroupMember("refunds", "requirement", "r1", BILLING))

    with pytest.raises(ValueError, match="still holds things"):
        store.remove(WORKSPACE, "refunds")

    assert store.unfile(WORKSPACE, "refunds", "requirement", "r1", BILLING) is True
    store.remove(WORKSPACE, "refunds")
    assert store.search(GroupQuery(scope=WORKSPACE)).items == ()


def test_a_group_holding_a_group_cannot_be_removed(store):
    store.put(WORKSPACE, _group("billing-v2", "Billing v2", type="project"))
    store.put(WORKSPACE, _group(parent_id="billing-v2"))

    with pytest.raises(ValueError, match="still holds things"):
        store.remove(WORKSPACE, "billing-v2")
