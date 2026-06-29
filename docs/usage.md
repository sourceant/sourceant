## Usage

SourceAnt runs automatically. Once connected to your repository through a GitHub App or webhook, every pull request triggers an automated code review. Here is the full workflow from push to result.

### The Review Cycle

**1. Open a Pull Request**

Push code and open a PR on any connected repository. SourceAnt receives the event within seconds.

**2. Review Runs Automatically**

SourceAnt analyzes the diff against the base branch. It evaluates every changed line for:

- Code quality and style issues
- Logic errors and edge cases
- Security vulnerabilities
- Performance concerns
- Merge conflicts and integration risks

**3. Read the Summary Comment**

SourceAnt posts a single summary comment on your PR. The comment includes:

- An overall assessment (approve, changes requested, or info-only)
- Key findings grouped by severity
- Specific line-level suggestions where applicable

Inline suggestions appear as review comments on the relevant lines of code. Each suggestion includes:

- The issue category (style, security, logic, performance)
- A proposed change with the exact diff
- A brief explanation of why the change matters

**4. Act on Feedback**

Apply the suggested changes, dismiss false positives, or adjust SourceAnt's settings to match your team's preferences. SourceAnt will update its review if the PR changes.

**5. Merge**

Once all concerns are addressed, merge normally. SourceAnt is passive. It never blocks a merge or modifies your code without approval.

### Understanding Review Comments

Each review comment follows a consistent structure:

```
[Category] Brief description of the issue

The relevant code line(s)

Suggested fix:
[code diff with the proposed change]

Why it matters: One-sentence explanation.
```

**Categories:**

| Category | What it flags |
|---|---|
| `security` | SQL injection, credential leaks, unsafe deserialization, missing input validation |
| `logic` | Off-by-one errors, null pointer risks, incorrect conditionals, race conditions |
| `style` | Naming conventions, formatting, dead code, overly complex expressions |
| `performance` | N+1 queries, unnecessary allocations, missing cache headers, large payloads |
| `reliability` | Missing error handling, resource leaks, timeout risks, idempotency gaps |

### Configuring Review Behavior

SourceAnt's review strictness is adjustable per repository. Set these via environment variables or the API:

**Suppress positive comments:**

Show only issues and skip diagnostics that pass:

```env
SUPPRESS_PRAISE=true
```

**Skip draft PRs:**

Avoid reviewing work-in-progress PRs:

```env
SKIP_DRAFTS=true
```

**Minimum change threshold:**

Only review PRs with at least a minimum number of changed lines:

```env
MIN_CHANGES=5
```

These settings go into `.env` or the repository Config model.

### Common Workflows

**Quick internal review:** Push a PR on a feature branch, review the summary, merge.

**Team review workflow:** Push a PR. SourceAnt provides a first pass. Your team reviews the automated findings alongside manual review, catching issues the AI missed or disagreeing with false positives.

**Security audit mode:** Enable strict checks and suppress praise for a full diff audit before a release branch merge.

### Next Steps

- Set up the [Repo Manager](repo-management.md) for automated PR and issue triage.
- Add custom plugins to extend review rules.
- Adjust review thresholds per repository through the API.

### Limitations

- Reviews are best-effort. SourceAnt may miss context-specific issues that a human reviewer would catch.
- Very large diffs (over 2000 lines) may be truncated to stay within model token limits.
- SourceAnt only reviews the diff. It does not execute your code or run tests.
