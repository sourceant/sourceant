from __future__ import annotations

import importlib
import json
import zlib


class BuildPolicy:
    def module_source(self, module: str, source: str) -> str:
        return source

    def resource_source(
        self, package: str, resources: dict[str, bytes], migrations: tuple[str, ...]
    ) -> str:
        packed = zlib.compress(
            json.dumps(
                {name: value.hex() for name, value in resources.items()}, sort_keys=True
            ).encode(),
            level=9,
        )
        return (
            "import json\nimport zlib\n"
            + f"_PAYLOAD = {packed!r}\n"
            + "def resources():\n    return {name: bytes.fromhex(value) for name, value in json.loads(zlib.decompress(_PAYLOAD)).items()}\n"
            + f"MIGRATIONS = {migrations!r}\n"
        )


def load_policy(reference: str | None) -> BuildPolicy:
    if reference is None:
        return BuildPolicy()
    module, separator, name = reference.partition(":")
    if not separator or not module or not name:
        raise ValueError("Build policy must use module:factory")
    policy = getattr(importlib.import_module(module), name)()
    if not isinstance(policy, BuildPolicy):
        raise TypeError("Build policy must extend BuildPolicy")
    return policy
