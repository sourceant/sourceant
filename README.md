<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/sourceant-logo-dark.svg">
    <img src="docs/assets/sourceant-logo.svg" alt="SourceAnt" width="720">
  </picture>
</p>

<p align="center"><strong>Code knowledge management.</strong></p>

SourceAnt helps the agents and humans building your software remember how it works. Its open core models code structure, decisions, rules, system topology, API contracts, and review findings behind storage-neutral interfaces. MCP clients can manage and retrieve that context without requiring the hosted SourceAnt service.

Code review is one application of this shared knowledge layer. SourceAnt also supports coding agents, architecture exploration, contract analysis, repository automation, and custom engineering workflows.

## What the community edition provides

- **Knowledge management through MCP**: Create, update, search, connect, and retrieve scoped engineering knowledge.
- **Durable storage**: Keep community knowledge in SourceAnt's existing SQL database. PostgreSQL is used in the normal deployment, while the existing SQLite fallback remains available for local use.
- **Engineering context composition**: Combine code, knowledge, software topology, contracts, and active review findings into bounded context packs.
- **Replaceable adapters**: Connect structural indexers, graph databases, or custom repositories without changing the core domain.
- **Automated code reviews**: Use the same context interfaces for GitHub review and repository automation.
- **Model choice**: Use Gemini, Anthropic Claude, OpenAI, DeepSeek, Mistral, and [100+ providers through LiteLLM](https://docs.litellm.ai/docs/providers).
- **Plugin runtime**: Extend SourceAnt with new integrations and workflows.

## Community knowledge server

The included MCP server uses SourceAnt's configured database. Knowledge remains available after the MCP process restarts.

### Start it

```bash
git clone https://github.com/sourceant/sourceant.git
cd sourceant
python -m pip install -r requirements.txt
sourceant db upgrade head
python -m src.mcp_server
```

This stdio option is useful for a local MCP client. It follows the required `DATABASE_URL`, just like the SourceAnt HTTP server. Set `STATELESS_MODE=true` to use temporary in-memory knowledge instead.

Configure your MCP client to run `python -m src.mcp_server` from the repository directory. Use that client's documented format for a local stdio server.

### Use the SourceAnt HTTP server

The regular SourceAnt application can also serve MCP over Streamable HTTP at `/mcp/`. This transport is disabled unless all three authorization settings are present:

```env
MCP_HTTP_ISSUER_URL=https://issuer.example.com
MCP_HTTP_RESOURCE_URL=https://sourceant.example.com/mcp/
MCP_HTTP_AUDIENCE=sourceant-mcp
MCP_HTTP_REQUIRED_SCOPES=sourceant
```

`JWT_SECRET` remains the signing secret. Tokens must contain `sub`, `exp`, `iss`, `aud`, and a space-separated `scope` claim. Enabling MCP does not change the existing HTTP API or its authentication behavior.

HTTP knowledge is isolated by the authenticated principal and the requested scope. The server adds the principal boundary itself, so a client cannot select another principal through tool arguments.

### Manage knowledge

The community server exposes these MCP tools:

| Tool | Purpose |
|------|---------|
| `put_knowledge` | Create or update a decision, rule, constraint, convention, note, or another knowledge type. |
| `put_knowledge_relationship` | Connect knowledge with relationships such as `depends_on`, `supports`, or `contradicts`. |
| `search_knowledge` | Find knowledge by scope, identity, type, status, or properties. |
| `get_context` | Traverse related knowledge and combine it with other configured engineering context. |

Scopes are open key-value pairs. A personal project can use `{"project": "shop"}`. An integration can use repository, organization, customer, or another boundary without changing core types.

Example requests to an MCP-enabled coding agent:

```text
Remember that project shop uses signed webhook requests. Store it as an approved decision.

Connect the signed webhook decision to the rule that rejects unsigned requests.

Get the approved knowledge related to the signed webhook decision before changing its handler.
```

The SQL repository is the basic community implementation. Applications can inject another `KnowledgeRepository` and the MCP tools continue to use the same contract.

Knowledge scopes can identify any repository. SourceAnt core does not clone or structurally index arbitrary repositories by itself. Code-aware context requires a `CodeIndexReader` integration, while knowledge management works without one.

## Review automation setup

### Prerequisites
- Python 3.10+
- GitHub account with a repository for testing.
- LLM API key (supports any [LiteLLM-compatible provider](https://docs.litellm.ai/docs/providers): Gemini, Anthropic, DeepSeek, OpenAI, etc.).

### Installation
1. **Clone the Repository**:
   ```bash
   git clone https://github.com/sourceant/sourceant.git
   cd sourceant
   ```

2. **Initial project setup (docker-compose)**:
   ```bash
   docker compose up -d
   ```

3. **Install Dependencies**:
   ```bash
   docker compose exec app pip install -r requirements.txt
   ```

4. **Set Environment Variables**:
   Copy `.env.example` into `.env` file in the root directory and update the credentials accordingly:
   ```bash
   docker compose exec app cp .env.example .env
   ```
   #### `.env` file
   ```env
   GITHUB_WEBHOOK_SECRET=your_github_webhook_secret
   LLM_MODEL=gemini/gemini-2.5-flash
   LLM_TOKEN_LIMIT=1000000
   GEMINI_API_KEY=your_gemini_api_key
   ```
SourceAnt API should be live at http://localhost:8000

## SourceAnt Commands

The `sourceant` command provides the following subcommands for managing the application:

| Command               | Description                                      |
|-----------------------|--------------------------------------------------|
| `docker compose exec app sourceant db upgrade head`        | Set up database tables |
| `docker compose exec app sourceant db --help`                                 | See more database commands |


### Example Usage

- **Start the database**:
  ```bash
  sourceant db
  ```

- **Start the API server**:
  ```bash
  docker compose up -d
  ```

- **Run the Worker**:
  ```bash
  docker compose exec app rq worker --url redis://redis:6379
  ```

- **View logs**:
  ```bash
  docker compose logs
  ```

## Configuration
The application can be configured using environment variables. Key variables are documented in the `.env.example` file.

### LLM Model

SourceAnt uses [LiteLLM](https://docs.litellm.ai/docs/providers) to support 100+ LLM providers through a unified interface. Set the `LLM_MODEL` env var using the `provider/model` format:

| Provider | `LLM_MODEL` | API Key Env Var |
|----------|-------------|-----------------|
| Google Gemini | `gemini/gemini-2.5-flash` | `GEMINI_API_KEY` |
| Anthropic | `anthropic/claude-sonnet-4-5-20250929` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai/gpt-4o` | `OPENAI_API_KEY` |
| DeepSeek | `deepseek/deepseek-chat` | `DEEPSEEK_API_KEY` |

```bash
# Example: switch from Gemini to Anthropic
LLM_MODEL=anthropic/claude-sonnet-4-5-20250929
ANTHROPIC_API_KEY=sk-ant-...
```

See the full list of supported providers in the [LiteLLM docs](https://docs.litellm.ai/docs/providers).

### GitHub App Setup

Authentication is handled via a GitHub App, which provides secure, repository-level access. Your setup path depends on whether you are using the official cloud service or self-hosting the backend.

#### For Cloud Users

If you are using the official SourceAnt cloud service, simply install our official GitHub App:

- **[Install the SourceAnt GitHub App](https://github.com/apps/sourceant)**

The app will request the necessary permissions, and once installed on your repositories, it will automatically send events to our hosted backend. No further configuration is needed. Manage your repositories, knowledge, and reviews from the dashboard at [app.sourceant.ai](https://app.sourceant.ai).

#### For Self-Hosted Users

If you are running your own instance of SourceAnt (e.g., from this repository), you **must create your own GitHub App**. This is because the webhook URL must point to your own server.

1.  **Create a New GitHub App**:
    *   Navigate to your GitHub settings: **Developer settings** > **GitHub Apps** > **New GitHub App**.
    *   **Webhook URL**: Set this to the public URL of your backend, pointing to the webhook endpoint (e.g., `https://your-domain.com/api/github/webhooks`).
    *   **Webhook Secret**: Generate a secure secret and save it. You will need this for the `GITHUB_SECRET` environment variable.

2.  **Set Permissions**:
    Under the "Permissions" tab for your app, grant the following access:
    *   **Repository permissions** > **Contents**: `Read-only`
    *   **Repository permissions** > **Pull requests**: `Read & write`

3.  **Generate a Private Key**:
    *   At the bottom of your app's settings page, generate a new private key (`.pem` file).
    *   Save this file securely and note its path.

4.  **Configure Environment Variables**:
    Update your `.env` file with the credentials from the app you just created:
    *   `GITHUB_APP_ID`: The "App ID" from your app's settings page.
    *   `GITHUB_APP_PRIVATE_KEY_PATH`: The file path to the `.pem` private key you downloaded.
    *   `GITHUB_SECRET`: The webhook secret you created.

### Repo Management

SourceAnt includes a builtin repo manager plugin that automates PR/issue triage and labeling. It is **disabled by default**. Enable it with environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `REPO_MANAGER_ENABLED` | `false` | Master switch for the repo manager |
| `REPO_MANAGER_PR_TRIAGE` | `true` | Enable PR duplicate detection |
| `REPO_MANAGER_ISSUE_TRIAGE` | `true` | Enable issue duplicate detection |
| `REPO_MANAGER_AUTO_LABEL` | `true` | Enable auto-labeling |

Settings can also be configured per-repository using the Config model. See the [Repo Management docs](https://sourceant.ai/docs/repo-management) for details.

### Stateless Mode
For development, testing, or specific use cases where you want to process events without writing them to the database, you can enable stateless mode. In this mode, the application will not attempt to connect to or interact with any database, making it lighter and preventing data accumulation.

To enable stateless mode, set the following environment variable:
```bash
STATELESS_MODE=true
```

### Log Driver

The `LOG_DRIVER` environment variable controls where the application logs are sent. This is particularly useful in serverless environments where file-based logging is not practical.

-   **`console` (Default)**: Logs are sent to the console, intelligently routing to `stdout` for informational messages (`INFO`, `DEBUG`) and `stderr` for warnings and errors (`WARNING`, `ERROR`, `CRITICAL`). This is the recommended setting for serverless and containerized environments like Cloud Run and Docker.
-   **`file`**: Logs are written to `sourceant.log` in the root directory. This is useful for traditional deployments where you have access to the file system.
-   **`syslog`**: Logs are sent to the system's syslog daemon. This is suitable for environments where you want to centralize logs from multiple services into a single, system-level logging solution.

### Queue Mode

The application supports different backend modes for processing background jobs, controlled by the `QUEUE_MODE` environment variable.

-   **`redis` (Default)**: This is the recommended mode for production. It uses a persistent Redis queue (`rq`) to handle background tasks. This requires a separate `rq` worker process to be running:
    ```bash
    docker compose exec app rq worker --url redis://redis:6379
    ```

-   **`redislite`**: A self-contained, file-based Redis queue. This mode is ideal for local development or testing as it provides the full functionality of a Redis queue without needing to run a separate Redis server. The Redis database file (`redislite.db`) will be created in the project root.

-   **`request`**: This mode uses FastAPI's `BackgroundTasks` to process jobs in the same process as the web request, after the response has been sent. It's the simplest mode for development as it requires no external worker or Redis, but it is not suitable for production as jobs are lost if the server restarts.

## Docker Images

### Base Image
The base SourceAnt image is built automatically on merge to `main` and pushed to `ghcr.io/sourceant/sourceant`. You can also trigger a build manually via **Actions → Build Image → Run workflow**.

### Enterprise Image
The enterprise image includes additional plugins and is built via manual dispatch.

**Setup:**
1. Add a repository secret named `PLUGIN_REPO_TOKEN` containing a PAT with access to clone private plugin repositories.
2. Add a repository variable `ENTERPRISE_PLUGINS` with your plugin configuration:
   ```json
   [{"name": "analytics", "repo": "sourceant/analytics"}]
   ```

**Usage:**
- Go to **Actions → Build Enterprise Image → Run workflow**.
- Leave the `plugins` input empty to use the `ENTERPRISE_PLUGINS` variable, or override with a custom JSON array.
- The image is pushed to `ghcr.io/sourceant/sourceant-enterprise:latest`.

### Local Builds
```bash
make prod-build                              # Build with default tag (:latest)
make prod-build IMAGE_TAG=v1.0.0             # Build with custom tag
make prod-push                               # Push to GHCR
```

## Setting Up GitHub Webhook
1. Go to your GitHub repository.
2. Navigate to **Settings > Webhooks > Add Webhook**.
3. Set the **Payload URL** to your server's `/webhook` endpoint (e.g., `https://your-server.com/webhook`).
4. Set the **Content type** to `application/json`.
5. Add the `GITHUB_WEBHOOK_SECRET` to the **Secret** field.
6. Select **Let me select individual events** and choose **Pull requests** and **Issues**.
7. Save the webhook.

## Contributing
We welcome contributions! Here's how you can help:
1. Fork the repository.
2. Create a new branch: `git checkout -b feature/your-feature`.
3. Make your changes and commit them: `git commit -m 'Add some feature'`.
4. Push to the branch: `git push origin feature/your-feature`.
5. Submit a pull request.

## License
This project is licensed under the MIT License. See the [LICENSE](LICENSE.md) file for details.

## Contact
Have questions or suggestions? Reach out to us:
- **Email**: hello@sourceant.ai
- **GitHub Issues**: [Open an issue](https://github.com/sourceant/sourceant/issues)

## Contributors
Thanks to these amazing people who have contributed to this project:

<a href="https://github.com/sourceant/sourceant/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=sourceant/sourceant" />
</a>

Maintained by [WhileSmart](https://whilesmart.com).
