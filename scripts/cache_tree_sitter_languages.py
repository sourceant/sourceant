from __future__ import annotations

import os

from tree_sitter_language_pack import (
    Error,
    PackConfig,
    configure,
    download_all,
    get_language,
    manifest_languages,
)

required_languages = {
    "c",
    "cpp",
    "csharp",
    "go",
    "java",
    "javascript",
    "kotlin",
    "php",
    "python",
    "ruby",
    "rust",
    "tsx",
    "typescript",
}
cache_dir = os.environ["SOURCEANT_TREE_SITTER_CACHE"]
configure(PackConfig(cache_dir=cache_dir))
languages = manifest_languages()
download_all()
unavailable = []
for language in languages:
    try:
        get_language(language)
    except (Error, RuntimeError):
        unavailable.append(language)

missing_required = sorted(required_languages.intersection(unavailable))
if missing_required:
    raise RuntimeError(
        f"Required tree-sitter languages are unavailable: {', '.join(missing_required)}"
    )

available_count = len(languages) - len(unavailable)
print(f"Cached and loaded {available_count} of {len(languages)} tree-sitter languages")
if unavailable:
    print(f"Unavailable manifest languages: {', '.join(sorted(unavailable))}")
