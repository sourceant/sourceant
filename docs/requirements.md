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
| `search_requirements` | Find requirements by identity, type, status, or origin |
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

### Where they are filed

A requirement belongs to a repository, not to a commit, so it is stored under a scope of `{"repository": "acme/billing"}` with no revision. Code structure is the opposite: it is pinned to the revision it was read at, because it changes with every commit.

A review reads both, each at its own scope. That is why a requirement recorded once keeps applying to every later change.

### In a review

A review is handed the requirements a change is answerable to, and judges the change against them. Deciding which requirements those are is a separate job, behind `RequirementSelector`.

The core ships one selector, which returns the requirements linked to the files the change touches. It is exact and shallow: a requirement nobody linked to those files is not returned, however clearly the change addresses it. A change touching nothing tracked reads exactly as it did before.

Reading intent out of a change, rather than following links somebody drew, is a different problem. An installation that can do it registers its own selector and the review uses that instead. The selector is told the scope, the changed paths, and the title and description of the change.

### From GitHub issues

Teams that already write requirements as issues can read them in:

```bash
./sourceant requirements import acme/billing --dry-run
./sourceant requirements import acme/billing
```

Issues carrying a `requirement` or `acceptance-criteria` label become requirements, closed issues arrive as `met`, and the issue URL is kept as the `external_ref`. Pass `--label` to choose different ones. Nothing is written back, so the issue stays the place the team edits it.

Each import replaces what it previously read for the same issue, so running it again is safe.

Adapters for other trackers, continuous sync, and judged satisfaction are part of [SourceAnt Cloud](https://app.sourceant.ai).

### Behavior scenarios

A requirement can include Gherkin examples of business rules or expected behavior. They do not need an automated test.

```gherkin
Feature: Workspace invitations
  @id:invite
  Scenario: Invite a teammate
    Given I own a workspace
    When I invite a teammate
    Then they receive an invitation
```

In the dashboard, open a requirement's **Scenarios** tab. Edit step text in **Steps**, or use **Gherkin** for rules, backgrounds, tables, and scenario outlines. Import and export `.feature` files there. **Draft with Memory** produces an unsaved proposal.

The **References** view shows code and test links for each scenario. **Traceability** follows a requirement or scenario to its references. A linked test does not mean it ran or passed. When a scenario changes, its earlier references need checking.

Local commands attach scenarios to an existing requirement:

```sh
./sourceant requirements validate-scenarios invitations.feature
./sourceant requirements import-scenarios acme/app invitations invitations.feature
./sourceant requirements export-scenarios acme/app invitations
```

Over MCP, use `parse_requirement_scenarios` to validate source, then `put_requirement` with `properties.behavior.source` to save it. `search_requirements` returns the scenarios. Add `properties.scenario_id` to `link_requirement` to associate a reference with one scenario. Supplying its `scenario_revision` rejects a link if the scenario changed since it was read.

Scenario names must be unique within a rule unless each has a distinct `@id:` tag. An explicit tag keeps the identity when renaming a scenario. Documents are limited to 100 KB and 100 scenarios. Parsing and validation do not call a model or execute tests.

### Acceptance and evidence

An acceptance criterion states what must be true and how it will be checked. Its purpose distinguishes verification (meeting the specification) from validation (meeting stakeholder needs). Methods include tests, analysis, inspection, demonstration, and review. Criteria can refer to behavior scenarios and can be revised or retired without erasing their history.

An evidence record stores an observation: results, source reference, observation time, build or document revision, environment, and producer. Types are extensible, for example a benchmark, test run, inspection report, or stakeholder approval. Records are immutable and reusable across requirements in the same scope. A corrected or repeated observation gets a new record.

An assessment binds evidence to a particular requirement and criterion revision. It records an explicit decision, rationale, and author. Outcomes are passed, failed, inconclusive, or waived. Reassessment explicitly supersedes the preceding decision; failed results and waivers remain in history. A waiver is never reported as a pass. Editing a requirement makes earlier assessments stale until the criteria and assessments are reviewed.

A requirement has no automatic acceptance result merely because code or tests are linked. Its acceptance view combines the recorded decisions for all active criteria. This is a summary of supplied assessments, not an independent judgement that the requirement is satisfied. Automated interpretation and drafting remain separate Memory capabilities.

Relationships carry a meaning separately from their target type, such as derived_from, depends_on, implemented_by, has_test_case, or mitigates. Both vocabularies can be extended. Existing code, test, knowledge, topology, and artifact links remain references; upgrading does not invent observations or passing assessments from them.

The dashboard's **Acceptance** tab defines criteria, records or reuses evidence, records assessments, and displays history. MCP exposes the same operations through `put_acceptance_criterion`, `record_requirement_evidence`, `get_requirement_evidence`, `search_requirement_evidence`, `assess_requirement_criterion`, `get_requirement_assurance`, and `get_requirement_assessment_history`. History and evidence browsing are paginated. Requirement baselines used by criteria and assessments are preserved with that history.

SQL storage requires the additive `requirement_assurance_001` migration. It creates baseline, criterion revision, evidence, assessment, and assessment-to-evidence tables; existing requirements and links are preserved. Reads and writes stay within the authenticated workspace and selected repository. The model does not execute tests or call a model to decide outcomes.
