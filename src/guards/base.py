from dataclasses import dataclass
from enum import Enum
from typing import Optional

from src.models.code_review import CodeReview


class GuardAction(Enum):
    ALLOW = "allow"
    BLOCK = "block"


@dataclass
class GuardResult:
    action: GuardAction
    review: Optional[CodeReview] = None
    reason: str = ""
