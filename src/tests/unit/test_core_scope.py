import pytest

from src.core.scope import (
    InMemoryScopeRepository,
    Scope,
    ScopeReference,
    ScopeRepository,
    ScopeResolver,
)

PRODUCT = Scope.from_mapping({"boundary": "product"})
OTHER_PRODUCT = Scope.from_mapping({"boundary": "other"})


def test_scope_reference_is_order_independent():
    first = ScopeReference.from_mapping(
        "repository",
        "123",
        {"provider": "github", "owner": "sourceant"},
    )
    second = ScopeReference(
        "repository",
        "123",
        (("owner", "sourceant"), ("provider", "github")),
    )

    assert first == second
    assert first.get("provider") == "github"


def test_scope_reference_rejects_invalid_identity_and_qualifiers():
    with pytest.raises(ValueError, match="kind and identity"):
        ScopeReference("", "123")
    with pytest.raises(ValueError, match="qualifier keys"):
        ScopeReference(
            "repository",
            "123",
            (("provider", "github"), ("provider", "gitlab")),
        )


def test_scope_repository_binds_replaces_and_removes_references():
    reference = ScopeReference("repository", "123")
    repository = InMemoryScopeRepository()

    assert isinstance(repository, ScopeResolver)
    assert isinstance(repository, ScopeRepository)
    assert repository.resolve(reference) is None

    repository.bind(reference, PRODUCT)
    assert repository.resolve(reference) == PRODUCT

    repository.bind(reference, OTHER_PRODUCT)
    assert repository.resolve(reference) == OTHER_PRODUCT

    repository.unbind(reference)
    assert repository.resolve(reference) is None


def test_scope_repository_keeps_references_isolated():
    first = ScopeReference.from_mapping(
        "repository",
        "123",
        {"provider": "github"},
    )
    second = ScopeReference.from_mapping(
        "repository",
        "123",
        {"provider": "gitlab"},
    )
    repository = InMemoryScopeRepository({first: PRODUCT, second: OTHER_PRODUCT})

    assert repository.resolve(first) == PRODUCT
    assert repository.resolve(second) == OTHER_PRODUCT
