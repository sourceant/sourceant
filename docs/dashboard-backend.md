# Dashboard backend

All hosted routes require a gateway-signed token carrying a workspace. Repository
filters narrow that workspace's connected repositories. Requirement writes use
the same workspace and repository scopes on HTTP and hosted MCP. Local MCP keeps
its existing local scopes.

## Requirements

The requirements API supports search and replacement at `/api/requirements`,
coverage at `/api/requirements/coverage`, and traceability at
`/api/requirements/links`. Delete a requirement or link by appending its ID to
the respective route. Pass `repo` for repository-specific records; omit it for
workspace records when writing, or all connected scopes when reading.

Search accepts `ids`, `kinds`, `statuses`, `priorities`, `limit` (up to 100), and
`offset`. Responses contain `data`; search also reports `total` and `has_more`.
Coverage keeps `code_links` and `test_links` separate.

Priority is indexed and remains available in `properties.priority`. The new
migration preserves existing values. Other properties remain unchanged.

`POST /api/requirements/import` accepts `{ "repo": "owner/repository" }` and
reads GitHub issues bearing `requirement` or `acceptance-criteria` labels with
the caller's GitHub credential. It does not change issues.

The gateway's `POST /api/requirements/assist` forwards to the memory plugin.
It accepts `summary`, `rationale`, `kind`, and `instruction`, returns a
`suggestion`, and does not save it. Usage is recorded as `requirement-assist`.

## Attachments

Upload a multipart `file` through the gateway at
`POST /api/artifacts/{namespace}/{name}`. The gateway forwards the file as a
streamed raw body with its media type. Direct core uploads use that raw-body
contract. Use namespace `requirements` for requirement attachments.

Set `ARTIFACT_MAX_UPLOAD_BYTES` on both gateway and core. The default is
2,000,000 bytes. PHP and the reverse proxy must also permit the configured
request size. Accepted formats are PDF; Word, Excel, and PowerPoint (legacy and
Open XML); OpenDocument text, spreadsheets, and presentations; RTF; plain text,
Markdown, CSV, TSV, JSON, XML, and YAML; PNG, JPEG, WebP, GIF, BMP, TIFF, and SVG;
and ZIP, gzip, tar, and 7z archives. The gateway detects the media type from the
file content; core owns the upload allowlist. Files are stored and downloaded
without parsing or extracting their contents.
Oversized uploads are rejected before writing to the artifact store.

The response contains metadata, a digest, a version, and an opaque `reference`.
Use that reference as `target_id` with `target_kind: "artifact"` when linking a
requirement. Removing the link preserves the artifact.

Download or delete at `/api/artifacts/{namespace}/{name}/{version}`. List at
`/api/artifacts/{namespace}`, optionally filtering by `name`. Every operation
uses the authenticated workspace. Downloads are attachments with content
sniffing disabled.

## Usage and topology

`GET /api/usage` accepts `period` (`7d`, `30d`, or `90d`; default `30d`),
`repository`, and `organization`. Filters apply to the entire report.
Costs are integer millionths; `unpriced_calls` counts missing provider prices.
Mixed currencies have separate `totals_by_currency`; their combined
`total.cost_micro` and `total.currency` are null. Organization grouping derives
from repository names and reports `organization_source: "repository_name"`.
Historical rows without workspace ownership are not assigned to a workspace.

`POST /api/topology/infer` accepts repository assets and `persist` (default
true). The gateway also exposes connections under a workspace's software-system
route. Persisted relationships remain pending and carry their evidence.

`POST /api/topology/suggest` accepts repository names and returns proposed
groups without writing systems. Each group reports its evidence sources and
repositories whose manifests were not read. Names alone receive low confidence.

## System creation

The gateway create route accepts `assets` and `adopt` with the system's name,
description, and optional parent. Send an `Idempotency-Key` header when retrying
the same request. Reusing the key for another body returns 409.

Topology changes commit atomically. The gateway keeps a durable operation until
local repository permissions have been recorded. Completed requests return 201
with the system. A pending operation returns 202 with `operation_id` and
`status`; validation failures return 422.

Read operation status at
`/api/workspaces/{workspace}/system-operations/{operation}`. Run Laravel's
scheduler so `systems:recover` retries pending operations every minute. The
command may also be run directly. Replays use the same operation ID and do not
create duplicate systems.

Apply the new core, memory, and gateway migrations before enabling these APIs.
The memory migration merges the new core migration branch. Plugin topology
adapters must support `TopologyBatchWriter` for atomic system creation.
