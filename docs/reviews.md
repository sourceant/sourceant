## Code Review

Once a repository is connected, SourceAnt reviews its pull requests without being asked. This page covers what it reacts to, what it posts, and what you can change about that.

### What triggers a review

| Event | Reviewed |
|---|---|
| Pull request opened | Yes |
| New commits pushed (`synchronize`) | Yes |
| Pull request reopened | Yes |
| Draft marked ready for review | Yes |
| Draft opened | Only with `REVIEW_DRAFT_PRS=true` |
| Already merged | No |

The review runs in the background, so the webhook is answered immediately and the result appears on the pull request a little later. In the default Redis queue mode an `rq` worker has to be running, or nothing is processed.

Deliveries to the OAuth webhook endpoint are recorded as activity but not reviewed.

### What gets posted

Two things, both tied to the same run.

**An overview comment**, created once and edited in place on later runs, so a pull request carries one summary rather than a new one per push. It contains the overview, key improvements, minor suggestions, and critical issues. A later run that says the same thing in different words leaves the existing comment alone.

**A review with inline comments**, submitted with the verdict as its event: `APPROVE`, `REQUEST_CHANGES`, or `COMMENT`. An inline comment carries the finding, plus a GitHub suggestion block where the model produced replacement code, so it can be committed from the pull request. A verdict of `REQUEST_CHANGES` is a real changes-requested review: on a branch with protection that requires review resolution, it holds the merge until it is resolved or dismissed.

Every suggestion is classified as one of `REFACTOR`, `STYLE`, `PERFORMANCE`, `BUG`, `SECURITY`, `CLARITY`, `DOCUMENTATION`, or `IMPROVEMENT`.

If posting the review fails, the same content is posted as a single comment instead, so a run is never lost silently.

### What is filtered before posting

| Filter | Effect |
|---|---|
| Praise detection | A comment that only says the code is fine is dropped rather than posted. `POSITIVE_SENTIMENT_THRESHOLD` (default `0.3`) sets how positive a comment has to read before it counts as praise |
| Missing anchor | A suggestion that does not quote the code it wants changed cannot be placed reliably. `REVIEW_MISSING_EXISTING_CODE_POLICY` decides: `drop` (default) discards it, `warn` keeps it and says so, `keep` keeps it silently |
| Already said | A suggestion matching one SourceAnt already posted on the pull request is dropped, and the verdict is recalculated from what survives |
| Repeat approval | A second approval on a pull request SourceAnt has already approved is downgraded to a comment |

### Large pull requests

A diff that fits inside `LLM_TOKEN_LIMIT` is reviewed in one pass. A larger one is reviewed file by file instead, so a big pull request costs more model calls rather than losing part of the diff. Nothing is truncated.

### Pushing more commits

When new commits arrive on a pull request SourceAnt has already reviewed, it reviews the difference between the last commit it saw and the new head, rather than the whole pull request again. A force push that makes that range meaningless falls back to the full diff.

### Reuse

A review generated through the API is kept per commit and served again on a repeat request for the same commit, so reading a pull request in [Lens](lens.md) twice does not pay for two model runs. `review.reuse_days` (default 7) sets how long one stays reusable, per repository or per organization. See [API](api.md#settings).

Reuse is best effort: when Redis is unavailable the review is simply generated again.

### Settings

| Variable | Default | What it does |
|---|---|---|
| `REVIEW_DRAFT_PRS` | `false` | Review draft pull requests |
| `POSITIVE_SENTIMENT_THRESHOLD` | `0.3` | How positive a comment must read to be treated as praise and dropped |
| `REVIEW_MISSING_EXISTING_CODE_POLICY` | `drop` | What happens to a suggestion with no anchoring code: `drop`, `warn`, `keep` |
| `LLM_TOKEN_LIMIT` | `131072` | Diff size that still fits a single-pass review |

### Limits

- SourceAnt reads the diff. It does not check out your repository, execute it, or run its tests, so a fault that only shows at runtime is out of reach.
- Findings are a first pass, not a gate. A human reviewer still owns the decision.

### Next steps

- [Lens](lens.md): read a change by risk instead of by diff, in SourceAnt Cloud.
- [Repo Management](repo-management.md): duplicate detection and labelling for pull requests and issues.
- [Configuration](configuration.md): every setting in one place.
