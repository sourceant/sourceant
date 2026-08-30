# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0-beta.2] - 2026-08-30

### Added

- Reviewing a checkout on this machine, through the same reviewer a pull request
  goes through, with no forge and no pull request involved
- A review is kept and answers to a name, so an agent can ask for one over MCP and
  hand somebody a link that still opens it an hour later
- `review_working_tree` over MCP, and prompts a person can trigger rather than tools
  a model has to decide to call
- What a review found can be kept between runs and recognised again when the code
  moves under it, so a finding dismissed once stays dismissed. Off unless asked for
- Interfaces for what a deployment provides: which repositories a workspace covers,
  where its skills are kept, which model it bills, and who is asking. A hosted
  deployment answers from a database and a personal one from a disk
- Knowledge can be read across every registered repository at once rather than one
  at a time

### Changed

- The MCP server is part of the core rather than beside it, and anything able to do
  something contributes the tools for it
- A large change is read in parts sized to what a review can take in, rather than to
  what the model will accept. On a change of a hundred and thirteen files the whole
  diff yielded between nothing and ten findings and the parts yielded thirty four
- The parts are read at the same time rather than one after another
- The reviewer, the folders a workspace covers and the model it bills are resolved
  through the registry rather than by reaching for a class

### Fixed

- A working checkout is indexed as it is rather than as a commit, and reviews asked
  for it by commit. The code graph was never reachable from a review of a checkout
- The claims a suggestion is checked against were optional in the schema a model
  answers, so it never sent them and nothing was ever checked
- Names bound at the left margin were recorded by nothing, so a claim that a constant
  is undefined could not be contradicted by the file defining it
- Skills are matched on letters rather than on A to Z, so a description written in
  any other language is no longer split at every accent
- A page of findings is asked of the database rather than fetched whole and sliced
- The local API is published on loopback rather than on every interface, and the MCP
  endpoint refuses a host it does not recognise

## [1.0.0-beta.1] - 2026-08-26

### Added

- Software topology: systems, the assets that belong to them, and the relationships
  between them, readable and writable through the API and durable across restarts
- Knowledge that outlives a review: decisions, constraints and contracts captured from
  pull requests, kept under a scope, and traversable by status
- Engineering context served over MCP, so an editor or agent can ask what a repository
  knows without going through a review
- Structural evidence drawn from the code itself in thirteen languages, cached so
  grammars are built once rather than per review
- Contract analysis for OpenAPI, AsyncAPI, GraphQL, SQL and gRPC, versioned so a change
  to a published interface is recognised as one
- A plugin system with pip-based discovery, letting a distribution add capabilities
  without a fork, and letting a repository or its organisation configure the core
- Repository management: pull request triage, issue triage, auto-labelling, and the
  findings already posted on a pull request served back
- `GET /health` and `GET /health/ready`, the second reaching the database, the queue,
  the graph store and every loaded plugin, and answering 503 when one of them cannot
  be reached
- Review previews, so a review can be generated and inspected without being posted
- Incremental review, so a re-reviewed pull request is judged on what changed
- Portable graph snapshots, streamed and validated, with interrupted writes recovered
- MySQL alongside PostgreSQL for knowledge storage
- Pagination on the list endpoints
- Gateway-signed deliveries, so the agent can refuse work that did not pass identity
  and entitlement

### Changed

- `GET /` returns a service index naming the service, version, environment and health
  path, in place of a fixed string
- `GET /repository-events` requires authentication. It previously returned every
  repository event to any caller
- Plugins are discovered as installed packages rather than files on a path. A plugin
  that was dropped into a directory is no longer found
- File uploads were removed from the review pipeline. Reviews are built from the diff
  and from evidence read out of the repository
- Repository packing moved from repomix to a tool-agnostic packer backed by yek
- Reviews reject praise and re-stated changes, and skip comments already made on an
  earlier pass, so a re-review is quieter than the first
- Claims are checked against the code before a comment is posted, and a finding whose
  evidence is uncertain is kept but marked rather than dropped
- Enterprise image builds moved out of this repository

### Fixed

- A readiness probe no longer reports a dependency as down when a pooled connection was
  replaced underneath it
- An unreachable graph store answers with its own status instead of a bad request
- Database migrations run to completion instead of stopping partway
- The model client is pinned to a version that can build a response, after a release
  broke reviews without any code change
- A provider answer that is not the list it should be no longer ends the run, and the
  whole list a provider offers is read rather than its first page
- An empty webhook secret is treated as no secret rather than as a secret that matches
  nothing
- Topology traversal stops at its entity limit instead of walking the graph
- Reviews are reused rather than regenerated, so the same change is not paid for twice

### Security

- Tracebacks are logged rather than returned in error responses
- The graph store's own failure detail, which named its host and port, is kept out of
  responses and written to the log instead
- `JWT_SECRET` is validated at startup, so a deployment missing it fails to start
  rather than starting healthy and refusing every authenticated request
- Source excerpts are bounded, and subprocess handling in the worker was hardened

## [0.0.1] - 2026-02-06

### Added
- Automated code review for GitHub pull requests via webhooks
- Multi-model LLM support through LiteLLM (Gemini, Claude, OpenAI, DeepSeek, and more)
- GitHub App integration for seamless repository access
- Background job processing with Redis/RQ
- Inline PR comment reviews with code suggestions
- Configurable review settings per repository
- Stateless mode for simplified deployments
- CLI tool for database migrations and code linting
- Docker and Docker Compose support for development and production
