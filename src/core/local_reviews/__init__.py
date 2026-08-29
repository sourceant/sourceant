"""Reviews of work in a checkout, kept so a link to one still works.

A review is asked for by one thing and read by another. An agent runs one over
MCP while somebody is in the middle of something else, and hands them a link;
the link has to open the same review an hour later. Holding it in the memory of
whichever process happened to run it does not survive that, or a restart.
"""

from .models import DONE, FAILED, RUNNING, LocalReview, named
from .sql import SQLLocalReviewStore

__all__ = [
    "DONE",
    "FAILED",
    "RUNNING",
    "LocalReview",
    "SQLLocalReviewStore",
    "named",
]
