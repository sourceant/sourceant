from __future__ import annotations

import os

from tree_sitter_language_pack import (
    Error,
    PackConfig,
    ProcessConfig,
    detect_language,
    process,
)

_cache_dir = os.getenv("SOURCEANT_TREE_SITTER_CACHE")
if _cache_dir:
    from tree_sitter_language_pack import configure

    configure(PackConfig(cache_dir=_cache_dir))


__all__ = ["Error", "ProcessConfig", "detect_language", "process"]
