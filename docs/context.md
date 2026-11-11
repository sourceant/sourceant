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
| `search_knowledge` | Find knowledge by scope, id, type, status, or properties |
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

Code structure, knowledge, and topology are stored in SourceAnt's database. Contracts and review findings are served by in-memory adapters in the community edition: they answer the same interfaces, and a plugin can replace any of them with a durable store without the callers changing.

Code structure gets there through [`sourceant index`](local-index.md), or from a SCIP index another tool produced.

### Asking for it in practice

To an MCP-enabled coding agent, this looks like ordinary instruction:

```text
Remember that project shop uses signed webhook requests. Store it as an approved decision.

Connect the signed webhook decision to the rule that rejects unsigned requests.

Get the approved knowledge related to the signed webhook decision before changing its handler.
```

### Knowledge category compatibility

Knowledge objects use `type` for their category. HTTP knowledge responses include
`type` and the deprecated `kind` with identical values for one release. Writes
accept either field and prefer `type` when both are present. Knowledge searches
accept `types`, with `kinds` retained as a deprecated alias.

The MCP `put_knowledge`, `capture_context`, and `update_context` tools accept
`type`, with `kind` retained as a deprecated argument. Their results and knowledge
search results use `type`. MCP knowledge searches accept `types` and the deprecated
`kinds` alias, preferring `types`.

Upgrade the database before starting the updated server. The migration preserves
knowledge while renaming its category column; downgrading restores the old name.

### Event memory

`Memory` has three operations and one configuration flag:

```python
configured: bool
record(scope, events: list[Event]) -> list[Event]
events(scope, since=None, until=None, after=None) -> EventPage
recall(scope, text, limit=10) -> list[Event]
```

`Event` is the same type for writing and reading. It contains a stable source id,
type, source, timestamps, text, title, explanation, actors, subjects, relationships,
attributes and optional valence and importance. `record` returns stored views with
engine ids. Read scores and `outside_window` are ignored on write. Actor and
subject views include their ids; subject parents reference ids in the same event.
`Source` contains system, installation and container ids plus source URLs and an
optional id namespace. Container names remain available on the root place subject.

`record` validates observation text, supplied timestamps, actor types, ratings
and subject parents before writing. Timestamps must include a timezone, valence
must be between -2 and 2, and importance must be between 1 and 5. Every subject
parent must be included among the event's subjects.

`events` returns an `EventPage` containing time-ordered events and a `next` cursor.
Missing parent events are included with `outside_window: true`. `recall` returns
events directly, with search scores. `configured` belongs to the implementation,
not individual events or pages. With both `MEMORY_ENGINE_URL` and
`MEMORY_ENGINE_KEY`, the default is `TrailpadMemory`. Without them, `DisabledMemory`
returns empty results and ignores writes. `InMemoryMemory` is the test double.

The MCP `remember` and `recall` tools retain their existing arguments and response
shapes. The caller builds observation events, actors and path subjects before
recording. Actors are agents when the scope or token marks an agent, otherwise
people. The MCP boundary validates observation text, timestamps, ratings and recall
queries even when memory is disabled or an observation uses a custom type.
Knowledge recording builds a new event for each write attempt. Repeated identical
writes attempt recording again, so a swallowed engine failure does not suppress
the next attempt.

The adapter owns engine records, identifier mapping and bucket binding. Source
integrations supply events without importing it. Event, actor and place IDs
include the scope, and path hashes include the scope bucket.
Source id namespaces preserve those identities without adapter branches for
specific sources. A place whose source id equals its source container is the root
place; other place ids are local suffixes. GitHub path ids use a separate `path:`
namespace. Source ids are always mapped, regardless of their spelling. Memory
imports neither integrations nor knowledge.

Each scope writes and reads exactly one engine bucket using its application
scope binding. Sources remain provenance and never select buckets. Reads need
neither a binding table nor a source resolver. Pull request head lookup reads only
pull request deliveries.

Record IDs are unique within an engine workspace and include every application
scope, including the empty scope. Captures are identified by engine workspace,
bucket, source system, installation and native ID. The same delivery can be
recorded in two scope buckets; replaying it within one bucket does not duplicate
it. Engine sources keep their real native IDs. Public sources use the event's
engine source and the root entity's source container ID and URL. History in
older source buckets must be replayed into scope buckets before it can be read.

GitHub ingestion retains the eight most recently used adapters. Each retains at
most 4,096 delivery ids and 1,024 branch heads, evicting the least recently used
entries. Delivery deduplication includes workspace and repository, so one
delivery can be recorded in multiple scopes. Stored pull requests restore branch
context when replaying pushes.

Application writers use `Source.for_scope(scope)`. Its identity includes every
scope dimension except actor, principal, user and agent. Branch and environment
scopes stay isolated while principals share events within the same application
scope. Bucket binding enforces the same identity.

Event timestamps are timezone-aware `datetime` values. Integrations parse external
timestamps before recording. Read views retain private adapter provenance so
writing a read view back preserves stored identities and timestamp bytes without
adding engine-specific fields to the public models.
