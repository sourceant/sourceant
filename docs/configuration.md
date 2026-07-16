## Configuration

SourceAnt is configured through environment variables. Copy `.env.example` to `.env` and set the values for your environment.

### Required: LLM Provider

SourceAnt supports 100+ LLM providers through LiteLLM. Set your model and API key:

```env
LLM_MODEL=gemini/gemini-2.5-flash
LLM_TOKEN_LIMIT=1000000
```

| Provider | `LLM_MODEL` | API Key |
|---|---|---|
| Google Gemini | `gemini/gemini-2.5-flash` | `GEMINI_API_KEY` |
| Anthropic | `anthropic/claude-sonnet-4-5-20250929` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai/gpt-4o` | `OPENAI_API_KEY` |
| DeepSeek | `deepseek/deepseek-chat` | `DEEPSEEK_API_KEY` |

### Required: GitHub Integration

SourceAnt needs a GitHub App to receive events. Configure its credentials:

```env
GITHUB_APP_ID=your_github_app_id
GITHUB_APP_PRIVATE_KEY_PATH=/path/to/private-key.pem
GITHUB_SECRET=your_webhook_secret
```

See the [GitHub App setup guide](github-app.md) for instructions on creating and configuring your app.

### Optional: Webhook-Only Mode

If you are not using a GitHub App, configure a repository webhook directly:

```env
WEBHOOK_SECRET=your_webhook_secret
REQUIRE_WEBHOOK_SECRET=true
```

### Required: Database

A PostgreSQL database is required. SourceAnt manages its own schema via Alembic migrations.

### Advanced Options

The following settings control internal behavior and are safe to leave at defaults for most deployments.

#### Mode Settings

**Stateless Mode:** Process events without a database. Intended for development and testing.

```env
STATELESS_MODE=true
```

---

**Queue Mode:** Controls background job processing.

| Mode | Description |
|---|---|
| `redis` (default) | Persistent Redis queue. Requires a separate `rq` worker process. Recommended for production. |
| `redislite` | Self-contained file-based Redis queue. No separate Redis server needed. |
| `request` | Uses FastAPI BackgroundTasks. Simplest for development. Jobs are lost on server restart. |

```env
QUEUE_MODE=redis
```

---

**Log Driver:** Where logs are written.

| Value | Description |
|---|---|
| `console` (default) | Logs to stdout/stderr. Recommended for Docker and serverless. |
| `file` | Logs to `sourceant.log` in the root directory. |
| `syslog` | Sends logs to the system syslog daemon. |

```env
LOG_DRIVER=console
```

#### Full Reference

For the complete list of supported environment variables, refer to `.env.example` in the repository root.
