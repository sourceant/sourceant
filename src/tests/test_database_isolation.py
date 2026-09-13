"""The suite must not be able to reach a database somebody is using."""

import os


def test_the_suite_binds_to_the_test_database_not_an_inherited_one():
    """Chosen at collection: a fixture runs after the application has read it."""
    from src.config.settings import DATABASE_URL

    intended = os.environ.get("TEST_DATABASE_URL", "sqlite:///./sourceant.db")

    assert DATABASE_URL == intended
