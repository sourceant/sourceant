import pytest

from src.core.contracts import (
    ContractAdapter,
    ContractChange,
    ContractComparison,
    ContractDocument,
    ContractElement,
    ContractEvidence,
    ContractPayload,
    ContractQuery,
    ContractSnapshot,
    InMemoryContractRepository,
)
from src.core.scope import Scope

PRODUCT = Scope.from_mapping({"boundary": "product"})
OTHER_PRODUCT = Scope.from_mapping({"boundary": "other"})


def document(identifier="checkout", format="openapi", digest="sha256:abc"):
    return ContractDocument(
        identifier,
        format,
        "application/yaml",
        digest,
        3,
        revision="abc123",
    )


def snapshot(identifier="checkout-v1", contract_document=None, elements=()):
    return ContractSnapshot(
        identifier,
        contract_document or document(),
        elements,
    )


def test_contract_payload_separates_content_from_immutable_descriptor():
    descriptor = document()

    payload = ContractPayload(descriptor, b"api")

    assert payload.document.digest == "sha256:abc"
    assert payload.content == b"api"
    with pytest.raises(ValueError, match="payload size"):
        ContractPayload(descriptor, b"wrong")


def test_contract_snapshot_requires_stable_unique_element_identities():
    operation = ContractElement("GET /orders", "operation", "List orders")

    result = snapshot(elements=(operation,))

    assert result.elements == (operation,)
    with pytest.raises(ValueError, match="element ids must be unique"):
        snapshot(elements=(operation, operation))


def test_in_memory_repository_is_scoped_filtered_and_deterministic():
    repository = InMemoryContractRepository()
    openapi_a = snapshot("a", document("checkout", "openapi", "sha256:a"))
    openapi_b = snapshot("b", document("checkout", "openapi", "sha256:b"))
    graphql = snapshot("c", document("catalog", "graphql", "sha256:c"))
    for scope in (PRODUCT, OTHER_PRODUCT):
        for item in (openapi_b, openapi_a, graphql):
            repository.put_snapshot(scope, item)

    result = repository.search(
        ContractQuery(
            PRODUCT,
            document_ids=frozenset({"checkout"}),
            formats=frozenset({"openapi"}),
            limit=1,
        )
    )

    assert result.items == (openapi_a,)
    assert result.total == 2
    assert result.has_more is True
    assert repository.get_snapshot(PRODUCT, "b") == openapi_b
    assert repository.get_snapshot(Scope(), "b") is None


def test_comparison_requires_both_snapshots_in_the_same_scope():
    repository = InMemoryContractRepository()
    before = snapshot("before")
    after = snapshot("after", document(digest="sha256:def"))
    repository.put_snapshot(PRODUCT, before)
    repository.put_snapshot(OTHER_PRODUCT, after)
    comparison = ContractComparison("comparison", "before", "after", True)

    with pytest.raises(ValueError, match="after snapshot"):
        repository.put_comparison(PRODUCT, comparison)

    repository.put_snapshot(PRODUCT, after)
    repository.put_comparison(PRODUCT, comparison)

    assert repository.get_comparison(PRODUCT, comparison.id) == comparison
    assert repository.get_comparison(OTHER_PRODUCT, comparison.id) is None


def test_comparison_preserves_deterministic_compatibility_evidence():
    evidence = ContractEvidence(
        "parser-output",
        "contract_diff",
        "adapter",
        "v1",
    )
    changes = tuple(
        ContractChange(
            classification,
            classification,
            "error" if classification in {"breaking", "removed"} else "info",
            classification,
            before_element_id="old" if classification != "additive" else None,
            after_element_id="new" if classification != "removed" else None,
            evidence=(evidence,),
        )
        for classification in (
            "additive",
            "breaking",
            "renamed",
            "removed",
            "unsupported",
        )
    )

    result = ContractComparison("diff", "before", "after", False, changes)
    unchanged = ContractComparison("same", "before", "after", True)

    assert tuple(change.classification for change in result.changes) == (
        "additive",
        "breaking",
        "renamed",
        "removed",
        "unsupported",
    )
    assert unchanged.changes == ()


def test_contract_adapter_protocol_is_format_neutral():
    class Adapter:
        def supports(self, contract_document):
            return contract_document.format == "example"

        def extract(self, payload):
            return snapshot(contract_document=payload.document)

        def compare(self, before, after):
            return ContractComparison("diff", before.id, after.id, True)

    assert isinstance(Adapter(), ContractAdapter)


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: document(identifier=""), "document id cannot be empty"),
        (lambda: document(format=""), "document format cannot be empty"),
        (lambda: document(digest="abc"), "digest must be algorithm:value"),
        (
            lambda: ContractElement("", "operation", "List orders"),
            "element id cannot be empty",
        ),
        (
            lambda: ContractChange("change", "removed", "error", "Removed"),
            "identify an affected element",
        ),
        (
            lambda: ContractChange(
                "change",
                "removed",
                "error",
                "Removed",
                before_element_id="old",
                confidence=1.1,
            ),
            "confidence must be between 0 and 1",
        ),
    ],
)
def test_contract_models_reject_invalid_boundaries(factory, message):
    with pytest.raises(ValueError, match=message):
        factory()
