## Local index

SourceAnt indexes repositories on your own machine, into one graph that covers all of them. There is no account, no service to stand up, and nothing written into the repositories themselves.

### Where it keeps things

With no `DATABASE_URL` set, SourceAnt uses a SQLite database in your user data directory:

| Platform | Location |
|---|---|
| Linux | `$XDG_DATA_HOME/sourceant`, or `~/.local/share/sourceant` |
| macOS | `~/.local/share/sourceant` |

`SOURCEANT_HOME` overrides it. One location per user, not one per checkout, so every repository you register goes into the same store, each under its own scope.

Set `DATABASE_URL` and it uses that instead, which is what a deployment does.

### Registering repositories

```bash
./sourceant repo add ~/code/billing
./sourceant repo add ~/code/shipping
./sourceant repo list
./sourceant repo remove ~/code/shipping
```

Each repository is stored under a scope of `{"repository": "<name>"}`. The name comes from the `origin` remote when there is one, so `git@github.com:acme/billing.git` is stored as `acme/billing`. Pass `--name` to choose it yourself.

Removing a repository stops it being indexed. Its graph is left alone.

### Indexing

```bash
./sourceant index ~/code/billing        # read it in full
./sourceant index ~/code/billing --update   # reparse only what changed
./sourceant index --all --update            # every registered repository
```

Run with no path and it uses the current directory, registering it if it is new.

A full run replaces whatever was stored for that repository. An update hashes every file, reparses the ones whose contents changed, and drops the ones that are gone. Each run reports what it did:

```
acme/billing  indexed 254  unchanged 0  removed 0  skipped 16
```

`skipped` counts files with no grammar available, files too large to parse, and anything that is not text.

### What is read

Inside a git repository, SourceAnt reads exactly the files git would list: tracked files plus untracked ones that `.gitignore` does not exclude. Outside one, it walks the directory and skips the usual dependency and build directories.

Repositories can exclude more through the `code_index.excluded_paths` setting. See [Configuration](configuration.md).

### Languages

Grammars ship for C, C++, C#, Go, Java, JavaScript, Kotlin, PHP, Python, Ruby, Rust, TypeScript, and TSX, along with common markup.

For anything else, run one of the [SCIP indexers](https://github.com/sourcegraph/scip) and load its output into the same graph:

```bash
scip-typescript index
./sourceant index . --scip index.scip.json --revision "$(git rev-parse HEAD)"
```

### Using it

Point an MCP client at the knowledge server and it reads the graph you just built:

```bash
python -m src.mcp_server
```

`search_code` finds files and symbols, `trace_code` walks the neighbourhood around one, and `get_context` combines code structure with decisions and topology into a single bounded pack. See [Knowledge and context](context.md).

The HTTP server serves the same thing, plus the REST API:

```bash
./sourceant serve
```

It starts with nothing configured. A signing secret is made once and kept in the data directory beside the database, so tokens survive a restart.

### Reading the index over HTTP

Three routes serve what a client needs to draw the index. They carry no token, and take no scope: a repository is readable only once `repo add` has registered it on this machine, so a deployment nobody registered anything on serves nothing here.

```bash
curl localhost:8000/api/code/repositories
curl 'localhost:8000/api/code/graph?repository=acme/billing'
curl 'localhost:8000/api/code/nodes?repository=acme/billing&file_path=app/charge.py'
```

`graph` returns a whole scope at once, as `nodes` and `links`, capped at five thousand nodes and reporting `truncated` when it hit the cap. Each node carries `labels` for what it is and `kind` for the language or symbol type, which is the difference between a Python file and a Python function. `path_prefix` narrows it to a directory and `include_tests` puts the test suite back in.

`nodes` pages what the index can filter without reading the scope: label and file path. A substring search over names is not offered, because answering one would read every node.

`serve` binds to `127.0.0.1` by default. These routes are for the machine the index is on; binding it wider publishes them.

### What a local index is not

This applies to code structure only.

A local index describes your working tree, which moves with every edit, so it is filed under the repository alone. A review reads code at the commit it is reviewing, filed under that repository and revision together. The two do not meet, and that is deliberate: a review that read your working tree could cite a symbol from work you have not committed.

Decisions and requirements are different. They belong to the repository rather than to a commit, so they are filed with no revision and every later review reads them. Recording a decision once is enough.

Connecting a local code graph to a hosted one is an explicit step, not something that happens because both wrote to the same database.
