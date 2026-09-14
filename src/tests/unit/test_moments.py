"""A moment the API says is a moment a reader can place."""

from datetime import datetime, timedelta, timezone

from src.utils.moments import utc


def test_a_naive_moment_is_said_to_be_utc():
    assert utc(datetime(2026, 9, 14, 18, 9, 24)) == "2026-09-14T18:09:24+00:00"


def test_an_aware_moment_keeps_the_instant_it_names():
    somewhere = timezone(timedelta(hours=1))

    assert utc(datetime(2026, 9, 14, 19, 9, 24, tzinfo=somewhere)) == (
        "2026-09-14T18:09:24+00:00"
    )


def test_nothing_stays_nothing():
    assert utc(None) is None


def test_a_reader_placing_it_locally_reads_the_same_instant():
    said = utc(datetime(2026, 9, 14, 18, 9, 24))

    assert datetime.fromisoformat(said) == datetime(
        2026, 9, 14, 18, 9, 24, tzinfo=timezone.utc
    )
