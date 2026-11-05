from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass, replace
from src.core.memory.models import (
    Source,
    Occurrence,
    OccurrenceActor,
    OccurrenceSubject,
)


@dataclass(frozen=True)
class Origin:
    installation: str
    container: str


def _instant(value) -> str | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return (
            datetime.fromtimestamp(value, timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    return str(value).replace("+00:00", "Z")


class _Batch:
    def __init__(self, origin: Origin):
        self.origin = origin
        self.subjects = {}
        self.events = {}

    def actor(self, user):
        if not user or not user.get("id"):
            return None
        bot = user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]")
        return OccurrenceActor(
            id=str(user["id"]),
            name=user.get("login") or "",
            image=user.get("avatar_url") or "",
            url=user.get("html_url") or "",
            type="bot" if bot else "person",
        )

    def repository(self, repository):
        subject = OccurrenceSubject(
            id="github.repository:repository",
            type="github.repository",
            name=repository.get("full_name") or "",
            url=repository.get("html_url") or "",
            role="place",
            container_root=True,
        )
        self.subjects[subject.id] = subject
        return subject

    def pull_place(self, repository, pull):
        parent = self.repository(repository)
        subject = OccurrenceSubject(
            id=f"github.pull_request:pr_{pull['number']}",
            type="github.pull_request",
            name=f"#{pull['number']}",
            url=pull.get("html_url") or "",
            parent=parent.id,
            role="place",
            attributes={"github.title": pull.get("title") or ""},
            place_id=f"pr_{pull['number']}",
        )
        self.subjects[subject.id] = subject
        return subject

    def path(self, path):
        subject = OccurrenceSubject(id=f"code.path:{path}", type="code.path", name=path)
        self.subjects[subject.id] = subject
        return subject

    def event(self, event, native, url=None):
        place = event.pop("location")
        subjects = event.pop("subjects", [])
        subjects += [place]
        if place.parent:
            subjects += [self.subjects[place.parent]]
        event["actors"] = [
            replace(actor, role=role)
            for actor, role in event.get("actors", [])
            if actor
        ]
        occurrence = Occurrence(
            source=Source(
                system="github",
                installation=self.origin.installation,
                container=self.origin.container,
                url=url or "",
                container_url=self.subjects["github.repository:repository"].url,
                native_id=native,
                id_namespace=f"gh_{self.origin.container}",
            ),
            subjects=subjects,
            **event,
        )
        self.events[occurrence.id] = occurrence
        return occurrence.id

    def pull(self, repository: dict, pull: dict) -> str:
        """The pull request as an event, from facts fixed when it was opened."""
        author = self.actor(pull.get("user"))
        return self.event(
            {
                "id": f"pr_{pull['number']}",
                "type": "github.pull_request",
                "when": _instant(pull.get("created_at")),
                "location": self.pull_place(repository, pull),
                "actors": [(author, "author")] if author else [],
            },
            native=f"pull:{pull.get('id') or pull['number']}",
            url=pull.get("html_url"),
        )

    def said(self, text):
        return (text or "").strip()

    def to_occurrences(self):
        return list(self.events.values())


def _review(b: _Batch, payload: dict) -> None:
    review, repository, pull = (
        payload["review"],
        payload["repository"],
        payload["pull_request"],
    )
    parent = b.pull(repository, pull)
    reviewer = b.actor(review.get("user"))
    b.event(
        {
            "id": f"review_{review['id']}",
            "type": "github.pull_request.review",
            "when": _instant(review.get("submitted_at")),
            "location": b.pull_place(repository, pull),
            "actors": ([(reviewer, "reviewer")] if reviewer else []),
            "what": b.said(review.get("body")),
            "part_of": parent,
            "attributes": {"github.state": str(review.get("state") or "").lower()},
        },
        native=f"review:{review['id']}",
        url=review.get("html_url"),
    )


def _comment(b: _Batch, payload: dict) -> None:
    comment, repository, pull = (
        payload["comment"],
        payload["repository"],
        payload["pull_request"],
    )
    parent = b.pull(repository, pull)
    speaker = b.actor(comment.get("user"))
    opened_it = (comment.get("user") or {}).get("id") == (pull.get("user") or {}).get(
        "id"
    )
    role = "author" if opened_it else "reviewer"
    path = comment.get("path")
    reply = comment.get("in_reply_to_id")
    b.event(
        {
            "id": f"comment_{comment['id']}",
            "type": "github.pull_request.review_comment",
            "when": _instant(comment.get("created_at")),
            "location": b.pull_place(repository, pull),
            "actors": [(speaker, role)] if speaker else [],
            "what": b.said(comment.get("body")),
            "subjects": [b.path(path)] if path else [],
            "part_of": parent,
            "in_reply_to": (f"comment_{reply}" if reply else None),
            "attributes": {"github.path": path} if path else {},
        },
        native=f"comment:{comment['id']}",
        url=comment.get("html_url"),
    )


def _ended(b: _Batch, payload: dict) -> None:
    repository, pull = payload["repository"], payload["pull_request"]
    parent = b.pull(repository, pull)
    merged = bool(pull.get("merged"))
    who = b.actor(pull.get("merged_by") if merged else payload.get("sender"))
    b.event(
        {
            "id": f"{parent}_{'merged' if merged else 'closed'}",
            "type": (
                "github.pull_request.merged" if merged else "github.pull_request.closed"
            ),
            "when": _instant(
                pull.get("merged_at") if merged else pull.get("closed_at")
            ),
            "location": b.pull_place(repository, pull),
            "actors": [(who, "decider")] if who else [],
            "part_of": parent,
        },
        native=f"pull:{pull.get('id') or pull['number']}:{'merged' if merged else 'closed'}",
        url=pull.get("html_url"),
    )


def _push(b: _Batch, payload: dict, pull: dict | None) -> None:
    commits = payload.get("commits") or []
    head = payload.get("head_commit") or (commits[-1] if commits else None)
    if not head or payload.get("deleted"):
        return
    repository = payload["repository"]
    if pull and pull.get("created_at") and head.get("timestamp"):
        created = datetime.fromisoformat(
            _instant(pull["created_at"]).replace("Z", "+00:00")
        )
        pushed_at = datetime.fromisoformat(
            _instant(head["timestamp"]).replace("Z", "+00:00")
        )
        if pushed_at < created:
            pull = None
    files = sorted(
        {
            f
            for c in commits
            for key in ("added", "modified", "removed")
            for f in c.get(key) or []
        }
    )
    pusher = b.actor(payload.get("sender"))
    message = "\n".join(
        (c.get("message") or "").splitlines()[0] for c in commits if c.get("message")
    )
    pushed = hashlib.sha256(
        f"{payload.get('ref')}\0{payload.get('after') or head['id']}".encode()
    ).hexdigest()[:24]
    b.event(
        {
            "id": f"push_{pushed}",
            "type": "github.push",
            "when": _instant(head.get("timestamp")),
            "location": (
                b.pull_place(repository, pull) if pull else b.repository(repository)
            ),
            "actors": [(pusher, "author")] if pusher else [],
            "what": b.said(message),
            "subjects": [b.path(f) for f in files],
            "part_of": b.pull(repository, pull) if pull else None,
            "attributes": {"github.files": files, "github.ref": payload.get("ref")},
        },
        native=f"push:{payload.get('ref')}:{payload.get('after') or head['id']}",
        url=payload.get("compare"),
    )


def translate(
    origin: Origin,
    event_type: str,
    action: str | None,
    payload: dict,
    pull_for_branch=None,
) -> list[Occurrence] | None:
    """Translate a delivery into source facts."""
    if not isinstance(payload.get("repository"), dict):
        return None
    if event_type.startswith("pull_request") and not isinstance(
        payload.get("pull_request"), dict
    ):
        return None
    b = _Batch(origin)
    if event_type == "pull_request_review" and action == "submitted":
        _review(b, payload)
    elif event_type == "pull_request_review_comment" and action == "created":
        _comment(b, payload)
    elif event_type == "pull_request" and action == "opened":
        b.pull(payload["repository"], payload["pull_request"])
    elif event_type == "pull_request" and action == "closed":
        _ended(b, payload)
    elif event_type == "push":
        branch = str(payload.get("ref") or "").removeprefix("refs/heads/")
        _push(
            b, payload, pull_for_branch(branch) if pull_for_branch and branch else None
        )
    else:
        return None
    return b.to_occurrences()
