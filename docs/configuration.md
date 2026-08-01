## Configuration

SourceAnt reads its configuration from environment variables. Copy `.env.example` to `.env` and set what your deployment needs. Compose loads that file into the app container, so it has to exist before anything starts.

Settings that make sense per repository are set through the API instead, and override the environment. See [API](api.md#settings).

### LLM provider

Required. SourceAnt reaches its models through LiteLLM, so any of its [100+ providers](https://docs.litellm.ai/docs/providers) works. Set the model, and the key its provider reads:

```env
LLM_MODEL=gemini/gemini-2.5-flash
GEMINI_API_KEY=your_gemini_api_key
```

| Provider | `LLM_MODEL` | API key |
|---|---|---|
| Google Gemini | `gemini/gemini-2.5-flash` | `GEMINI_API_KEY` |
| Anthropic | `anthropic/claude-sonnet-4-5-20250929` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai/gpt-4o` | `OPENAI_API_KEY` |
| DeepSeek | `deepseek/deepseek-chat` | `DEEPSEEK_API_KEY` |

`LLM_TOKEN_LIMIT` (default `131072`) is the diff size that still fits a single-pass review. Raise it to match a model with a larger window; a diff above it is reviewed file by file instead.

### GitHub App

Required to post anything to GitHub. All three are needed, and the integration refuses to start without them:

```env
GITHUB_APP_ID=123456
GITHUB_APP_CLIENT_ID=Iv23liOAxxxxxM88Sqy97
GITHUB_APP_PRIVATE_KEY_PATH=/path/to/private-key.pem
```

`GITHUB_SECRET` is the webhook signing secret, and must be the same string you set on the webhook in GitHub:

```env
GITHUB_SECRET=your_webhook_secret
```

A delivery whose signature does not match the secret is rejected. Signatures are only checked when both the secret and the signature header are present, so leaving `GITHUB_SECRET` unset means deliveries are accepted unverified. Set it, on both sides, on any instance GitHub can reach.

See [GitHub App Setup](github-app.md) for creating the app.

### API tokens

Required for every `/api/` route other than the webhooks:

```env
JWT_SECRET=your_signing_secret
```

Tokens are HS256 JWTs signed with this secret. See [API](api.md#authentication).

### Database

Required unless `STATELESS_MODE=true`. PostgreSQL is the deployment target; SQLite works for local use.

```env
DATABASE_URL=postgresql://admin:admin@db:5432/sourceant
```

SourceAnt owns its schema through Alembic migrations, applied with `sourceant db upgrade head`.

### Queue

```env
QUEUE_MODE=redis
REDIS_HOST=redis
REDIS_PORT=6379
```

| Mode | Behaviour |
|---|---|
| `redis` (default) | Redis-backed queue. Needs a separate `rq` worker process. Use this in production |
| `redislite` | File-backed queue in-process. No Redis server needed |
| `request` | FastAPI background tasks. Simplest for development; queued work is lost on restart |

An invalid value fails at startup rather than silently falling back.

Redis also holds generated reviews so the same commit is not reviewed twice. That cache is best effort: when Redis is unavailable the review is generated again.

### Review behaviour

| Variable | Default | What it does |
|---|---|---|
| `REVIEW_DRAFT_PRS` | `false` | Review draft pull requests |
| `POSITIVE_SENTIMENT_THRESHOLD` | `0.3` | How positive a comment must read before it is treated as praise and dropped |
| `REVIEW_MISSING_EXISTING_CODE_POLICY` | `drop` | A suggestion that does not quote the code it changes: `drop`, `warn`, or `keep` |

See [Code Review](reviews.md).

### Repo manager

| Variable | Default | What it does |
|---|---|---|
| `REPO_MANAGER_ENABLED` | `false` | Master switch |
| `REPO_MANAGER_PR_TRIAGE` | `true` | Duplicate detection on pull requests |
| `REPO_MANAGER_ISSUE_TRIAGE` | `true` | Duplicate detection on issues |
| `REPO_MANAGER_AUTO_LABEL` | `true` | Labelling from the repository's own labels |

See [Repo Management](repo-management.md).

### Knowledge over HTTP

The MCP transport at `/mcp/` is off unless these are set, and setting only some of them is a startup error:

```env
MCP_HTTP_ISSUER_URL=https://issuer.example.com
MCP_HTTP_RESOURCE_URL=https://sourceant.example.com/mcp/
MCP_HTTP_AUDIENCE=sourceant-mcp
MCP_HTTP_REQUIRED_SCOPES=sourceant
```

See [Knowledge and Context](context.md).

### Modes and logging

```env
STATELESS_MODE=false
APP_ENV=production
DEBUG_MODE=false
LOG_DRIVER=console
LOG_FILE=sourceant.log
```

`STATELESS_MODE=true` runs without a database, for development and testing. Anything that depends on stored state, including review history and knowledge, does not survive the process.

| `LOG_DRIVER` | Where logs go |
|---|---|
| `console` (default) | stdout and stderr. Use this in containers |
| `file` | `LOG_FILE` in the working directory |
| `syslog` | The system syslog daemon |

`.env.example` in the repository root carries the full list.
