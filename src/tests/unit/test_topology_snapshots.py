import json

import pytest

from src.core.topology import (
    JSONTopologySnapshotCodec,
    TopologyEntity,
    TopologyEvidence,
    TopologyRelationship,
    TopologySnapshot,
    TopologySnapshotCodec,
)


def graph_values():
    evidence = TopologyEvidence(
        "contract-v2",
        "openapi",
        "provider",
        "abc123",
        {"path": "openapi.yaml"},
    )
    provider = TopologyEntity(
        "provider",
        "service",
        "active",
        properties={"versions": ["v1", "v2"]},
        evidence=(evidence,),
    )
    consumer = TopologyEntity(
        "consumer",
        "component",
        "inactive",
        confidence=0.75,
        stale=True,
    )
    relationship = TopologyRelationship(
        "consumer-provider",
        consumer.id,
        provider.id,
        "depends_on",
        "approved",
        confidence=0.8,
        stale=True,
        properties={"operation": "createOrder"},
        evidence=(evidence,),
    )
    return provider, consumer, relationship


def test_round_trips_a_canonical_topology_snapshot():
    provider, consumer, relationship = graph_values()
    codec = JSONTopologySnapshotCodec()
    snapshot = TopologySnapshot(
        (provider, consumer),
        (relationship,),
        properties={"revision": "def456"},
    )

    payload = codec.encode(snapshot)
    restored = codec.decode(payload)

    assert isinstance(codec, TopologySnapshotCodec)
    assert restored == snapshot
    assert tuple(entity.id for entity in restored.entities) == (
        "consumer",
        "provider",
    )
    assert json.loads(payload) == {
        "entities": [
            {
                "confidence": 0.75,
                "evidence": [],
                "id": "consumer",
                "kind": "component",
                "properties": {},
                "stale": True,
                "status": "inactive",
            },
            {
                "confidence": 1.0,
                "evidence": [
                    {
                        "id": "contract-v2",
                        "kind": "openapi",
                        "properties": {"path": "openapi.yaml"},
                        "revision": "abc123",
                        "source": "provider",
                    }
                ],
                "id": "provider",
                "kind": "service",
                "properties": {"versions": ["v1", "v2"]},
                "stale": False,
                "status": "active",
            },
        ],
        "format": "sourceant.topology",
        "properties": {"revision": "def456"},
        "relationships": [
            {
                "confidence": 0.8,
                "evidence": [
                    {
                        "id": "contract-v2",
                        "kind": "openapi",
                        "properties": {"path": "openapi.yaml"},
                        "revision": "abc123",
                        "source": "provider",
                    }
                ],
                "id": "consumer-provider",
                "properties": {"operation": "createOrder"},
                "source_id": "consumer",
                "stale": True,
                "status": "approved",
                "target_id": "provider",
                "type": "depends_on",
            }
        ],
        "version": 1,
    }


def test_encoding_is_stable_across_input_and_property_order():
    provider, consumer, relationship = graph_values()
    deployment = TopologyEvidence("deployment-7", "deployment", "build", "def456")
    provider = TopologyEntity(
        provider.id,
        provider.kind,
        provider.status,
        properties=provider.properties,
        evidence=(*provider.evidence, deployment),
    )
    codec = JSONTopologySnapshotCodec()
    first = TopologySnapshot(
        (provider, consumer),
        (relationship,),
        properties={"branch": "main", "revision": "abc123"},
    )
    second = TopologySnapshot(
        (
            consumer,
            TopologyEntity(
                provider.id,
                provider.kind,
                provider.status,
                properties=provider.properties,
                evidence=tuple(reversed(provider.evidence)),
            ),
        ),
        (relationship,),
        properties={"revision": "abc123", "branch": "main"},
    )

    assert codec.encode(first) == codec.encode(second)


def test_rejects_duplicate_ids_and_missing_relationship_endpoints():
    provider, consumer, relationship = graph_values()
    with pytest.raises(ValueError, match="entity ids must be unique"):
        TopologySnapshot((provider, provider), ())
    with pytest.raises(ValueError, match="relationship ids must be unique"):
        TopologySnapshot(
            (provider, consumer),
            (relationship, relationship),
        )
    with pytest.raises(ValueError, match="endpoints must exist"):
        TopologySnapshot((provider,), (relationship,))
    duplicate_evidence = TopologyEvidence("same", "commit", "git")
    with pytest.raises(ValueError, match="evidence ids must be unique"):
        TopologySnapshot(
            (
                TopologyEntity(
                    "duplicate",
                    "component",
                    "active",
                    evidence=(duplicate_evidence, duplicate_evidence),
                ),
            ),
            (),
        )


@pytest.mark.parametrize(
    "payload",
    (
        b"[]",
        b'{"format":"unknown","version":1,"entities":[],"relationships":[]}',
        b'{"format":"sourceant.topology","version":2,"entities":[],"relationships":[]}',
        b'{"format":"sourceant.topology","version":1,"entities":{},"relationships":[]}',
    ),
)
def test_rejects_invalid_snapshot_documents(payload):
    with pytest.raises((TypeError, ValueError)):
        JSONTopologySnapshotCodec().decode(payload)
