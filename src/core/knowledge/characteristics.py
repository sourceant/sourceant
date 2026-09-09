from enum import Enum


class KnowledgeImportance(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def priority(self) -> int:
        return tuple(type(self)).index(self)


class KnowledgeBasis(str, Enum):
    STATEMENT = "statement"
    REPOSITORY_ANALYSIS = "repository_analysis"
    ABSENCE_OBSERVATION = "absence_observation"


class KnowledgeApplicability(str, Enum):
    SCOPE = "scope"
    TARGETS = "targets"
