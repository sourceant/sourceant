from __future__ import annotations

import hashlib
from datetime import datetime
from dataclasses import dataclass


def subject_id(bucket: str, category: str, name: str) -> str:
    digest = hashlib.sha256(f"{bucket}\0{category}\0{name}".encode()).hexdigest()
    return f"sb_{digest[:24]}"


BATCH = 100


@dataclass(frozen=True)
class Bound:
    """A repository's bucket and the GitHub ids it is bound to."""

    bucket: str
    installation: str
    container: str


def _instant(value) -> str | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        from datetime import datetime, timezone

        return (
            datetime.fromtimestamp(value, timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    return str(value).replace("+00:00", "Z")


class _Batch:
    def __init__(self, bound: Bound):
        self.bound = bound
        self.entities: dict[str, dict] = {}
        self.events: dict[str, dict] = {}

    def _entity(self, entity: dict) -> str:
        self.entities[entity["id"]] = {
            "bucket": self.bound.bucket,
            **{k: v for k, v in entity.items() if v not in (None, "", {})},
        }
        return entity["id"]

    def actor(self, user: dict | None) -> str | None:
        if not user or not user.get("id"):
            return None
        bot = user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]")
        return self._entity(
            {
                "id": f"ac_gh_{self.bound.container}_{user['id']}",
                "class": "actor",
                "type": "software.bot" if bot else "person",
                "name": user.get("login") or "",
                "image": user.get("avatar_url") or "",
                "url": user.get("html_url") or "",
            }
        )

    def repository(self, repository: dict) -> str:
        return self._entity(
            {
                "id": f"pl_gh_{self.bound.container}",
                "class": "place",
                "type": "github.repository",
                "name": repository.get("full_name") or "",
                "url": repository.get("html_url") or "",
            }
        )

    def pull_place(self, repository: dict, pull: dict) -> str:
        return self._entity(
            {
                "id": f"pl_gh_{self.bound.container}_pr_{pull['number']}",
                "class": "place",
                "type": "github.pull_request",
                "name": f"#{pull['number']}",
                "url": pull.get("html_url") or "",
                "parent": self.repository(repository),
                "attributes": {"github.title": pull.get("title") or ""},
            }
        )

    def path(self, path: str) -> str:
        return self._entity(
            {
                "id": subject_id(self.bound.bucket, "path", path),
                "class": "subject",
                "type": "code.path",
                "name": path,
            }
        )

    def event(self, event: dict, native: str, url: str | None = None) -> str:
        source = {
            "system": "github",
            "installation": self.bound.installation,
            "native_id": native,
        }
        if url:
            source["url"] = url
        body = {
            "bucket": self.bound.bucket,
            **{k: v for k, v in event.items() if v not in (None, [], {}, "")},
            "source": source,
        }
        self.events[body["id"]] = body
        return body["id"]

    def pull(self, repository: dict, pull: dict) -> str:
        """The pull request as an event, from facts fixed when it was opened."""
        author = self.actor(pull.get("user"))
        return self.event(
            {
                "id": f"ev_gh_{self.bound.container}_pr_{pull['number']}",
                "type": "github.pull_request",
                "occurred_at": _instant(pull.get("created_at")),
                "place": self.pull_place(repository, pull),
                "participants": [{"actor": author, "role": "author"}] if author else [],
            },
            native=f"pull:{pull.get('id') or pull['number']}",
            url=pull.get("html_url"),
        )

    def said(self, speaker: str | None, text: str | None) -> list[dict]:
        text = (text or "").strip()
        if not text:
            return []
        return [{"speaker": speaker, "text": text} if speaker else {"text": text}]

    def to_dict(self) -> dict:
        return {
            "entities": list(self.entities.values()),
            "events": list(self.events.values()),
        }


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
            "id": f"ev_gh_{b.bound.container}_review_{review['id']}",
            "type": "github.pull_request.review",
            "occurred_at": _instant(review.get("submitted_at")),
            "place": b.pull_place(repository, pull),
            "participants": (
                [{"actor": reviewer, "role": "reviewer"}] if reviewer else []
            ),
            "content": b.said(reviewer, review.get("body")),
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
            "id": f"ev_gh_{b.bound.container}_comment_{comment['id']}",
            "type": "github.pull_request.review_comment",
            "occurred_at": _instant(comment.get("created_at")),
            "place": b.pull_place(repository, pull),
            "participants": [{"actor": speaker, "role": role}] if speaker else [],
            "content": b.said(speaker, comment.get("body")),
            "subjects": [{"subject": b.path(path), "role": "about"}] if path else [],
            "part_of": parent,
            "in_reply_to": (
                f"ev_gh_{b.bound.container}_comment_{reply}" if reply else None
            ),
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
            "occurred_at": _instant(
                pull.get("merged_at") if merged else pull.get("closed_at")
            ),
            "place": b.pull_place(repository, pull),
            "participants": [{"actor": who, "role": "decider"}] if who else [],
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
            "id": f"ev_gh_{b.bound.container}_push_{pushed}",
            "type": "github.push",
            "occurred_at": _instant(head.get("timestamp")),
            "place": (
                b.pull_place(repository, pull) if pull else b.repository(repository)
            ),
            "participants": [{"actor": pusher, "role": "author"}] if pusher else [],
            "content": b.said(pusher, message),
            "subjects": [{"subject": b.path(f), "role": "about"} for f in files],
            "part_of": b.pull(repository, pull) if pull else None,
            "attributes": {"github.files": files, "github.ref": payload.get("ref")},
        },
        native=f"push:{payload.get('ref')}:{payload.get('after') or head['id']}",
        url=payload.get("compare"),
    )


def translate(
    bound: Bound,
    event_type: str,
    action: str | None,
    payload: dict,
    pull_for_branch=None,
) -> dict | None:
    """The engine records one webhook stands for, or None when it records
    nothing that happened (an edit, a label, a re-run)."""
    if not isinstance(payload.get("repository"), dict):
        return None
    if event_type.startswith("pull_request") and not isinstance(
        payload.get("pull_request"), dict
    ):
        return None
    b = _Batch(bound)
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
    return b.to_dict()


def merge(batches: list[dict]) -> dict:
    """Several webhooks' records as one write, each record once."""
    entities: dict[str, dict] = {}
    events: dict[str, dict] = {}
    for batch in batches:
        entities.update({e["id"]: e for e in batch["entities"]})
        events.update({e["id"]: e for e in batch["events"]})
    return {"entities": list(entities.values()), "events": list(events.values())}
