"""Where this product keeps the skills somebody wrote here.

Not in their repository. A repository belongs to the team that owns it, and a
folder appearing in it because a tool was opened is that tool helping itself to
somebody else's checkout: it turns up in their `git status`, in their diffs, and
in a review that nobody asked for.

So they are kept beside the index, under this machine's own data directory, and
the association with a repository is recorded rather than implied by where the
file sits. A skill written for one project is still a skill written for that
project; it just lives where the rest of what this product knows about that
project lives.

What a team has committed to their repository, in the folders their coding
agents read, is a different matter and is read exactly as before. Those are
theirs, put there deliberately.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from src.config.paths import data_dir

# Under the data directory, so a machine's index and what it was told are in one
# place and back up together.
KEPT = "skills"
GLOBAL = "global"
REPOSITORIES = "repositories"

# A repository is named `owner/name`, which is two path segments and a surprise
# on Windows. The name is kept readable and made safe, with a short digest on
# the end so two that flatten to the same thing stay apart.
UNSAFE = re.compile(r"[^a-z0-9._-]+")


def folder_name(repository: str) -> str:
    """A directory name for a repository, readable and unambiguous."""
    tidy = UNSAFE.sub("-", repository.strip().lower()).strip("-") or "repository"
    digest = hashlib.sha256(repository.encode("utf-8")).hexdigest()[:8]
    return f"{tidy[:60]}-{digest}"


def global_skills() -> Path:
    """Where what somebody works by everywhere is kept."""
    return data_dir() / KEPT / GLOBAL


def repository_skills(repository: str) -> Path:
    """Where what somebody wrote for one project is kept."""
    return data_dir() / KEPT / REPOSITORIES / folder_name(repository)


def kept_for(repository: str = "") -> tuple[Path, ...]:
    """Everywhere of ours worth reading, for a machine and optionally a repository.

    Global first and the repository's after it, so a project can say something
    that departs from how somebody usually works and be seen saying it.
    """
    found = [global_skills()]
    if repository:
        found.append(repository_skills(repository))
    return tuple(found)
