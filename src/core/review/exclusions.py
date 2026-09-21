from fnmatch import fnmatchcase

from src.core.settings.review_defaults import DEFAULT_EXCLUSIONS
from src.utils.diff_parser import parse_diff


def excluded(path: str, patterns: str) -> bool:
    for pattern in patterns.splitlines():
        pattern = pattern.strip()
        if not pattern:
            continue
        candidate = path if "/" in pattern else path.rsplit("/", 1)[-1]
        if fnmatchcase(candidate, pattern) or (
            pattern.startswith("**/") and fnmatchcase(path, pattern[3:])
        ):
            return True
    return False


def review_diff(diff: str, configuration):
    patterns = configuration.value("review.exclude_patterns")
    if not isinstance(patterns, str):
        patterns = DEFAULT_EXCLUSIONS
    files = parse_diff(diff)
    omitted = tuple(one.file_path for one in files if excluded(one.file_path, patterns))
    if not omitted:
        return diff, ()
    kept = "\n".join(one.diff_text for one in files if one.file_path not in omitted)
    return kept, omitted
