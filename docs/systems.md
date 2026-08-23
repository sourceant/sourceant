## Systems

A repository is not a system. The thing that breaks in production is usually several repositories, a queue, and a database that nobody drew on the same page. Systems is where SourceAnt records those parts and how they depend on each other, so a change can be read against what sits downstream of it.

The graph, the endpoints, and the dependency inference below are all in the open core.

### Scope

Every entity and relationship is stored under a scope, and a read only ever sees its own scope. To the core a scope is an opaque set of key-value pairs: it keeps one graph away from another without the core knowing, or defining, what the keys mean. There is no notion of a team, a workspace, or an organization here to own a graph.

Where the scope comes from depends on how you arrive.

| Transport | Scope |
|---|---|
| MCP | The authenticated principal. This is the path that needs nothing beyond the core |
| HTTP | A `workspace_id` claim on the token. A token without one is refused |

The HTTP routes read that claim; they do not create or validate whatever it names. In SourceAnt Cloud the claim comes from your workspace there. Self-hosting, it is yours to mint: any stable string works, and two different values simply keep two graphs apart.

### Entities and relationships

An entity is one part of the system: a service, a repository, a datastore, a queue, whatever your architecture calls a part. `kind` is yours to choose; SourceAnt does not impose a vocabulary.

| Field | Meaning |
|---|---|
| `id` | Stable identifier you choose; writing the same id again replaces it |
| `kind` | What sort of part it is |
| `status` | Where it stands, for example `proposed`, `approved`, `retired` |
| `confidence` | 0 to 1. A human-declared part is 1; an inferred one is lower |
| `stale` | Set when the part is believed out of date rather than removed |
| `evidence` | What the claim rests on: the file, the manifest, the revision |

A relationship joins two entities with a `type` (`depends_on`, `extends`, or your own) and carries the same status, confidence, staleness, and evidence.

```bash
curl -X PUT https://your-instance/api/topology/entities \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"id": "billing-api", "kind": "service", "status": "approved"}'
```

### Inferring dependencies

`POST /api/topology/infer` proposes relationships from the manifests repositories already publish. Give it the repositories to read and the entity each one stands for:

```json
{"assets": [{"entity_id": "billing-api", "repository": "acme/billing-api"},
            {"entity_id": "shared-auth", "repository": "acme/shared-auth"}]}
```

`package.json`, `composer.json`, `go.mod`, `requirements.txt`, and `pyproject.toml` are read through the GitHub API. Nothing is cloned and nothing is executed. Names are compared by the rules of the ecosystem that issued them, so `friendly.bard` and `Friendly_Bard` are one PyPI project rather than two.

Every proposal comes back pending, carrying the file it came from, and is recorded in the graph so it can be reviewed in place. Send `"persist": false` for a preview that leaves the graph untouched. Approving a proposal is a person's decision; nothing here approves itself.

### Reading the graph

`POST /api/topology/search` lists entities with their relationships, filtered by ids, kinds, statuses, properties, and minimum confidence. Up to 100 per page.

`POST /api/topology/traverse` walks outward from up to 50 seed entities:

| Field | Default | Range |
|---|---|---|
| `depth` | `2` | 1 to 3 |
| `direction` | `both` | `outbound`, `inbound`, `both` |
| `minimum_confidence` | `0.0` | 0 to 1 |
| `include_stale` | `false` | |
| `entity_limit` | `50` | up to 50 |
| `relationship_limit` | `100` | up to 100 |

The bounds are the point: a traversal answers with a subgraph small enough to put in front of a model or a person, and says when it had to stop rather than quietly returning half a graph.

### Removing

`DELETE /api/topology/entities/{id}` removes an entity and every relationship attached to it. `DELETE /api/topology/relationships/{id}` removes one relationship and leaves both ends in place.

### Where it is used

Topology is one of the sources a context pack draws on, so an agent asking what a change touches gets the neighbouring systems alongside the code and the decisions. See [Knowledge and Context](context.md).
