"""A moment as the API says it, with the timezone it is actually in.

Timestamps are stored naive, in UTC, because SQLite hands back a naive datetime
and Postgres an aware one, and comparing the two raises. A naive one written out
carries no offset, and a reader takes that for its own local time, so a moment
reads as however far that reader is from UTC.
"""

from datetime import datetime, timezone
from typing import Optional


def utc(value: Optional[datetime]) -> Optional[str]:
    """The moment in ISO 8601, said to be UTC."""
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc).isoformat()
