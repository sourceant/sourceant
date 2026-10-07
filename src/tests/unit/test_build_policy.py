import importlib
import sys

import pytest

from src.build.policy import BuildPolicy, load_policy


def test_default_policy_preserves_source():
    source = "VALUE = 'public value'\n"
    assert BuildPolicy().module_source("sample", source) == source


def test_resource_policy_round_trip_preserves_binary_and_revision_manifest():
    assets = {"skill/SKILL.md": "Review café changes".encode(), "data.txt": b"\xff\x00"}
    namespace = {}
    exec(
        BuildPolicy().resource_source("sample", assets, ("sample.revision",)), namespace
    )
    assert namespace["resources"]() == assets
    assert namespace["MIGRATIONS"] == ("sample.revision",)


def test_policy_factory_loads_an_extension(tmp_path, monkeypatch):
    module = tmp_path / "custom_build_policy.py"
    module.write_text(
        "from src.build.policy import BuildPolicy\n"
        "class CustomPolicy(BuildPolicy):\n"
        "    def module_source(self, module, source):\n"
        "        return source + '\\nEXTRA = 1\\n'\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    try:
        policy = load_policy("custom_build_policy:CustomPolicy")
        assert policy.module_source("sample", "VALUE = 2") == "VALUE = 2\nEXTRA = 1\n"
    finally:
        sys.modules.pop("custom_build_policy", None)


@pytest.mark.parametrize(
    "reference", ["missing-factory", ":BuildPolicy", "src.build.policy:"]
)
def test_invalid_policy_reference_is_rejected(reference):
    with pytest.raises(ValueError, match="module:factory"):
        load_policy(reference)


def test_policy_factory_requires_the_build_contract():
    with pytest.raises(TypeError, match="extend BuildPolicy"):
        load_policy("builtins:object")
