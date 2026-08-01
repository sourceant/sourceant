## Triage

Triage is one queue of the open issues across the repositories you connect, and the three actions worth taking on them: reply, label, or close. It reads live from GitHub rather than from a copy, so what you see is what the repository says right now.

The endpoints below are in the open core. SourceAnt Cloud puts the same queue on a screen; anything you run yourself can do the same.

### The queue

`GET /api/triage?repo=owner/name` returns one page of open issues, newest activity first. Repeat `repo` to work several repositories as a single list:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://your-instance/api/triage?repo=acme/api&repo=acme/web&page=1&size=25"
```

Each entry carries the number, title, author, labels, comment count, and the GitHub URL. Pull requests are excluded, even though GitHub answers them from the same endpoint.

`GET /api/triage/detail?repo=owner/name&number=42` returns one issue with its body and up to 50 comments.

### Acting on an issue

`POST /api/triage/action` takes one of three actions:

```json
{"repo": "acme/api", "number": 42, "action": "comment", "comment": "Which version does this reproduce on?"}
{"repo": "acme/api", "number": 42, "action": "label", "labels": ["bug", "needs-info"]}
{"repo": "acme/api", "number": 42, "action": "close"}
```

The action is taken as you, using the GitHub token in your session, so it appears under your name and is bound by your permissions on the repository.

### Automatic triage

Working the queue by hand is one half. The [repo manager](repo-management.md) is the other: when it is enabled, a newly opened issue is compared against the ones already open, labelled from the repository's own label set, and told about a likely duplicate before anyone reaches it.

The two do not conflict. The repo manager narrows what arrives in the queue; triage is where a person decides.
