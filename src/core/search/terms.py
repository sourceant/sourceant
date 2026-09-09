import re
from collections import Counter

_STOP_WORDS = frozenset(
    "self cls def class return import from with for while elif else try except "
    "raise pass none true false str int bool list dict tuple set any optional "
    "args kwargs type value values item items data result results name path "
    "file files test tests assert mock monkeypatch lambda await async yield "
    "the and that this not only all can has have are was will new old add "
    "added use using code function method parameter parameters object string".split()
)


def code_words(text: str) -> tuple[str, ...]:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return tuple(
        word.lower()
        for word in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,63}", separated)
        if word.lower() not in _STOP_WORDS
    )


def changed_code_terms(diff: str) -> tuple[str, ...]:
    added = "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    counts = Counter(code_words(added))
    return tuple(sorted(counts, key=lambda term: (-counts[term], term))[:16])
