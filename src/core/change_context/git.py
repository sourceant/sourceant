"""What has changed in a checkout, without anybody hosting it.

The hosted path learns about a change because a forge told it. Work in progress
on somebody's laptop has no forge and no pull request, and that is exactly when
being told what is wrong is worth the most: before it is proposed to anyone.

So the same change set is read straight from git. Uncommitted work is included,
because uncommitted work is the work.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from src.core.scope import Scope

from .models import ChangedFile, ChangeSet

TIMEOUT = 60

# Enough diff for a model to judge the change, and a stop before a branch that
# regenerated a lock file becomes the whole review.
MAX_DIFF = 200_000

STATUS = {
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "M": "modified",
}


class GitError(RuntimeError):
    pass


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GitError(str(error)) from error
    if completed.returncode != 0:
        raise GitError(completed.stderr.strip() or "git failed")
    return completed.stdout


def default_branch(root: Path) -> str:
    """What this checkout branched off, as its remote says.

    The remote's own head, not a guess at a name: a repository whose trunk is
    `dev` is not unusual, and reviewing a branch against a `main` that has not
    moved in a year reports the year as the change.
    """
    for reference in ("refs/remotes/origin/HEAD",):
        try:
            named = _git(root, "symbolic-ref", "--quiet", reference).strip()
        except GitError:
            continue
        if named:
            return named.rsplit("/", 1)[-1]
    for name in ("main", "master", "dev", "develop"):
        try:
            _git(root, "rev-parse", "--verify", "--quiet", name)
        except GitError:
            continue
        return name
    return ""


def _base(root: Path, against: str) -> str:
    """Where this branch left the one it is going back to.

    The fork point rather than the tip, so a trunk that moved on while somebody
    worked does not turn everybody else's commits into their change.
    """
    if not against:
        return ""
    for candidate in (f"origin/{against}", against):
        try:
            return _git(root, "merge-base", "HEAD", candidate).strip()
        except GitError:
            continue
    return ""


def _differ(root: Path, *arguments: str) -> str:
    """git diff, where a difference is the answer rather than a failure.

    ``--no-index`` exits 1 when the files differ, which is the case this is
    called for.
    """
    try:
        completed = subprocess.run(
            ["git", "diff", *arguments],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GitError(str(error)) from error
    if completed.returncode > 1:
        raise GitError(completed.stderr.strip() or "git failed")
    return completed.stdout


def _untracked(root: Path) -> list[str]:
    """Files git has not been told about yet.

    A file somebody has written but not staged is still part of what they are
    about to propose, and it is the part most likely to have been forgotten.

    A directory comes back where git will not look inside one, which is how it
    reports a checkout nested in this one: a worktree, or a repository somebody
    cloned in here. Whatever is in there is that checkout's work and not this
    one's, so it is left out rather than reported as a file somebody added.
    """
    try:
        listed = _git(root, "ls-files", "--others", "--exclude-standard")
    except GitError:
        return []
    return [
        line
        for line in listed.splitlines()
        if line and not line.isspace() and not line.endswith("/")
    ]


def _changed(root: Path, base: str) -> list[ChangedFile]:
    lines = _git(root, "diff", "--name-status", base).splitlines()
    files: list[ChangedFile] = []
    seen: set[str] = set()
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        letter = parts[0][:1]
        path = parts[-1]
        if not path or path in seen:
            continue
        seen.add(path)
        files.append(ChangedFile(path=path, change=STATUS.get(letter, "modified")))

    for path in _untracked(root):
        if path in seen:
            continue
        seen.add(path)
        files.append(ChangedFile(path=path, change="added"))
    return files


def read_change(
    root: Path,
    scope: Scope,
    against: str = "",
    title: str = "",
    description: str = "",
) -> ChangeSet | None:
    """This checkout's work, as the same change set a hosted review is given.

    None when nothing has changed, which is a fine answer and not an error.
    """
    root = Path(root)
    if not (root / ".git").exists():
        raise GitError("not a git checkout")

    against = against or default_branch(root)
    base = _base(root, against)
    if not base:
        raise GitError(
            "Could not tell what this branch came from. Name the branch to "
            "compare against."
        )

    files = _changed(root, base)
    if not files:
        return None

    diff = _git(root, "diff", base)
    for path in _untracked(root):
        if len(diff) > MAX_DIFF:
            break
        diff += _differ(root, "--no-index", "--", os.devnull, path)
    if len(diff) > MAX_DIFF:
        diff = diff[:MAX_DIFF] + "\n… the rest of the diff was left out\n"

    try:
        revision = _git(root, "rev-parse", "HEAD").strip()
    except GitError:
        revision = ""

    return ChangeSet(
        scope=scope,
        files=tuple(files),
        revision=revision,
        base_revision=base,
        title=title,
        description=description,
        diff=diff,
    )
