# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
