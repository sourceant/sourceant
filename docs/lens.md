## Lens

Inline comments on a diff assume someone will open that diff and read it. When most of the change was written by an agent, and the provider will not render a diff that size anyway, the review has no working consumer: the person cannot read it, and the agent that wrote the change has to scrape its own pull request page to find out what was said.

Lens is the other way to review. It opens on what the change is trying to do and what is most likely to hurt, ranks findings by risk and by how much depends on the code they sit on, and zooms into the code only once it has said where to look. Nothing here is posted to GitHub, so reading a change in Lens costs the pull request nothing.

Lens is part of SourceAnt Cloud. The review endpoints underneath it are in the open core, so a self-hosted instance can build the same surface on the same data.

### How it works

Pick a repository and an open pull request. Lens loads the pull request with the reviews already on it, asks the API for a review of the current revision, and lays the two side by side against the diff.

The review is generated in preview mode: `POST /api/reviews/rerun` with `post: false`. Nothing reaches the pull request from here. A review already generated for the same commit is served again rather than paid for a second time, and the screen says so. Asking for a fresh one is an explicit action.

### Claims

Each finding becomes a claim: what it says, the file and lines it is anchored to, and the replacement code where the model produced one.

Review comments already written on overlapping lines, by people or by other agents, are attached to the claim they concern, so an argument that already happened is visible next to the finding rather than buried in the pull request timeline.

### How claims are ordered

A claim carries a position made of named dimensions, each cited to the records it was derived from, rather than one opaque score. Two dimensions compose it.

**Risk** is how badly this can go wrong, read from the reviewer's classification of the finding.

| Risk | Categories |
|---|---|
| High | `SECURITY`, `BUG` |
| Medium | `PERFORMANCE`, `REFACTOR` |
| Low | `CLARITY`, `STYLE`, `DOCUMENTATION`, `IMPROVEMENT` |

**Importance** is how much depends on the code the claim is anchored to: what [the system graph](systems.md) says consumes it, whether it sits on a published contract, and what knowledge records say about the part it belongs to. A bug in a script nobody calls and a bug on the payment path are the same risk and a different importance, and only the second one is worth interrupting someone for.

The two are kept apart on purpose. Collapsing them hides which one put a claim at the top, and a reader who cannot see that has no way to disagree with it.

A dimension with no evidence behind it contributes nothing rather than a guess, so a claim on code SourceAnt has never been told about is ordered on risk alone. Neither dimension is inferred from diff size, touched paths, or who wrote the change.

### Deciding

Two decisions are available on each claim.

**Sign** accepts the risk the claim describes. It does not fix or resolve anything; it records that someone read it and chose to proceed.

**Dismiss** rejects the claim, with a reason: wrong about the code, not a concern here, already handled, or out of scope for this change. The reason is the part worth capturing, since it is what an agent needs in order to stop raising it.

A decision belongs to the revision it was made on. When new commits move the pull request, decisions taken against the old revision are marked spent rather than carried forward.

Decisions are kept in your browser, so they do not follow you to another device.

### On your own instance

Lens is one client of the review endpoints, not a privileged one. `POST /api/reviews/rerun`, `GET /api/reviews/detail`, and `GET /api/reviews/pulls` are in the open core and answer anything holding a token, so generating a review without posting it needs nothing from the cloud. What Lens adds is the reading: the ranking, the anchored claims, and the record of what was signed or dismissed. See [API](api.md#reviews).
