from __future__ import annotations

from src.core.knowledge import KnowledgeObject, KnowledgeRelationship, KnowledgeWriter
from src.core.scope import Scope

from .models import CODE, KNOWLEDGE, TEST, TOPOLOGY, Requirement, RequirementLink

KIND = "requirement"

RELATIONSHIP_TYPES = {
    CODE: "implemented_by",
    TEST: "verified_by",
    KNOWLEDGE: "relates_to",
    TOPOLOGY: "delivered_by",
}


def as_knowledge(requirement: Requirement) -> KnowledgeObject:
    properties = dict(requirement.properties)
    properties["requirement_kind"] = requirement.kind
    if requirement.external_ref:
        properties["external_ref"] = requirement.external_ref
    return KnowledgeObject(
        id=knowledge_id(requirement.id),
        kind=KIND,
        status=requirement.status,
        summary=requirement.summary,
        properties=properties,
    )


def as_knowledge_relationship(link: RequirementLink) -> KnowledgeRelationship:
    return KnowledgeRelationship(
        id=f"requirement-link:{link.id}",
        source_id=knowledge_id(link.requirement_id),
        target_id=link.target_id,
        type=RELATIONSHIP_TYPES.get(link.target_kind, "relates_to"),
        properties=dict(link.properties),
    )


def knowledge_id(requirement_id: str) -> str:
    return f"requirement:{requirement_id}"


class KnowledgeBackedRequirements:
    """Requirements that also answer to the knowledge tools.

    A requirement is written twice: once where requirements are queried and
    linked, and once as an ordinary knowledge item, so an agent that only knows
    about knowledge still finds it.
    """

    def __init__(self, requirements, knowledge: KnowledgeWriter) -> None:
        self._requirements = requirements
        self._knowledge = knowledge

    def put(self, scope: Scope, requirement: Requirement) -> None:
        self._requirements.put(scope, requirement)
        self._knowledge.put(scope, as_knowledge(requirement))

    def put_link(self, scope: Scope, link: RequirementLink) -> None:
        self._requirements.put_link(scope, link)
        if link.target_kind == KNOWLEDGE:
            self._knowledge.put_relationship(scope, as_knowledge_relationship(link))

    def remove(self, scope: Scope, requirement_id: str) -> None:
        self._requirements.remove(scope, requirement_id)

    def search(self, query):
        return self._requirements.search(query)

    def get_links(self, scope: Scope, requirement_ids: frozenset[str]):
        return self._requirements.get_links(scope, requirement_ids)

    def coverage(self, query):
        return self._requirements.coverage(query)
