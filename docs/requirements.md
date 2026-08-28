## Requirements

A review that does not know what the software is meant to do can only judge how a change is written. SourceAnt records requirements alongside the code graph and links them to the files, tests, decisions, and systems that carry them.

A requirement is also an ordinary knowledge item of kind `requirement`, so anything that already searches knowledge finds it, and it relates to decisions and rules the same way everything else does.

### Recording one

Over MCP:

```text
Record a requirement: refunds settle within one business day.

Link that requirement to src/refund.py and to tests/test_refund.py.
```

The tools behind that:

| Tool | What it does |
|---|---|
| `put_requirement` | Create or update a requirement |
| `link_requirement` | Point it at code, a test, knowledge, or a system |
| `search_requirements` | Find requirements by identity, kind, status, or origin |
| `get_requirement_coverage` | What has code, what has tests, and what a change touches |

A requirement carries an `external_ref`, which is where it came from: an issue URL, a ticket id, a row in whatever the team already uses.

### Linking

A link points at one of four things:

| Target | Meaning |
|---|---|
| `code` | A file or symbol that implements the requirement |
| `test` | A file that verifies it |
| `knowledge` | A decision, rule, or constraint it relates to |
| `topology` | A system or service that delivers it |

Links are what coverage counts, so they are worth keeping accurate.

### Coverage

Coverage answers what the links already say, and nothing more:

```text
Which requirements does this change touch, and which of them have no test?
```

`get_requirement_coverage` reports, per requirement, how many code links and test links it has, and which paths those are. Ask with a set of changed paths and it narrows to the requirements those files carry.

It reports two lists directly: `uncovered`, requirements nothing implements, and `untested`, requirements with code but no linked test.

Whether a requirement is genuinely satisfied is a judgement rather than arithmetic, and is not something the open core claims to answer.

### In a review

When a change touches files linked to a requirement, that requirement is named in the review prompt along with its status and whether anything tests it. A change touching nothing tracked reads exactly as it did before.

### From GitHub issues

Teams that already write requirements as issues can read them in. Issues carrying a `requirement` or `acceptance-criteria` label become requirements, closed issues arrive as `met`, and the issue URL is kept as the `external_ref`. Nothing is written back, so the issue stays the place the team edits it.

Adapters for other trackers, continuous sync, and judged satisfaction are part of [SourceAnt Cloud](https://app.sourceant.ai).
