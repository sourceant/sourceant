"""Every table model, imported so the mapper can resolve them by name.

A relationship names its other side as a string, which SQLAlchemy resolves
against whatever has been imported. Registering them all here means importing
any one model is enough for the mapper to find the other.
"""

from src.models.code_review import CodeReview
from src.models.config import Config
from src.models.connected_repository import ConnectedRepository
from src.models.pull_request import PullRequest
from src.models.repository import Repository
from src.models.repository_event import RepositoryEvent
from src.models.review_record import ReviewRecord
from src.models.token_usage import TokenUsageRecord
from src.models.workspace import Workspace

__all__ = [
    "CodeReview",
    "Config",
    "ConnectedRepository",
    "PullRequest",
    "Repository",
    "RepositoryEvent",
    "ReviewRecord",
    "TokenUsageRecord",
    "Workspace",
]
