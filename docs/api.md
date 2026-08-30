## API

Every endpoint on this page is in the open core and available on any instance you run. The interactive reference for a running instance is at `/docs`.

SourceAnt Cloud adds surfaces of its own on top, including organization-wide knowledge, workspaces, and analytics. Those are not part of this repository and are not documented here.

### Authentication

Every route under `/api/` expects a bearer token, except the GitHub webhooks and the local code index:

```
Authorization: Bearer <token>
```

The token is a JWT signed with `JWT_SECRET` using HS256. It must carry `sub` and `exp`. Two optional claims decide what a token can reach:

| Claim | Needed for |
|---|---|
| `github_token` | Anything that talks to GitHub: reviews, triage, repository listing, dependency inference. Without it a list comes back empty and an action is refused |
| `scope.workspace_id` | The topology routes, which keep one graph away from another by it. A token without it is refused with 403. The core does not interpret the value or model anything behind it; in the cloud it is your workspace there, and on your own instance it is any stable string you choose |

The webhook endpoints authenticate differently, by HMAC signature. See [GitHub App Setup](github-app.md).

The `/api/code/` routes take no token, because a laptop has nobody to issue one. What limits them instead is the registry: they read a repository only when `sourceant repo add` has registered it on that machine, and they take no scope from the caller. On a server where nobody registered a repository they return 404 to everything. See [Local index](local-index.md).

### Response shape

```json
{"status": "success", "message": "Request was successful", "data": {}}
```

Errors answer with `status`, `message`, and `error`. List endpoints put a page inside `data`:

```json
{"items": [], "total": 0, "page": 1, "size": 50, "pages": 0}
```

Pass `page` and `size` as query parameters.

### Reviews

| Endpoint | Purpose |
|---|---|
| `GET /api/reviews?repo=owner/name` | Reviews SourceAnt has generated, with live pull request state |
| `GET /api/reviews/pulls?repo=owner/name` | Open pull requests across the given repositories |
| `GET /api/reviews/detail?repo=owner/name&number=1` | One pull request with the reviews and comments on it |
| `POST /api/reviews/rerun` | Generate a review for a pull request |

`repo` may be repeated to work across several repositories.

`rerun` takes `{"repo": "owner/name", "number": 1}` with two flags. `post` defaults to `false`, which returns the review without touching GitHub; set it to `true` to publish. `refresh` defaults to `false`, which serves a review already generated for the same commit; set it to `true` to pay for a new one. The response reports `cached` so a caller can tell which it got. See [Code Review](reviews.md) and [Lens](lens.md).

### Triage

| Endpoint | Purpose |
|---|---|
| `GET /api/triage?repo=owner/name` | Open issues across the given repositories |
| `GET /api/triage/detail?repo=owner/name&number=42` | One issue with its comments |
| `POST /api/triage/action` | Comment on, label, or close an issue |

See [Triage](triage.md).

### Repositories

| Endpoint | Purpose |
|---|---|
| `GET /api/repos?q=` | Repositories the caller's GitHub token reaches, with connected status |
| `GET /api/repos/connected` | Connected repositories, from SourceAnt's own records |
| `POST /api/repos/connect` | Connect a repository to the current user |
| `DELETE /api/repos/{id}/disconnect` | Disconnect it again |

### Settings

Settings resolve through three scopes. A user setting wins over a repository setting, which wins over an organization setting, which falls back to the shipped default. Every answer says where the value came from, so a screen can show whether it is set here, inherited, or default.

| Endpoint | Purpose |
|---|---|
| `GET /api/settings/catalogue` | What can be set, with types, ranges, and defaults |
| `GET /api/settings/{scope}/{scope_id}` | Current values and the source of each |
| `PUT /api/settings/{scope}/{scope_id}/{key}` | Set one value at this scope |
| `DELETE /api/settings/{scope}/{scope_id}/{key}` | Stop setting it here, and go back to inheriting |

`scope` is `user`, `repository`, or `organization`. `scope_id` is the user id, `owner/name`, or the organization login.

```bash
curl -X PUT https://your-instance/api/settings/repository/acme/api/review.reuse_days \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"value": 3}'
```

The catalogue is the whole list of what these endpoints accept. Anything else is answered with 404, so a key that only exists as an environment variable cannot be set this way:

| Key | Default | Scope |
|---|---|---|
| `review.reuse_days` | `7` | repository, organization |
| `initialization.candidate_limit` | `20` | repository, organization |
| `initialization.evidence_limit` | `20` | repository, organization |
| `initialization.evidence_character_limit` | `20000` | repository, organization |
| `initialization.investigation_limit` | `12` | repository, organization |

Everything else is configured through the environment. See [Configuration](configuration.md).

### Topology

`PUT /api/topology/entities`, `PUT /api/topology/relationships`, `POST /api/topology/search`, `POST /api/topology/traverse`, `POST /api/topology/infer`, and the two `DELETE` routes. See [Systems](systems.md).

### Code index

| Endpoint | Purpose |
|---|---|
| `GET /api/code/repositories` | Every repository registered on this machine |
| `GET /api/code/graph` | One whole scope, as `nodes` and `links`, for drawing |
| `GET /api/code/nodes` | A page of nodes, by label or by file |

No token, and no scope from the caller. See [Local index](local-index.md).

### Webhooks

| Endpoint | Purpose |
|---|---|
| `POST /api/prs/github-webhook` | GitHub App and repository webhook deliveries |
| `POST /api/prs/github-webhook-oauth` | Deliveries from repositories connected through GitHub OAuth |

### MCP

`/mcp/` serves the knowledge and context tools over Streamable HTTP when its authorization settings are configured. See [Knowledge and Context](context.md).
