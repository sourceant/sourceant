"""Skills carrying the documents they point at.

A skill is often a page and a pointer to another page. The pointer is the
part that reaches a reviewer unless somebody follows it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path

from .filesystem import attach, references
from .models import Skill

#: How many documents several skills share before the rest are left out.
MAX_SHARED = 2

HOUSE = "Applies to everything here, whatever the change is about."


def split(chosen) -> list[Skill]:
    """Each skill with the documents only it points at, plus shared ones alone.

    A document more than one skill references is not that skill's content, so
    attaching it to each would ask the same question repeatedly and file every
    answer under an unrelated skill.
    """
    attached = {skill.id: references(skill) for skill in chosen}
    counted = Counter(path for found in attached.values() for path in found)
    shared = [path for path, times in counted.most_common(MAX_SHARED) if times > 1]

    asking = [
        replace(
            skill,
            body=attach(
                skill.body,
                {
                    path: text
                    for path, text in attached[skill.id].items()
                    if path not in shared
                },
            ),
        )
        for skill in chosen
    ]

    for path in shared:
        text = next(found[path] for found in attached.values() if path in found)
        name = Path(path).name
        asking.append(
            Skill(
                id=name,
                name=name,
                description=HOUSE,
                body=text,
                path=path,
                origin="shared",
            )
        )
    return asking
