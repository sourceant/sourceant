from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from itertools import chain
from typing import Any

from .models import TopologyEntity, TopologyEvidence, TopologyRelationship


@dataclass(frozen=True)
class TopologySnapshot:
    entities: tuple[TopologyEntity, ...]
    relationships: tuple[TopologyRelationship, ...]
    format_version: int = 1
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.format_version != 1:
            raise ValueError("unsupported topology snapshot version")
        entity_ids = [entity.id for entity in self.entities]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("topology snapshot entity ids must be unique")
        relationship_ids = [relationship.id for relationship in self.relationships]
        if len(relationship_ids) != len(set(relationship_ids)):
            raise ValueError("topology snapshot relationship ids must be unique")
        known_ids = frozenset(entity_ids)
        if any(
            relationship.source_id not in known_ids
            or relationship.target_id not in known_ids
            for relationship in self.relationships
        ):
            raise ValueError("topology snapshot relationship endpoints must exist")
        if any(
            len(item.evidence) != len({evidence.id for evidence in item.evidence})
            for item in chain(self.entities, self.relationships)
        ):
            raise ValueError("topology snapshot evidence ids must be unique")
        object.__setattr__(
            self,
            "entities",
            tuple(
                sorted(
                    (
                        replace(
                            entity,
                            evidence=tuple(
                                sorted(entity.evidence, key=lambda item: item.id)
                            ),
                        )
                        for entity in self.entities
                    ),
                    key=lambda entity: entity.id,
                )
            ),
        )
        object.__setattr__(
            self,
            "relationships",
            tuple(
                sorted(
                    (
                        replace(
                            relationship,
                            evidence=tuple(
                                sorted(relationship.evidence, key=lambda item: item.id)
                            ),
                        )
                        for relationship in self.relationships
                    ),
                    key=lambda relationship: relationship.id,
                )
            ),
        )


class JSONTopologySnapshotCodec:
    media_type = "application/vnd.sourceant.topology+json"

    def encode(self, snapshot: TopologySnapshot) -> bytes:
        value = {
            "entities": [self._entity(entity) for entity in snapshot.entities],
            "format": "sourceant.topology",
            "properties": dict(snapshot.properties),
            "relationships": [
                self._relationship(relationship)
                for relationship in snapshot.relationships
            ],
            "version": snapshot.format_version,
        }
        return json.dumps(
            value,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def decode(self, payload: bytes) -> TopologySnapshot:
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict) or value.get("format") != "sourceant.topology":
            raise ValueError("invalid topology snapshot format")
        if value.get("version") != 1:
            raise ValueError("unsupported topology snapshot version")
        entities = value.get("entities")
        relationships = value.get("relationships")
        properties = value.get("properties", {})
        if not isinstance(entities, list) or not isinstance(relationships, list):
            raise TypeError("topology snapshot collections must be lists")
        if not isinstance(properties, dict):
            raise TypeError("topology snapshot properties must be an object")
        return TopologySnapshot(
            tuple(self._entity_from_value(entity) for entity in entities),
            tuple(
                self._relationship_from_value(relationship)
                for relationship in relationships
            ),
            value["version"],
            properties,
        )

    @classmethod
    def _entity(cls, entity: TopologyEntity) -> dict:
        return {
            "confidence": entity.confidence,
            "evidence": [cls._evidence(item) for item in entity.evidence],
            "id": entity.id,
            "kind": entity.kind,
            "properties": dict(entity.properties),
            "stale": entity.stale,
            "status": entity.status,
        }

    @classmethod
    def _relationship(cls, relationship: TopologyRelationship) -> dict:
        return {
            "confidence": relationship.confidence,
            "evidence": [cls._evidence(item) for item in relationship.evidence],
            "id": relationship.id,
            "properties": dict(relationship.properties),
            "source_id": relationship.source_id,
            "stale": relationship.stale,
            "status": relationship.status,
            "target_id": relationship.target_id,
            "type": relationship.type,
        }

    @staticmethod
    def _evidence(evidence: TopologyEvidence) -> dict:
        return {
            "id": evidence.id,
            "kind": evidence.kind,
            "properties": dict(evidence.properties),
            "revision": evidence.revision,
            "source": evidence.source,
        }

    @classmethod
    def _entity_from_value(cls, value) -> TopologyEntity:
        if not isinstance(value, dict):
            raise TypeError("topology snapshot entity must be an object")
        return TopologyEntity(
            id=value["id"],
            kind=value["kind"],
            status=value["status"],
            confidence=value.get("confidence", 1.0),
            stale=value.get("stale", False),
            properties=value.get("properties", {}),
            evidence=tuple(
                cls._evidence_from_value(item) for item in value.get("evidence", [])
            ),
        )

    @classmethod
    def _relationship_from_value(cls, value) -> TopologyRelationship:
        if not isinstance(value, dict):
            raise TypeError("topology snapshot relationship must be an object")
        return TopologyRelationship(
            id=value["id"],
            source_id=value["source_id"],
            target_id=value["target_id"],
            type=value["type"],
            status=value["status"],
            confidence=value.get("confidence", 1.0),
            stale=value.get("stale", False),
            properties=value.get("properties", {}),
            evidence=tuple(
                cls._evidence_from_value(item) for item in value.get("evidence", [])
            ),
        )

    @staticmethod
    def _evidence_from_value(value) -> TopologyEvidence:
        if not isinstance(value, dict):
            raise TypeError("topology snapshot evidence must be an object")
        return TopologyEvidence(
            id=value["id"],
            kind=value["kind"],
            source=value["source"],
            revision=value.get("revision", ""),
            properties=value.get("properties", {}),
        )
