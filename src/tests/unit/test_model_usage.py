"""What a model call consumed is recorded against whoever it was made for."""

from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from src.core.model.settings import Choice, SettingsModelSource
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
    assert kept[0].repository == "sourceant/cli"
    assert kept[0].input_tokens == 120
    assert kept[0].output_tokens == 30
    assert kept[0].cost == 0.0004
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
