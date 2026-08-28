<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/sourceant-logo-dark.svg">
    <img src="docs/assets/sourceant-logo.svg" alt="SourceAnt" width="720">
  </picture>
</p>

<p align="center"><strong>An indexed graph of your codebase and the decisions behind it, served to any AI tool over MCP.</strong></p>

SourceAnt reads your repositories into a durable graph of files, symbols, imports, and definitions, and keeps the decisions, rules, API contracts, and system topology that explain them alongside it. Any MCP client can search that graph, walk it, and write to it, so what your team knows outlives the session that learned it.

It runs on a laptop with nothing configured and no account. Code review, issue triage, and system mapping are applications built on the graph, not the product.

## Start here

```bash
git clone https://github.com/sourceant/sourceant.git
cd sourceant
pip install -r requirements.txt

sourceant db upgrade head          # keeps its data in your user directory
sourceant repo add ~/code/your-project
sourceant index ~/code/your-project
```

That parses every file the repository does not ignore and stores the result. Index a second repository and it joins the same graph rather than starting another one, so a question can cross a repository boundary.

Running it again reparses only what changed:

```bash
sourceant index ~/code/your-project --update
```

Point an MCP client at the knowledge server and it can use all of it:

```bash
python -m src.mcp_server
```

See [Local index](docs/local-index.md) for the whole command set, and [Knowledge and context](docs/context.md) for what the server exposes.

## What the graph holds

| Part | What it is | Where it is kept |
|---|---|---|
| Code structure | Files, symbols, imports, definitions, references | Your database, from `sourceant index` or a SCIP index |
| Knowledge | Decisions, rules, constraints, conventions, and how they relate | Your database |
| Requirements | What the software is meant to do, and what carries it | Your database |
| Topology | Systems, services, components, and their dependencies | Your database |
| Contracts | API surfaces and what changed between versions | In-memory in the core; a plugin makes it durable |
| Review findings | What a review raised and what became of it | In-memory in the core; a plugin makes it durable |

Everything is filed under a **scope**, an open map of key-value pairs you choose. A personal project can use `{"project": "shop"}`. An integration can use `{"organization": "acme", "repository": "acme/billing"}`. Nothing in the core needs to know which keys you picked.

## Knowledge over MCP

| Tool | Purpose |
|---|---|
| `search_code` | Find files and symbols by label and property |
| `trace_code` | Walk the neighbourhood around a symbol |
| `put_knowledge` | Record a decision, rule, constraint, or convention |
| `put_knowledge_relationship` | Connect knowledge with `depends_on`, `supports`, `contradicts` |
| `search_knowledge` | Find knowledge by scope, identity, type, or property |
| `put_topology_entity` | Record a part of the system |
| `put_topology_relationship` | Record how two parts relate |
| `traverse_topology` | Walk the system graph from a set of seeds |
| `put_requirement` | Record what the software is meant to do |
| `link_requirement` | Point a requirement at the code or test that carries it |
| `search_requirements` | Find requirements by identity, kind, status, or origin |
| `get_requirement_coverage` | What has code, what has tests, what a change touches |
| `get_context` | Combine any of the above into one bounded pack |

To an MCP-enabled agent this is ordinary instruction:

```text
Remember that project shop uses signed webhook requests. Store it as an approved decision.

Connect the signed webhook decision to the rule that rejects unsigned requests.

Get the approved knowledge related to the signed webhook decision before changing its handler.
```

Storage is replaceable. Inject another `KnowledgeRepository`, `TopologyRepository`, or `CodeIndexRepository` and the same tools keep working against it.

## Built on the graph

- **[Code review](docs/reviews.md)** reads the graph for structure around a change before it comments.
- **[Issue triage](docs/triage.md)** finds duplicates and labels what comes in.
- **[Systems](docs/systems.md)** maps services and dependencies independently of repository layout.
- **[Repo management](docs/repo-management.md)** automates the housekeeping around both.

These need model access and, for GitHub, an app. Both are optional; the graph is not.

## Documentation

| | |
|---|---|
| [Quick start](docs/quick-start.md) | Get something running |
| [Local index](docs/local-index.md) | Index repositories on your own machine |
| [Knowledge and context](docs/context.md) | The knowledge server and context packs |
| [Requirements](docs/requirements.md) | What the software is meant to do |
| [Systems](docs/systems.md) | Software topology |
| [Configuration](docs/configuration.md) | Every environment variable |
| [GitHub App setup](docs/github-app.md) | Self-hosted GitHub integration |
| [API](docs/api.md) | HTTP endpoints |
| [Deployment](docs/deployment.md) | Images, compose, and running it as a service |

Models come through [LiteLLM](https://docs.litellm.ai/docs/providers), so Gemini, Anthropic, OpenAI, DeepSeek, Mistral, and 100+ other providers work by setting two variables.

## SourceAnt Cloud

The core is MIT licensed and self-hostable in full. [SourceAnt Cloud](https://app.sourceant.ai) runs the same engine with a managed layer on top: memory your team curates, contract analysis and structural indexing at scale, the explorable graph, workspaces and roles, and analytics.

## Contributing

1. Fork the repository.
2. Create a branch: `git checkout -b feature/your-feature`.
3. Commit your changes.
4. Push and open a pull request.

Tests and formatting are what CI checks:

```bash
docker compose exec app pytest src/tests/ -v
docker compose exec app sourceant code lint
```

## License

MIT. See [LICENSE](LICENSE.md).

## Contact

- **Email**: hello@sourceant.ai
- **Issues**: [Open an issue](https://github.com/sourceant/sourceant/issues)

<a href="https://github.com/sourceant/sourceant/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=sourceant/sourceant" />
</a>

Maintained by [WhileSmart](https://whilesmart.com).
