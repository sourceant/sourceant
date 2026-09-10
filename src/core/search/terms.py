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
    """What to go looking for, given what the diff did.

    A name the diff took away comes first. Searching only what was added
    answers "where else is this new thing", when the question a review has to
    answer is "who was using the old one": a renamed field is added under its
    new name and removed under its old, and only the old name finds the callers
    that are about to break.
    """
    added, removed = [], []
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:])
    put_in = Counter(code_words("\n".join(added)))
    taken_out = Counter(code_words("\n".join(removed)))
    counts = put_in + taken_out
    # A word standing on both sides of the hunk in equal number is the shape
    # the change was written in, not the change. Searching for it ranks every
    # file that happens to be written the same way above the one that calls the
    # thing that moved.
    moved = {
        term: abs(put_in[term] - taken_out[term])
        for term in counts
        if put_in[term] != taken_out[term]
    }
    gone = {term for term in taken_out if term not in put_in}
    ordered = moved or {term: counts[term] for term in counts}
    return tuple(
        sorted(
            ordered,
            key=lambda term: (term not in gone, -ordered[term], term),
        )[:16]
    )
