"""What a model call consumed is recorded against whoever it was made for."""

from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from src.core.model.settings import Choice, SettingsModelSource
from src.core.usage import record_completion
from src.llms.litellm_provider import LiteLLMProvider
from src.models.model_usage import ModelUsageRecord


def _answered(prompt_tokens=120, completion_tokens=30, cost=0.0004):
    """A completion shaped the way the provider returns one."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="an answer"))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        ),
        _hidden_params={"response_cost": cost},
    )


def _kept(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'usage.db'}")
    SQLModel.metadata.create_all(engine, tables=[ModelUsageRecord.__table__])
    return engine


def test_a_call_records_what_it_consumed_and_for_whom(tmp_path):
    engine = _kept(tmp_path)
    provider = LiteLLMProvider(
        model="gemini/gemini-2.5-flash",
        token_limit=1000,
        attribution={"repository": "sourceant/cli", "organization": None, "user": None},
    )

    with patch("src.core.usage.sql.get_engine", return_value=engine):
        with patch("litellm.completion", return_value=_answered()):
            assert provider.generate_text("anything") == "an answer"

    with Session(engine) as session:
        kept = session.exec(select(ModelUsageRecord)).all()

    assert len(kept) == 1
    assert kept[0].model == "gemini/gemini-2.5-flash"
    assert (kept[0].owner_type, kept[0].owner_id) == ("repository", "sourceant/cli")
    assert kept[0].input_tokens == 120
    assert kept[0].output_tokens == 30
    assert kept[0].cost_micro == 400
    assert kept[0].purpose == "text"


def test_recording_nowhere_to_keep_it_does_not_fail_the_call(tmp_path):
    """The answer is already on its way back, so a failed write must not raise."""
    provider = LiteLLMProvider(model="m", token_limit=10)

    with patch(
        "src.core.usage.sql.get_engine", side_effect=RuntimeError("no database")
    ):
        with patch("litellm.completion", return_value=_answered()):
            assert provider.generate_text("anything") == "an answer"


def test_two_repositories_are_not_billed_to_one(tmp_path):
    """A provider kept for one scope must not report later calls as that one."""
    source = SettingsModelSource()

    with patch.object(
        SettingsModelSource,
        "choice_for",
        return_value=Choice(name="m", token_limit=10),
    ):
        first = source.model_for(repository="one/a")
        second = source.model_for(repository="two/b")

    assert first is not second
    assert first._attribution["repository"] == "one/a"
    assert second._attribution["repository"] == "two/b"


def test_a_provider_that_names_the_counts_differently_is_still_read(tmp_path):
    """The raw Anthropic client reports input_tokens and output_tokens."""
    engine = _kept(tmp_path)
    answered = SimpleNamespace(usage=SimpleNamespace(input_tokens=90, output_tokens=15))

    with patch("src.core.usage.sql.get_engine", return_value=engine):
        record_completion(
            answered, model="anthropic/one", purpose="extraction", repository="a/b"
        )

    with Session(engine) as session:
        kept = session.exec(select(ModelUsageRecord)).all()

    assert len(kept) == 1
    assert kept[0].input_tokens == 90
    assert kept[0].output_tokens == 15
    assert (kept[0].owner_type, kept[0].owner_id) == ("repository", "a/b")
    assert kept[0].cost_micro is None


def test_an_answer_that_reports_nothing_is_not_a_call_that_cost_nothing(tmp_path):
    """A row of zeroes would read as a free call, which is a different claim."""
    engine = _kept(tmp_path)

    with patch("src.core.usage.sql.get_engine", return_value=engine):
        record_completion(SimpleNamespace(), model="m", purpose="p")
        record_completion(
            SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0)
            ),
            model="m",
            purpose="p",
        )

    with Session(engine) as session:
        assert session.exec(select(ModelUsageRecord)).all() == []


def test_a_provider_that_reports_only_a_total_is_still_recorded(tmp_path):
    """litellm builds a Usage with a total and no split, and it still cost money."""
    from litellm.types.utils import ModelResponse, Usage

    engine = _kept(tmp_path)
    answered = ModelResponse(usage=Usage(total_tokens=4321))
    answered._hidden_params = {"response_cost": 0.0731}

    with patch("src.core.usage.sql.get_engine", return_value=engine):
        record_completion(answered, model="m", purpose="review")

    with Session(engine) as session:
        kept = session.exec(select(ModelUsageRecord)).all()

    assert len(kept) == 1
    assert kept[0].reported_total == 4321
    assert kept[0].cost_micro == 73_100


def test_a_real_answer_is_read_the_way_the_provider_builds_it(tmp_path):
    """Every other fixture here is hand made, so one is built by litellm itself."""
    from litellm.types.utils import ModelResponse, Usage

    engine = _kept(tmp_path)
    answered = ModelResponse(usage=Usage(prompt_tokens=120, completion_tokens=30))

    with patch("src.core.usage.sql.get_engine", return_value=engine):
        record_completion(answered, model="m", purpose="review", repository="a/b")

    with Session(engine) as session:
        kept = session.exec(select(ModelUsageRecord)).all()

    assert (kept[0].input_tokens, kept[0].output_tokens) == (120, 30)
    assert (kept[0].owner_type, kept[0].owner_id) == ("repository", "a/b")


def test_an_answer_it_cannot_read_does_not_destroy_the_answer(tmp_path):
    """The call already happened, so nothing about counting it may raise.

    The counts arrive as whatever the provider put there. Read outside a guard,
    a shape it did not expect turned an answer the model had already produced
    into a model error.
    """
    engine = _kept(tmp_path)
    provider = LiteLLMProvider(model="m", token_limit=10)
    unreadable = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="an answer"))],
        usage=SimpleNamespace(prompt_tokens=lambda: 1, completion_tokens=2),
        _hidden_params=SimpleNamespace(),
    )

    with patch("src.core.usage.sql.get_engine", return_value=engine):
        with patch("litellm.completion", return_value=unreadable):
            assert provider.generate_text("anything") == "an answer"

    with Session(engine) as session:
        kept = session.exec(select(ModelUsageRecord)).all()

    # What could be read is kept, and what could not is left at nothing rather
    # than guessed at.
    assert [(one.input_tokens, one.output_tokens, one.cost_micro) for one in kept] == [
        (0, 2, None)
    ]


def test_the_provider_is_kept_apart_from_the_model(tmp_path):
    """Asking what one service cost should not mean parsing a model name."""
    from litellm.types.utils import ModelResponse, Usage

    engine = _kept(tmp_path)
    answered = ModelResponse(usage=Usage(prompt_tokens=1, completion_tokens=1))

    with patch("src.core.usage.sql.get_engine", return_value=engine):
        record_completion(answered, model="gemini/gemini-2.5-flash", purpose="review")

    with Session(engine) as session:
        kept = session.exec(select(ModelUsageRecord)).all()

    assert kept[0].provider == "gemini"
    assert kept[0].model == "gemini/gemini-2.5-flash"


def test_money_is_kept_whole(tmp_path):
    """A bill is a sum of many rows, and a float drifts as they add up."""
    from src.core.usage import ModelUsage

    assert ModelUsage.micro(0.0731) == 73_100
    assert ModelUsage.micro(0.0000001) == 0
    assert ModelUsage.micro(None) is None


def test_a_workspace_owes_for_it_and_the_repository_is_what_it_was_about():
    """Naming the owner by kind is what lets the kind change later."""
    from src.core.usage import ModelUsage

    assert ModelUsage.owed_by(repository="a/b") == ("repository", "a/b", None, None)
    assert ModelUsage.owed_by(workspace="73", repository="a/b") == (
        "workspace",
        "73",
        "repository",
        "a/b",
    )
    assert ModelUsage.owed_by(organization="acme", repository="a/b") == (
        "organization",
        "acme",
        "repository",
        "a/b",
    )
    assert ModelUsage.owed_by() == (None, None, None, None)
