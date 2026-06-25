## Configuration

SourceAnt is configured through environment variables. Copy `.env.example` to `.env` and adjust the values.

### LLM Configuration

SourceAnt supports 100+ LLM providers through LiteLLM. Set the model using the `provider/model` format:

```env
LLM_MODEL=gemini/gemini-2.5-flash
LLM_TOKEN_LIMIT=1000000
```

Set the corresponding API key for your provider:

| Provider | `LLM_MODEL` | API Key |
|---|---|---|
| Google Gemini | `gemini/gemini-2.5-flash` | `GEMINI_API_KEY` |
| Anthropic | `anthropic/claude-sonnet-4-5-20250929` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai/gpt-4o` | `OPENAI_API_KEY` |
| DeepSeek | `deepseek/deepseek-chat` | `DEEPSEEK_API_KEY` |

### GitHub Integration

```env
GITHUB_WEBHOOK_SECRET=your_webhook_secret
GITHUB_APP_ID=your_github_app_id
GITHUB_APP_PRIVATE_KEY_PATH=/path/to/private-key.pem
GITHUB_SECRET=your_github_secret
```

### Mode Settings

**Stateless Mode:** Process events without a database. Useful for development and testing.

```env
STATELESS_MODE=true
```

**Queue Mode:** Controls how background jobs are processed.

| Mode | Description |
|---|---|
| `redis` (default) | Persistent Redis queue. Requires a separate `rq` worker process. Recommended for production. |
| `redislite` | Self-contained file-based Redis queue. No separate Redis server needed. |
| `request` | Uses FastAPI BackgroundTasks. Simplest for development. Jobs are lost on server restart. |

```env
QUEUE_MODE=redis
```

**Log Driver:**

| Value | Description |
|---|---|
| `console` (default) | Logs to stdout/stderr. Recommended for Docker and serverless. |
| `file` | Logs to `sourceant.log` in the root directory. |
| `syslog` | Sends logs to the system syslog daemon. |

```env
LOG_DRIVER=console
```
