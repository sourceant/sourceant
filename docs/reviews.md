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

**An overview comment**, created once and edited in place on later runs. It contains a short overview, key improvements, regressions, critical issues, and minor suggestions. Empty lists are omitted. Replacement code stays with the inline review or findings comment.

Under Review settings, `review.include_nitpicks` defaults to off. Enable it to include style, naming, clarity, documentation, refactoring, and optional improvement suggestions. Without it, model findings are limited to bugs, security issues, and material performance problems. Deterministic tool results remain available.

Under Review settings, `review.overview.show_minor_suggestions`, `review.overview.show_critical_findings`, and `review.overview.show_regressions` control those overview sections independently. All default to on and only show a section when it has content. Hiding a section does not remove findings from the review or change its verdict.

**A review with inline comments**, submitted with the verdict as its event: `APPROVE`, `REQUEST_CHANGES`, or `COMMENT`. An inline comment carries the finding, plus a GitHub suggestion block where the model produced replacement code, so it can be committed from the pull request. A verdict of `REQUEST_CHANGES` is a real changes-requested review: on a branch with protection that requires review resolution, it holds the merge until it is resolved or dismissed.

Every suggestion is classified as one of `REFACTOR`, `STYLE`, `PERFORMANCE`, `BUG`, `SECURITY`, `CLARITY`, `DOCUMENTATION`, or `IMPROVEMENT`.

A native review is attempted first. Findings without a valid inline location are included in its body. If GitHub rejects the inline payload with a non-rate-limit 422 response, posting retries as a native review with every finding in its body. Only if that review also receives a non-rate-limit 422 response does a separate findings comment explain the failure and include file links. This fallback does not submit an approval or a changes-requested review. Large findings sets are split across numbered comments. Retries resume the same delivery and reuse comments already posted for it; a later review gets its own findings comment.

With the database queue, generation and posting are separate jobs. Rate limits delay posting with increasing waits; they do not rerun the model. A review is recorded as completed only after its findings and overview have been delivered. A failed posting job remains visible in the job queue. Outside database mode, transient posting failures retry the same generated review up to three attempts, respecting GitHub’s retry delay and increasing waits. Permanent failures stop immediately.

### What is filtered before posting

| Filter | Effect |
|---|---|
| Praise detection | A comment that only says the code is fine is dropped rather than posted. `POSITIVE_SENTIMENT_THRESHOLD` (default `0.3`) sets how positive a comment has to read before it counts as praise |
| Missing anchor | A suggestion that does not quote the code it wants changed cannot be placed reliably. `REVIEW_MISSING_EXISTING_CODE_POLICY` decides: `drop` (default) discards it, `warn` keeps it and says so, `keep` keeps it silently |
| Already said | A suggestion matching one SourceAnt already posted on the pull request is dropped, and the verdict is recalculated from what survives |
| Repeat approval | A second approval on a pull request SourceAnt has already approved is downgraded to a comment |

### Expert passes

`review.expert_passes` controls which expert reviews run. Its default, `auto`, keeps automatic skill selection. Set exact skill IDs, one per line, to run only those passes; set an empty string to disable expert passes. The general review and guidance still run. Named passes are not restricted by the automatic selector's five-skill limit, but their file patterns still apply. Unknown IDs fail the review with an error naming them.

The setting supports repository, workspace, organization, and user scopes. A more specific value replaces the inherited selection. A local review's explicit skill selection takes precedence for that run.

Built-in skills have the origin `system`; this describes who supplies the skill, not membership in a software system. The built-in contract consistency and duplicate implementation passes also apply to standalone repositories. Connected-system context depends on the available graph and source evidence, not a parent requirement in skill selection.

### Large pull requests

`review.reading_budget` controls the token budget per batch. General and specialist passes run concurrently; specialist passes with explicit skill path patterns read only matching files, and an overview is prepared alongside PR review. All model calls in a process share `REVIEW_MODEL_CONCURRENCY` slots (default 6). Multiple worker processes each have their own limit.

Lockfiles are excluded from model passes by default. Dependency manifests remain included. `review.exclude_patterns` is a newline-separated list of glob patterns, configurable for a user, repository, workspace, or organization. Setting a list replaces the inherited list; setting an empty string includes all files. Excluded paths are recorded in review coverage.

A pattern without `/`, such as `package-lock.json`, matches that filename at any depth. Patterns with `/` match repository-relative paths; `*` can span directories and a leading `**/` also matches the repository root. For example:

```text
package-lock.json
yarn.lock
pnpm-lock.yaml
vendor/*
**/*.min.js
```

Use specific generated-file patterns rather than excluding every `.json` or `.yaml` file, which would also remove manifests and application configuration.

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
| `REVIEW_MODEL_CONCURRENCY` | `6` | Maximum concurrent model calls per process |
| `LLM_PROBE_TIMEOUT` | `15` | Seconds a provider gets to answer a model check |
| `LLM_RESOLVE_TIMEOUT` | `5` | Seconds a custom endpoint's name gets to resolve |
| `LLM_ALLOW_PRIVATE_ENDPOINTS` | `false` | Accept a model endpoint on a private address |

### Limits

- SourceAnt reads the diff. It does not check out your repository, execute it, or run its tests, so a fault that only shows at runtime is out of reach.
- Findings are a first pass, not a gate. A human reviewer still owns the decision.

### Next steps

- [Lens](lens.md): read a change by risk instead of by diff, in SourceAnt Cloud.
- [Repo Management](repo-management.md): duplicate detection and labelling for pull requests and issues.
- [Configuration](configuration.md): every setting in one place.
