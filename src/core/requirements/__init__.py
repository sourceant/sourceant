from .github import GitHubIssueRequirements
from .interfaces import (
    RequirementsReader,
    RequirementsRepository,
    RequirementsSource,
    RequirementsWriter,
)
from .knowledge import KnowledgeBackedRequirements, as_knowledge, knowledge_id
from .models import (
    CODE,
    KNOWLEDGE,
    TARGET_KINDS,
    TEST,
    TOPOLOGY,
    CoverageQuery,
    CoverageReport,
    Requirement,
    RequirementCoverage,
    RequirementLink,
    RequirementQuery,
    RequirementResult,
)
from .sql import SQLRequirementsRepository

__all__ = [
    "CODE",
    "KNOWLEDGE",
    "TARGET_KINDS",
    "TEST",
    "TOPOLOGY",
    "CoverageQuery",
    "CoverageReport",
    "GitHubIssueRequirements",
    "KnowledgeBackedRequirements",
    "Requirement",
    "RequirementCoverage",
    "RequirementLink",
    "RequirementQuery",
    "RequirementResult",
    "RequirementsReader",
    "RequirementsRepository",
    "RequirementsSource",
    "RequirementsWriter",
    "SQLRequirementsRepository",
    "as_knowledge",
    "knowledge_id",
]
