## Repo Management

SourceAnt includes a built-in repo manager plugin that automates PR and issue triage. It is disabled by default.

### Enabling

```env
REPO_MANAGER_ENABLED=true
```

### Features

| Variable | Default | Description |
|---|---|---|
| `REPO_MANAGER_ENABLED` | `false` | Master switch for the repo manager |
| `REPO_MANAGER_PR_TRIAGE` | `true` | Enable PR duplicate detection |
| `REPO_MANAGER_ISSUE_TRIAGE` | `true` | Enable issue duplicate detection |
| `REPO_MANAGER_AUTO_LABEL` | `true` | Enable AI-powered auto-labeling |

### How It Works

When enabled, the repo manager:

- **Detects duplicates** - Compares new PRs and issues against existing ones to find potential duplicates using AI analysis.
- **Applies labels** - Automatically suggests and applies labels to PRs and issues based on their content.
- **Sends feedback** - Posts review comments and triage results directly on the PR or issue.

### Per-Repository Configuration

Settings can also be configured per repository using the Config model through the API, allowing different configurations for different repositories.
