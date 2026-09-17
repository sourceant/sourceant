"""Somewhere to keep what can be worked out again."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from src.core.cache import keyed
from src.core.cache.sql import SQLCache
from src.models.cache_entry import CacheEntry
from src.models.code_review import CodeReview, Verdict
from src.llms.litellm_provider import LiteLLMProvider

RESPONSES = "model-response"


@pytest.fixture
def kept(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'cache.db'}")
    SQLModel.metadata.create_all(engine, tables=[CacheEntry.__table__])
    with patch("src.core.cache.sql.get_engine", return_value=engine):
        yield engine


@pytest.fixture
def reuses(monkeypatch):
    """This suite works everything out; a test about the cache says otherwise."""
    from src.core.settings.resolver import value_of as settled

    monkeypatch.setattr(
        "src.core.settings.value_of",
        lambda key, **scopes: (
            1 if key == "review.reuse_responses_days" else settled(key, **scopes)
        ),
    )


def _answered(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10),
        _hidden_params={},
    )


class TestTheStoreItself:
    def test_a_value_comes_back_the_way_it_went_in(self, kept):
        SQLCache().set("anything", "k", "what it held", ttl=3600)

        assert SQLCache().get("anything", "k") == "what it held"

    def test_a_key_nothing_has_written_is_a_miss(self, kept):
        assert SQLCache().get("anything", "never written") is None

    def test_two_namespaces_do_not_read_each_other(self, kept):
        """The point of a shared cache is that sharing it is safe."""
        cache = SQLCache()
        cache.set("one", "k", "first", ttl=3600)
        cache.set("two", "k", "second", ttl=3600)

        assert cache.get("one", "k") == "first"
        assert cache.get("two", "k") == "second"

    def test_no_time_to_live_keeps_nothing(self, kept):
        SQLCache().set("anything", "k", "what it held", ttl=0)

        assert SQLCache().get("anything", "k") is None

    def test_an_entry_past_its_time_is_not_served(self, kept):
        import sqlalchemy as sa
        from datetime import timedelta

        cache = SQLCache()
        cache.set("anything", "k", "what it held", ttl=3600)
        table = CacheEntry.__table__
        with kept.begin() as connection:
            now = connection.execute(
                sa.select(sa.func.current_timestamp(type_=sa.DateTime))
            ).scalar()
            connection.execute(
                sa.update(table)
                .where(table.c.key == "k")
                .values(expires_at=now - timedelta(seconds=1))
            )

        assert cache.get("anything", "k") is None

    def test_writing_again_replaces_what_was_kept(self, kept):
        cache = SQLCache()
        cache.set("anything", "k", "first", ttl=3600)
        cache.set("anything", "k", "second", ttl=3600)

        assert cache.get("anything", "k") == "second"

    def test_an_entry_can_be_forgotten(self, kept):
        cache = SQLCache()
        cache.set("anything", "k", "what it held", ttl=3600)
        cache.forget("anything", "k")

        assert cache.get("anything", "k") is None

    def test_nowhere_to_keep_it_is_not_a_failure(self):
        with patch("src.core.cache.sql.get_engine", return_value=None):
            SQLCache().set("anything", "k", "what it held", ttl=3600)

            assert SQLCache().get("anything", "k") is None

    def test_a_store_that_cannot_be_reached_is_not_a_failure(self):
        with patch("src.core.cache.sql.get_engine", side_effect=RuntimeError("no")):
            SQLCache().set("anything", "k", "v", ttl=3600)

            assert SQLCache().get("anything", "k") is None


class TestNamingOneThing:
    def test_the_same_parts_name_the_same_thing(self):
        assert keyed("a", "b") == keyed("a", "b")

    def test_different_parts_name_different_things(self):
        assert keyed("a", "b") != keyed("a", "c")

    def test_the_parts_cannot_be_run_together(self):
        """ "ab" and "" must not name what "a" and "b" name."""
        assert keyed("a", "b") != keyed("ab", "")

    def test_a_key_is_short_however_long_its_parts(self):
        assert len(keyed("x" * 100_000)) == 64


class TestARunThatFailedLate:
    """The reason this exists: the retry must not re-read the change."""

    def _model(self):
        return LiteLLMProvider(
            model="gemini/gemini-2.5-flash",
            token_limit=1000,
            attribution={"repository": "acme/web"},
        )

    def test_the_review_is_not_asked_for_twice(self, kept, reuses):
        review = CodeReview(verdict=Verdict.COMMENT, code_suggestions=[])
        model = self._model()

        with patch(
            "litellm.completion", return_value=_answered(review.model_dump_json())
        ) as asked:
            first = model.generate_code_review("- old\n+ new")
            second = model.generate_code_review("- old\n+ new")

        assert first is not None and second is not None
        assert first.verdict == second.verdict
        assert asked.call_count == 1

    def test_a_different_change_is_still_read(self, kept, reuses):
        review = CodeReview(verdict=Verdict.COMMENT, code_suggestions=[])
        model = self._model()

        with patch(
            "litellm.completion", return_value=_answered(review.model_dump_json())
        ) as asked:
            model.generate_code_review("- old\n+ new")
            model.generate_code_review("- old\n+ something else")

        assert asked.call_count == 2

    def test_another_account_does_not_read_this_ones_answer(self, kept, reuses):
        review = CodeReview(verdict=Verdict.COMMENT, code_suggestions=[])
        theirs = LiteLLMProvider(
            model="gemini/gemini-2.5-flash",
            token_limit=1000,
            attribution={"repository": "other/app"},
        )

        with patch(
            "litellm.completion", return_value=_answered(review.model_dump_json())
        ) as asked:
            self._model().generate_code_review("- old\n+ new")
            theirs.generate_code_review("- old\n+ new")

        assert asked.call_count == 2

    def test_a_reused_answer_is_not_recorded_as_a_second_call(self, kept, reuses):
        """Nothing was consumed, so nothing may be billed for."""
        review = CodeReview(verdict=Verdict.COMMENT, code_suggestions=[])
        model = self._model()

        with patch("src.core.usage.record_completion") as recorded:
            with patch(
                "litellm.completion", return_value=_answered(review.model_dump_json())
            ):
                model.generate_code_review("- old\n+ new")
                model.generate_code_review("- old\n+ new")

        assert recorded.call_count == 1

    def test_the_comparison_that_failed_the_run_is_kept_too(self, kept, reuses):
        model = self._model()

        with patch("litellm.completion", return_value=_answered("DIFFERENT")) as asked:
            assert model.is_summary_different("a", "b") is True
            assert model.is_summary_different("a", "b") is True

        assert asked.call_count == 1

    def test_a_cache_that_is_broken_still_lets_the_review_run(self, reuses):
        review = CodeReview(verdict=Verdict.COMMENT, code_suggestions=[])

        with patch("src.core.cache.sql.get_engine", side_effect=RuntimeError("no")):
            with patch(
                "litellm.completion", return_value=_answered(review.model_dump_json())
            ):
                assert self._model().generate_code_review("- old\n+ new") is not None


class TestACacheThatWentAway:
    """One bad minute must not cost every review until a restart."""

    @staticmethod
    def _refusing(monkeypatch, calls):
        import sys, types

        def refuse(*args, **kwargs):
            calls.append(1)
            raise ConnectionError("no route to host")

        monkeypatch.setitem(sys.modules, "redis", types.SimpleNamespace(Redis=refuse))

    def test_an_unreachable_cache_is_not_dialled_on_every_read(self, monkeypatch):
        from src.core.cache.redis import RedisCache

        calls = []
        self._refusing(monkeypatch, calls)
        cache = RedisCache(cooldown=60)

        assert cache.get("anything", "k") is None
        assert cache.get("anything", "k") is None
        assert cache.get("anything", "k") is None
        assert len(calls) == 1

    def test_it_is_tried_again_once_the_cooldown_passes(self, monkeypatch):
        from src.core.cache import redis as module

        now = [0.0]
        monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
        calls = []
        self._refusing(monkeypatch, calls)
        cache = module.RedisCache(cooldown=60)

        assert cache.get("anything", "k") is None
        assert len(calls) == 1

        now[0] = 30.0
        assert cache.get("anything", "k") is None
        assert len(calls) == 1

        now[0] = 61.0
        assert cache.get("anything", "k") is None
        assert len(calls) == 2

    def test_a_cache_that_came_back_is_used_again(self, monkeypatch):
        import sys, types
        from src.core.cache import redis as module

        held = {}

        class Working:
            def __init__(self, **kwargs):
                pass

            def ping(self):
                return True

            def get(self, key):
                return held.get(key)

            def setex(self, key, ttl, value):
                held[key] = value

        now = [0.0]
        monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
        calls = []
        self._refusing(monkeypatch, calls)
        cache = module.RedisCache(cooldown=60)
        assert cache.get("anything", "k") is None

        monkeypatch.setitem(sys.modules, "redis", types.SimpleNamespace(Redis=Working))
        now[0] = 1000.0

        cache.set("anything", "k", "back again", ttl=3600)
        assert cache.get("anything", "k") == "back again"


class TestKeepingASearchRound:
    """The search loop is a third of what a review spends."""

    @staticmethod
    def _round(name="search_code", arguments='{"terms": ["cache"]}'):
        from litellm import ModelResponse

        return ModelResponse(
            choices=[
                {
                    "message": {
                        "role": "assistant",
                        "content": "looking",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": name, "arguments": arguments},
                            }
                        ],
                    }
                }
            ],
            usage={
                "prompt_tokens": 5000,
                "completion_tokens": 40,
                "total_tokens": 5040,
            },
        )

    def _model(self):
        return LiteLLMProvider(
            model="gemini/gemini-2.5-flash",
            token_limit=1000,
            attribution={"repository": "acme/web"},
        )

    def _asked(self, model):
        return model.ask_with_tools(
            [{"role": "user", "content": "what should I look at"}],
            [{"type": "function", "function": {"name": "search_code"}}],
            purpose="review_search",
        )

    def test_the_same_round_is_not_asked_twice(self, kept, reuses):
        model = self._model()

        with patch("litellm.completion", return_value=self._round()) as asked:
            first = self._asked(model)
            second = self._asked(model)

        assert asked.call_count == 1
        assert first == second
        assert first["tool_calls"][0]["name"] == "search_code"
        assert first["content"] == "looking"

    def test_a_different_conversation_is_asked(self, kept, reuses):
        model = self._model()

        with patch("litellm.completion", return_value=self._round()) as asked:
            self._asked(model)
            model.ask_with_tools(
                [{"role": "user", "content": "something else entirely"}],
                [{"type": "function", "function": {"name": "search_code"}}],
                purpose="review_search",
            )

        assert asked.call_count == 2

    def test_a_different_tool_set_is_asked(self, kept, reuses):
        """The tools decide the answer as much as the conversation does."""
        model = self._model()
        messages = [{"role": "user", "content": "what should I look at"}]

        with patch("litellm.completion", return_value=self._round()) as asked:
            model.ask_with_tools(
                messages, [{"type": "function", "function": {"name": "search_code"}}]
            )
            model.ask_with_tools(
                messages, [{"type": "function", "function": {"name": "read_file"}}]
            )

        assert asked.call_count == 2

    def test_requiring_a_tool_is_a_different_question(self, kept, reuses):
        model = self._model()
        messages = [{"role": "user", "content": "what should I look at"}]
        tools = [{"type": "function", "function": {"name": "search_code"}}]

        with patch("litellm.completion", return_value=self._round()) as asked:
            model.ask_with_tools(messages, tools, require=False)
            model.ask_with_tools(messages, tools, require=True)

        assert asked.call_count == 2

    def test_something_kept_that_is_not_a_round_is_not_served(self, kept, reuses):
        from src.core.cache import cache
        from src.llms.litellm_provider import _is_a_round

        assert _is_a_round('{"content": "", "tool_calls": []}') is True
        assert _is_a_round("not json at all") is False
        assert _is_a_round('{"something": "else"}') is False


class TestNothingBrokenIsKept:
    """A kept answer that cannot be read is served until it expires."""

    def _model(self):
        return LiteLLMProvider(
            model="gemini/gemini-2.5-flash",
            token_limit=1000,
            attribution={"repository": "acme/web"},
        )

    def test_a_summary_that_will_not_parse_is_not_kept(self, kept, reuses):
        from src.models.code_review import CodeSuggestion, Side, SuggestionCategory

        said = [
            CodeSuggestion(
                file_name="a.py",
                start_line=1,
                end_line=1,
                side=Side.RIGHT,
                comment="something",
                category=SuggestionCategory.BUG,
                suggested_code="x",
            )
        ]

        with patch("litellm.completion", return_value=_answered("not json at all")):
            with pytest.raises(Exception):
                self._model().generate_summary(said)

        assert _rows(kept) == 0

    def test_a_comparison_that_reads_as_neither_answer_is_not_kept(self, kept, reuses):
        model = self._model()

        with patch(
            "litellm.completion", return_value=_answered("¯\\_(ツ)_/¯")
        ) as asked:
            model.is_summary_different("a", "b")
            model.is_summary_different("a", "b")

        assert asked.call_count == 2

    def test_a_comparison_that_answers_properly_is_kept(self, kept, reuses):
        model = self._model()

        with patch("litellm.completion", return_value=_answered("SAME")) as asked:
            assert model.is_summary_different("a", "b") is False
            assert model.is_summary_different("a", "b") is False

        assert asked.call_count == 1


def _rows(engine):
    import sqlalchemy as sa

    with engine.connect() as c:
        return c.execute(sa.text("select count(*) from cache_entries")).scalar()


def _only_key(engine):
    import sqlalchemy as sa

    with engine.connect() as c:
        return c.execute(sa.text("select key from cache_entries limit 1")).scalar()
