# Local code graph: status and handoff

Last updated 2026-08-29. Everything below was checked against a running stack,
not remembered.

## Where it is

| | |
|---|---|
| Phase 1 | merged, `sourceant/sourceant#126` |
| Core | `feat/local-client-api`, 712 passed, 3 skipped, lint clean |
| `sourceant/cli` | public MIT, 3 commits on `main` |
| `sourceant/agent` | public MIT, 8 commits on `main`, lint 0 issues |
| `sourceant/design` | public MIT, 1 commit on `main` |
| Pushed | **nothing** |

`sourceant/design` must be pushed before its pin resolves for anyone else. It
is consumed locally by an installed copy meanwhile.

## Running anything

```bash
# once
printf 'FROM ghcr.io/sourceant/sourceant:latest\nUSER appuser\nRUN pip install --user --no-cache-dir "litellm==1.96.0" black flake8 httpx pytest pytest-mock "pytest-asyncio==0.23.6"\nENTRYPOINT []\n' > Dockerfile.dev-local
docker build -t sourceant-dev:local -f Dockerfile.dev-local .
docker network create sa-dev-net
docker run -d --name sa-dev-pg --network sa-dev-net -e POSTGRES_USER=sourceant -e POSTGRES_PASSWORD=secret -e POSTGRES_DB=sourceant postgres:14
docker run -d --name sa-dev-redis --network sa-dev-net redis:7

# each run, migrations first or a dozen tests fail on a missing table
docker run --rm --network sa-dev-net -v "$PWD":/app -w /app \
  -e DATABASE_URL=postgresql://sourceant:secret@sa-dev-pg:5432/sourceant \
  -e REDIS_HOST=sa-dev-redis -e JWT_SECRET=test -e APP_ENV=test \
  sourceant-dev:local ./sourceant db upgrade head
```

The `litellm` pin matters: the published image ships 1.97.0, which cannot
construct a `ModelResponse`. CI runs migrations, `pytest`, then `code lint`;
the last is `black --check` and has failed while tests were green.

## Read the right branch

**`sourceant/dashboard` and `sourceant/memory` default to `dev` and `main`
respectively, and a local checkout goes stale.** Ask GitHub, never `origin/HEAD`:

```bash
gh repo view <owner/repo> --json defaultBranchRef --jq .defaultBranchRef.name
```

Reading `main` when the answer was `dev` caused every design complaint in this
session: an old renderer, wrong navigation, a hand-drawn logo.

## The three pieces

| Piece | Repo | Binary |
|---|---|---|
| CLI a person types | `sourceant/cli` | `sourceant` |
| Daemon that stays up | `sourceant/agent` | `sourceant-agent` |
| Indexer | this repo | `sourceant` |

`sourceant install` puts a core on the machine and writes
`~/.sourceant/config.json`; the agent reads it. `--runtime docker` works today.
`--runtime python` is written but blocked: this repo has no `pyproject.toml`
and nothing is on PyPI, so no wheel exists. It says exactly that.

A container mounts the installer's home at the path it already has, so one
registry of absolute paths means the same thing to either runtime, and runs as
`--user $(id -u):$(id -g)`, because the image's own user is uid 1000 and
anything it wrote into a mounted index on another host would belong to nobody.

## What the local surface serves

`/api/code/{repositories,graph,nodes,index}`, `/api/knowledge{,/initialize}`,
`/api/local/settings`. The registry is the authorization for reading: a scope is
never taken from the query string. Writing needs `sourceant serve`, because
whoever can register a path can then read it and the registry cannot vouch for
the route that fills it. Deployment scripts run uvicorn directly.

## The graph

The payload is `{id, name, kind, language?, labels, path, degree, community}`
plus `{communities, truncated, focus}`. A file is a file whatever it is written
in. Clustering is by modularity, moved from the memory plugin whole with its
tests.

Three things were wrong and are fixed:

- **Imports were stored as whole statements**, quotes and all, so nothing
  matched and every file was an island. They are read down to the module and
  resolved against the files the repository has. This repo went from 0 to 250
  file-to-file links. Ambiguous matches draw nothing: a wrong line reads as fact.
- **Minified bundles were indexed**, filling the graph with symbols called `q`
  and `eo`. Generated files are judged by content, not directory.
- **Imports of things nobody here wrote are not drawn.** They have nothing on
  the far side. The index keeps them.

## The view

Vue, built by Vite from `agent/ui/`, embedded with `go:embed`. `make build`
builds the view then the binary. Components and tokens come from
`@sourceant/design` pinned by commit, not copies.

Overview, Knowledge, Graphs, Repositories, Settings. Six graph modes; all but
2D are three-dimensional.

## Knowledge

Two layers. **Reading** takes what a repository already states: decision
records, `AGENTS.md`, conventions and constraints sections. Deterministic,
offline, every object points at its heading. **Asking** puts what it never wrote
down to a model, and appears only once one is configured.

Everything from either arrives `proposed`. The rule that rejects an inventory
summary rejects one from a model too.

## Bringing a model

`model.name`, `model.api_key`, `model.base_url`, user-scoped, all empty by
default. A key is written and never read back: the API answers `is_set`.
Onboarding asks once; somebody who skips is not asked again.

## What is left

- **Push.** Four branches, none pushed. `design` first, or the pin dangles.
- **A published image from this branch.** `ghcr.io/sourceant/sourceant:latest`
  predates the code routes entirely. Verified against a locally built
  `:local` tag.
- **Package this repo.** No `pyproject.toml`, so `--runtime python` cannot work
  and an installed console script cannot exist.
- **The memory `code_index` migration.** `clustering.py` is in core and memory
  can adopt it. The rest — deleting `adapter`, `transport`, `store`, and
  replacing them with a Memgraph repository fed by core's indexer — needs
  memory's Compose stack up to verify, and was not attempted blind.
- **The watcher.** Without it the graph is whatever the last index found.
- **One view across every repository.** The picker shows one at a time.
- **Skills in core**, discussed and not started: interfaces and primitives in
  the open core, read and share over MCP, declarative checks separated from
  executable ones so a cloned repository cannot run code by being opened.
- **Local review before a PR**, discussed and not started. `ChangeImpact` and
  the context provider already exist; the verdict is the part that must not
  quietly cross local and hosted.
- CI and releases for all three Go/JS repos.

## Two mistakes worth not repeating

Fixtures were once written to match an assumption instead of real usage. Every
fixture here is a captured payload from a running stack.

Comments and four commit messages in the public repos named private ones.
Scrubbed and the history rewritten before anything was pushed. A public repo
states what is true of itself and names nothing it cannot show.
