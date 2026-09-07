"""Reusing a generated review, without a second thing running to hold it."""

from datetime import datetime

import pytest
import sqlalchemy as sa

from src.config.db import get_engine
from src.models.cached_review import CachedReview
from src.tests.base_test import BaseTestCase
from src.utils import review_cache

REPO = "sourceant/sourceant"
REVIEW = {"summary": "looks fine", "comments": []}


def _age(key: str) -> None:
    """Move a kept review's deadline into the past, as time would."""
    table = CachedReview.__table__
    with get_engine().begin() as connection:
        connection.execute(
            sa.update(table)
            .where(table.c.key == key)
            .values(expires_at=datetime(2000, 1, 1))
        )


class TestReviewCache(BaseTestCase):

    @pytest.fixture(autouse=True)
    def kept_in_the_database(self, monkeypatch):
        monkeypatch.setattr(review_cache, "REVIEW_CACHE", "database")

    def test_a_review_is_served_again_for_the_same_revision(self):
        review_cache.save_review(REPO, 1, "abc123", REVIEW)

        assert review_cache.get_review(REPO, 1, "abc123") == REVIEW

    def test_a_new_revision_misses(self):
        review_cache.save_review(REPO, 2, "abc123", REVIEW)

        assert review_cache.get_review(REPO, 2, "def456") is None

    def test_reviewing_the_same_revision_again_replaces_what_was_kept(self):
        review_cache.save_review(REPO, 3, "abc123", REVIEW)
        review_cache.save_review(REPO, 3, "abc123", {"summary": "second look"})

        assert review_cache.get_review(REPO, 3, "abc123") == {"summary": "second look"}

    def test_nothing_is_kept_when_reuse_is_turned_off(self, monkeypatch):
        monkeypatch.setattr(review_cache, "_ttl_seconds", lambda repo: 0)

        review_cache.save_review(REPO, 4, "abc123", REVIEW)

        assert review_cache.get_review(REPO, 4, "abc123") is None

    def test_a_review_that_has_outlived_its_reuse_is_not_served(self, monkeypatch):
        monkeypatch.setattr(review_cache, "_ttl_seconds", lambda repo: 1)
        review_cache.save_review(REPO, 5, "abc123", REVIEW)
        assert review_cache.get_review(REPO, 5, "abc123") == REVIEW

        _age(review_cache._key(REPO, 5, "abc123"))

        assert review_cache.get_review(REPO, 5, "abc123") is None

    def test_two_reviews_of_one_revision_arriving_at_once_keep_one(self):
        key = review_cache._key(REPO, 6, "abc123")
        review_cache.save_review(REPO, 6, "abc123", REVIEW)

        with get_engine().begin() as connection:
            review_cache._first(
                connection,
                key,
                datetime(2030, 1, 1),
                {
                    "payload": '{"summary": "the other one"}',
                    "expires_at": datetime(2030, 1, 2),
                    "updated_at": datetime(2030, 1, 1),
                },
            )

        assert review_cache.get_review(REPO, 6, "abc123") == {
            "summary": "the other one"
        }
