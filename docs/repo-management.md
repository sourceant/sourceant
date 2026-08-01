## Repo Management

The repo manager is the automatic half of triage. When a pull request or issue is opened or reopened, it checks whether the same thing is already open and labels it from the repository's own labels, so the queue a person works has already been narrowed. It is off by default.

```env
REPO_MANAGER_ENABLED=true
```

### What it does

**Duplicate detection.** A new pull request is compared against the other open pull requests, a new issue against the other open issues, up to 50 candidates. When the model reports a likely duplicate, one comment is posted naming what it matched. The comment carries a marker, so a later run edits it rather than posting again.

**Labelling.** Labels are suggested from the title and body, plus the diff on a pull request, then validated against the labels that already exist on the repository. Labels SourceAnt cannot find are discarded, so it never invents a label or reshapes your taxonomy. A repository with no labels gets none.

Both use the same LLM the reviewer uses, so provider configuration is shared.

### Settings

| Variable | Default | What it does |
|---|---|---|
| `REPO_MANAGER_ENABLED` | `false` | Master switch |
| `REPO_MANAGER_PR_TRIAGE` | `true` | Duplicate detection on pull requests |
| `REPO_MANAGER_ISSUE_TRIAGE` | `true` | Duplicate detection on issues |
| `REPO_MANAGER_AUTO_LABEL` | `true` | Labelling |

A repository can override any of these under the key `repo_manager.<name>`, for example `repo_manager.auto_label_enabled`. These keys are not part of the settings catalogue, so they are not reachable through the settings API yet; today they are read from stored configuration and otherwise fall back to the environment.

### Requirements

The GitHub App needs **Issues: read and write** and a subscription to the **Issues** event, on top of what code review needs. See [GitHub App Setup](github-app.md).

Only deliveries authenticated as the GitHub App are processed. Events from repositories connected through OAuth are skipped.

### Next steps

- [Triage](triage.md): the queue a person works, and the actions available there.
