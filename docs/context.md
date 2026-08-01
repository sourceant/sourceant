## Knowledge and Context

The reason a piece of code looks the way it does is rarely in the code. It is in a decision someone made two years ago, a rule the team agreed once, a constraint from a customer. SourceAnt stores that alongside the structure of the system, and hands it back as a bounded pack when an agent or a person needs it.

This is what the review, triage, and systems surfaces all read from.

### Starting the knowledge server

The community MCP server runs over stdio and uses the database SourceAnt is already configured with, so knowledge survives a restart:

```bash
python -m src.mcp_server
```

Point your MCP client at that command, run from the repository directory, using whatever format the client documents for a local stdio server. Set `STATELESS_MODE=true` to keep knowledge in memory instead, which is useful for a throwaway session.

The running HTTP server can also serve MCP over Streamable HTTP at `/mcp/`. That transport stays off unless all of its authorization settings are present:

```env
MCP_HTTP_ISSUER_URL=https://issuer.example.com
MCP_HTTP_RESOURCE_URL=https://sourceant.example.com/mcp/
MCP_HTTP_AUDIENCE=sourceant-mcp
MCP_HTTP_REQUIRED_SCOPES=sourceant
JWT_SECRET=your_signing_secret
```

Setting some but not all of them is an error at startup rather than a server that quietly accepts anonymous callers. Over HTTP, knowledge is isolated by the authenticated principal: the server applies that boundary itself, so a client cannot ask for another principal's knowledge through tool arguments.

### Scopes

A scope is an open map of key-value pairs, not a fixed hierarchy. A personal project can use `{"project": "shop"}`. An integration can use `{"organization": "acme", "repository": "acme/billing"}`. Nothing in the core needs to know which keys you chose.

### Tools

| Tool | What it does |
|---|---|
| `put_knowledge` | Create or update a decision, rule, constraint, convention, or note, with a status and free-form properties |
| `put_knowledge_relationship` | Connect two knowledge items, for example `depends_on`, `supports`, `contradicts` |
| `search_knowledge` | Find knowledge by scope, id, kind, status, or properties |
| `put_topology_entity` | Record a part of the system |
| `put_topology_relationship` | Record how two parts relate |
| `traverse_topology` | Walk the system graph outward from seed entities |
| `get_context` | Combine all of the above into one bounded pack |

### Context packs

`get_context` takes a scope and the seeds you have, and returns only what you asked for:

| Argument | Source |
|---|---|
| `code_node_ids` | Code structure |
| `knowledge_ids` | Decisions, rules, conventions |
| `topology_entity_ids` | [Systems](systems.md) and their relationships |
| `contract_document_ids` | API contracts |
| `finding_states` | Review findings in the given states |

`depth` (1 to 3, default 2) and `limit` (1 to 50) bound the walk. The pack reports whether it was truncated, so a caller can tell a small answer from a clipped one.

Knowledge and topology are stored in SourceAnt's database. Code structure, contracts, and review findings are served by in-memory adapters in the community edition: they answer the same interfaces, and a plugin can replace any of them with a real index or store without the callers changing.

### Asking for it in practice

To an MCP-enabled coding agent, this looks like ordinary instruction:

```text
Remember that project shop uses signed webhook requests. Store it as an approved decision.

Connect the signed webhook decision to the rule that rejects unsigned requests.

Get the approved knowledge related to the signed webhook decision before changing its handler.
```
