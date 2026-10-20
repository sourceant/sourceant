## Lens

Inline comments on a diff assume someone will open that diff and read it. When most of the change was written by an agent, and the provider will not render a diff that size anyway, the review has no working consumer: the person cannot read it, and the agent that wrote the change has to scrape its own pull request page to find out what was said.

Lens is the other way to review. It opens on what the change is trying to do, what behaves differently because of it, what is most likely to hurt, and what code outside the change depends on it. The diff is still there, underneath, as the evidence each of those points at. Nothing here is posted to GitHub, so reading a change in Lens costs the pull request nothing.

Lens is part of SourceAnt Cloud. The review endpoints underneath it are in the open core, so a self-hosted instance can build the same surface on the same data.

### How it works

Pick a repository and an open pull request. Lens loads the pull request with the reviews already on it and asks for two things at once: a review of the current revision, and what the change reaches in the repository.

The page leads with the intent, one sentence on the problem the change solves, and the review's overview. Below it, one list holds everything worth reading, in this order:

| Group | What it is | Where it comes from |
|---|---|---|
| Behavior | Something a user or caller can observe, before and after | The review; each one names the changed lines that make it, and one that cannot is dropped |
| Risks | The review's findings | The review |
| Consumers | Code the pull request does not change but that calls what it does | The code index, or names matched in the source, each labelled with which |
| Notes | Remarks that are not findings | The review |
| Files | Every changed file, including ones nothing was said about | The diff |

Selecting one shows it in the middle, with the code it rests on. The right side shows its evidence: whether a finding is anchored and how often it happens, which changed tests name the file, and who calls the definitions it touches. The full diff opens at the bottom, on the line the selection points at.

The review is generated in preview mode: `POST /api/reviews/rerun` with `post: false`. Nothing reaches the pull request from here. A review already generated for the same commit is served again rather than paid for a second time, and the screen says so. Asking for a fresh one is an explicit action.

### Claims

Each finding becomes a claim: what it says, the file and lines it is anchored to, and the replacement code where the model produced one.

Review comments already written on overlapping lines, by people or by other agents, are attached to the claim they concern, so an argument that already happened is visible next to the finding rather than buried in the pull request timeline.

### How claims are ordered

Each finding states what causes it, who is worse off, what goes wrong and how often. The review turns those answers into a severity, and Lens orders claims by it:

| Severity | Shown as |
|---|---|
| Blocking | High risk |
| Advisory | Medium risk |
| Nit | Low risk |

A review generated before findings carried a severity is ordered by category instead: `SECURITY` and `BUG` high, `PERFORMANCE` and `REFACTOR` medium, the rest low. A finding with no category is shown as risk unstated, which is not the same as low. Order is never inferred from diff size, touched paths, or who wrote the change.

Consumers are listed separately rather than folded into the order, so a reader can see what depends on a finding's code without the ranking hiding why a claim is where it is.

### Deciding

Two decisions are available on each claim.

**Sign** accepts the risk the claim describes. It does not fix or resolve anything; it records that someone read it and chose to proceed.

**Dismiss** rejects the claim, with a reason: wrong about the code, not a concern here, already handled, or out of scope for this change. The reason is the part worth capturing, since it is what an agent needs in order to stop raising it.

A decision belongs to the revision it was made on. When new commits move the pull request, decisions taken against the old revision are marked spent rather than carried forward.

Decisions are kept in your browser, so they do not follow you to another device.

### On your own instance

Lens is one client of the review endpoints, not a privileged one. `POST /api/reviews/rerun`, `GET /api/reviews/detail`, `GET /api/reviews/impact`, and `GET /api/reviews/pulls` are in the open core and answer anything holding a token, so generating a review without posting it needs nothing from the cloud. What Lens adds is the reading: the ranking, the anchored claims, and the record of what was signed or dismissed. See [API](api.md#reviews).
